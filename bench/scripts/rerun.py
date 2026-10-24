#!/usr/bin/env python3
"""
rerun -- re-measure the paper's benchmark, the way the recorded results were
measured: every tool on one instance before moving on to the next, so that all
tools see the same machine state.

    python3 scripts/rerun.py --plan             # what would run, and how long it took us
    python3 scripts/rerun.py                    # the subset (about 1/10, 4.5 h on our laptop)
    python3 scripts/rerun.py --full             # all 987 instances (~45 h)
    python3 scripts/rerun.py --instances arbiter2__e09 TorcsGearing__e02
    python3 scripts/rerun.py --only msr,mtsr-corp
    python3 scripts/rerun.py --corpus my_folder   # your own instances (see below)

Run from bench/ with the system python3; each tool is started under the
interpreter it needs (bench_lib: SPOT_PY, EXPLAIN_PY). Results go to
bench/rerun/ (--out to change) in the same layout as bench/results/, so

    python3 scripts/compare_runs.py               # instance by instance against the recorded rows
    ~/micromamba/envs/spot/bin/python3 paper/make_tables.py --src rerun --subset

work on it directly. The shipped results are never written to.

YOUR OWN INSTANCES
------------------
--corpus DIR runs the same comparison on every input file in DIR (the format
of docs/TOOL.md), with --timeout seconds per tool (default 120). Instances
go through the paper's gate first (scripts/pi_gate.py: the trace is a run of
the system and the effect holds on it); the others are skipped (--no-gate to
keep them). Results go to bench/rerun/<DIR's name>/, in the layout of
bench/results/ip3, and the end prints how many instances each tool answered.

Stoppable and resumable: Ctrl-C at any point, then the same command again.
Every driver appends one row per instance to its CSV and skips the instances
it already has, so a stopped run loses at most the instance in flight.

PER INSTANCE, IN THIS ORDER (as for the recorded runs)
------------------------------------------------------
    msr         MSR with AutoHyper, then with HyperQB          msr_bench.py
    explainhyper  ExplainHyper, the tool of [11]               explainhyper_bench.py
    mtsr-corp   the MTSR, then CORP                            mtsr_corp_bench.py
    cats-check  the MTSR --check and CATS --check on the       mtsr_cats_check_bench.py
                same random candidate (|IP| <= 3 only)
Timeout: 120 s per tool per instance for |IP| <= 3, 300 s for |IP| = 4.

After the corpora, two fixed sets that do not depend on the corpus:
    cats-sketch  MTSR vs CATS sketch, CATS's X^k error family  mtsr_cats_sketch_bench.py
    handcrafted  CORP's arbiters (Table 6) and CATS's          paper/corp_curated.py
                 hand-crafted instances (Table 4)              paper/cats_handcrafted.py

THE SUBSET
----------
Every 10th instance of each |IP| class, in name order (83 of 823 with
|IP| <= 3, 17 of 164 with |IP| = 4), plus all of cats-sketch and handcrafted. Systematic
rather than random, so it spreads over the systems and is the same for
everyone. --plan prints how long these instances took on the machine of the
paper; budget more on a slower machine or in a VM.
"""
import argparse
import csv
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
# where re-runs go: bench/rerun, or $RERUN_OUT (the Docker image sets it to
# /shared/rerun, so a re-run in the container lands on the host)
RERUN = os.environ.get("RERUN_OUT") or os.path.join(BENCH, "rerun")
REPO = os.path.dirname(BENCH)
sys.path.insert(0, HERE)
import bench_lib as BL  # noqa: E402

CORPORA = (("ip3", os.path.join(BENCH, "CATS_RL_ip3"), 120),
           ("ip4", os.path.join(BENCH, "CATS_RL_ip4"), 300))
RECORDED = os.path.join(BENCH, "results")
GROUPS = ("msr", "explainhyper", "mtsr-corp", "cats-check", "cats-sketch", "handcrafted")
OLD_NAMES = {"eh": "explainhyper", "sketch": "cats-sketch", "curated": "handcrafted"}
STRIDE = 10


# --------------------------------------------------------------------------- #
def hyperqb_bin():
    for p in (os.environ.get("HYPERQB_BIN"),
              os.path.join(REPO, "external_tools", "HyperQB", "target", "release", "HyperRUSTY"),
              os.path.join(REPO, "external_tools", "HyperQB", "HyperRUSTY")):
        if p and os.access(p, os.X_OK):
            return p
    return None


def available():
    """tool -> None if usable, else why not."""
    why = {}
    if not os.access(os.path.join(REPO, "external_tools", "AutoHyper", "app", "AutoHyper"), os.X_OK) \
            and not os.path.exists(os.path.join(REPO, "external_tools", "AutoHyper", "app", "AutoHyper.dll")):
        why["autohyper"] = "AutoHyper not found"
    if not hyperqb_bin():
        why["hyperqb"] = "no HyperQB build (install.sh builds it; or set HYPERQB_BIN)"
    if not os.path.exists(BL.SPOT_PY):
        why["spot"] = f"no spot environment at {BL.SPOT_PY} (set SPOT_ENV_PY)"
    if not os.path.exists(BL.EXPLAIN_PY) or not os.path.isdir(os.path.join(BENCH, "tools", "ExplainHyper")):
        why["explainhyper"] = "ExplainHyper or its environment missing (tools/fetch.sh, install.sh --baselines)"
    if not os.path.exists(os.path.join(BENCH, "tools", "CORP", "corp.py")):
        why["corp"] = "CORP missing (tools/fetch.sh corp)"
    if not os.path.exists(os.path.join(BENCH, "tools", "CATS", "app", "CATS")):
        why["cats"] = "CATS missing (tools/fetch.sh cats)"
    return why


def selection(full, names, corpora=CORPORA):
    """[(tag, path, timeout)] in run order."""
    out = []
    for tag, corpus, t in corpora:
        by_ip = {}
        for f in sorted(os.listdir(corpus)):
            if not f.endswith(".txt"):
                continue
            if names:
                if f[:-4] in names or f in names:
                    out.append((tag, os.path.join(corpus, f), t))
                continue
            ip = int(next(l for l in open(os.path.join(corpus, f))
                          if "inputs=" in l).split("inputs=")[1].split(",")[0]) \
                if tag == "ip3" else 4
            by_ip.setdefault(ip, []).append(f)
        for ip in sorted(by_ip):
            fs = by_ip[ip] if full else by_ip[ip][::STRIDE]
            out += [(tag, os.path.join(corpus, f), t) for f in fs]
    return out


# --------------------------------------------------------------------------- #
def recorded_seconds(sel, groups):
    """How long the selected instances took when the paper's results were
    measured (wall clock, all selected tools). A TIMEOUT counts its full time."""
    cols = {"msr": [("MSR/msr_autohyper.csv", "wall_s"), ("MSR/msr_hyperqb.csv", "wall_s")],
            "explainhyper": [("MSR/explainhyper.csv", "wall_s")],
            "mtsr-corp": [("MTSR_CORP/mtsr.csv", "mtsr_s"), ("MTSR_CORP/corp.csv", "corp_s")],
            "cats-check": [("MTSR_CATS_check/mtsr.csv", "mtsr_s"), ("MTSR_CATS_check/cats_check.csv", "cats_s")]}
    cache, tot = {}, 0.0
    for tag, path, _ in sel:
        name = os.path.basename(path)[:-4]
        for g in groups:
            for rel, col in cols.get(g, []):
                key = (tag, rel)
                if key not in cache:
                    p = os.path.join(RECORDED, tag, rel)
                    cache[key] = ({r["instance"].removesuffix(".txt"): r for r in csv.DictReader(open(p))}
                                  if os.path.exists(p) else {})
                try:
                    tot += float(cache[key].get(name, {}).get(col) or 0)
                except ValueError:
                    pass
    return tot


def summary(res, secs):
    """--corpus DIR: how many instances each tool answered, by status."""
    took = f"{secs / 60:.0f} min" if secs < 3600 else f"{secs / 3600:.1f} h"
    print(f"\ndone in {took}.  Results: {res}/  (one CSV per tool)\n")
    for rel in ("MSR/msr_autohyper.csv", "MSR/msr_hyperqb.csv", "MSR/explainhyper.csv",
                "MTSR_CORP/mtsr.csv", "MTSR_CORP/corp.csv",
                "MTSR_CATS_check/mtsr.csv", "MTSR_CATS_check/cats_check.csv"):
        p = os.path.join(res, rel)
        if not os.path.exists(p):
            continue
        rows = list(csv.DictReader(open(p, newline="")))
        col = next((c for c in (rows[0] if rows else {}) if c.endswith("status")), None)
        counts = {}
        for r in rows:
            counts[r.get(col, "?")] = counts.get(r.get(col, "?"), 0) + 1
        print(f"  {rel:32s} " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


# --------------------------------------------------------------------------- #
def run(label, cmd, cwd, log):
    t0 = time.monotonic()
    with open(log, "a") as lf:
        lf.write(f"\n== {label}  {time.strftime('%F %T')}\n$ {' '.join(cmd)}\n")
        lf.flush()
        rc = subprocess.call(cmd, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT)
    if rc != 0:
        print(f"      [{label}] exit {rc} -- see {log}")
    return time.monotonic() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="all instances, not the subset")
    ap.add_argument("--instances", nargs="+", metavar="NAME", help="only these instances")
    ap.add_argument("--only", default=",".join(GROUPS), help="comma-separated groups (default: all)")
    ap.add_argument("--corpus", default="both", metavar="{ip3,ip4,both,DIR}",
                    help="one of the paper's corpora, or a folder of your own instances")
    ap.add_argument("--timeout", type=int, default=120, help="per tool, for --corpus DIR (default 120 s)")
    ap.add_argument("--no-gate", action="store_true", help="--corpus DIR: do not skip instances that fail the gate")
    ap.add_argument("--out", default=RERUN)
    ap.add_argument("--plan", action="store_true", help="print the plan and exit")
    a = ap.parse_args()
    groups = [OLD_NAMES.get(g.strip(), g.strip()) for g in a.only.split(",") if g.strip()]
    for g in groups:
        if g not in GROUPS:
            sys.exit(f"unknown group {g}; groups: {', '.join(GROUPS)}")
    out = os.path.abspath(a.out)
    own = a.corpus not in ("ip3", "ip4", "both")
    if own:
        folder = os.path.abspath(a.corpus)
        if not os.path.isdir(folder):
            sys.exit(f"--corpus: {a.corpus} is neither ip3, ip4, both nor a folder")
        tag = os.path.basename(folder)
        sel = selection(True, set(a.instances or []), ((tag, folder, a.timeout),))
        groups = [g for g in groups if g not in ("cats-sketch", "handcrafted")]
    else:
        sel = selection(a.full, set(a.instances or []))
        if a.corpus != "both":
            sel = [s for s in sel if s[0] == a.corpus]
    why = available()

    if own:
        per = sum({"msr": 2, "explainhyper": 1, "mtsr-corp": 2, "cats-check": 2}.get(g, 0) for g in groups)
        print(f"instances: {len(sel)} in {folder} ({a.timeout} s per tool); groups: {', '.join(groups)}")
        print(f"at most {len(sel) * per * a.timeout / 3600:.1f} h (if every tool ran out of time)")
    else:
        n3, n4 = sum(s[0] == "ip3" for s in sel), sum(s[0] == "ip4" for s in sel)
        est = recorded_seconds(sel, groups)
        if not a.instances:          # the fixed sets: ~0.3 h of cats-sketch, ~10 min handcrafted
            est += 0.3 * 3600 * ("cats-sketch" in groups) + 600 * ("handcrafted" in groups)
        print(f"instances: {n3} with |IP| <= 3 (120 s), {n4} with |IP| = 4 (300 s); groups: {', '.join(groups)}")
        print(f"on the machine of the paper this took {est / 3600:.1f} h (expect more in a VM)")
    for k, v in why.items():
        print(f"  not available: {k}: {v}")
    print(f"results: {os.path.join(out, tag) if own else out}")
    if a.plan:
        return
    if "autohyper" in why or "spot" in why:
        sys.exit("AutoHyper and spot are required; run install.sh first.")

    os.makedirs(out, exist_ok=True)
    if own and not a.no_gate:
        gate = os.path.join(out, tag)
        os.makedirs(gate, exist_ok=True)
        subprocess.call([BL.SPOT_PY, os.path.join(HERE, "pi_gate.py"), *[p for _, p, _ in sel], "--quiet",
                         "-o", os.path.join(gate, "gate_ok.txt"), "--csv", os.path.join(gate, "gate.csv")],
                        stdout=subprocess.DEVNULL)
        ok = {os.path.abspath(l.strip()) for l in open(os.path.join(gate, "gate_ok.txt")) if l.strip()}
        print(f"gate: {sum(os.path.abspath(p) in ok for _, p, _ in sel)} of {len(sel)} instances pass "
              f"(the others are skipped; why: {os.path.join(gate, 'gate.csv')})")
        sel = [x for x in sel if os.path.abspath(x[1]) in ok]
    with open(os.path.join(out, "instances.txt"), "a") as f:
        f.write("".join(f"{t}/{os.path.basename(p)}\n" for t, p, _ in sel))
    work = os.path.join(out, ".work")
    log = os.path.join(out, "rerun.log")
    py, spy, epy = sys.executable, BL.SPOT_PY, BL.EXPLAIN_PY
    S = lambda f: os.path.join(HERE, f)
    started = time.monotonic()

    for i, (tag, path, t) in enumerate(sel, 1):
        name = os.path.basename(path)
        res = os.path.join(out, tag)
        one = os.path.join(work, "one")
        shutil.rmtree(one, ignore_errors=True)
        os.makedirs(one)
        shutil.copy(path, one)
        mine = os.path.join(one, name)
        for d in ("MSR", "MTSR_CORP", "MTSR_CATS_check", "cwd_autohyper", "cwd_hyperqb"):
            os.makedirs(os.path.join(res, d), exist_ok=True)
        el = (time.monotonic() - started) / 3600
        print(f"[{i}/{len(sel)}] {tag}/{name}   ({el:.1f} h so far)", flush=True)
        if "msr" in groups:
            run("msr autohyper", [py, S("msr_bench.py"), "--model", "autohyper", "--corpus", one,
                                  "--no-gate", "--timeout", str(t), "--out",
                                  os.path.join(res, "MSR", "msr_autohyper.csv")],
                os.path.join(res, "cwd_autohyper"), log)
            if "hyperqb" not in why:
                run("msr hyperqb", [py, S("msr_bench.py"), "--model", "hyperqb", "--corpus", one,
                                    "--no-gate", "--timeout", str(t), "--out",
                                    os.path.join(res, "MSR", "msr_hyperqb.csv")],
                    os.path.join(res, "cwd_hyperqb"), log)
        if "explainhyper" in groups and "explainhyper" not in why:
            run("explainhyper", [epy, S("explainhyper_bench.py"), "--corpus", one, "--no-gate",
                                 "--timeout", str(t), "--out", os.path.join(res, "MSR", "explainhyper.csv")],
                HERE, log)
        if "mtsr-corp" in groups and "corp" not in why:
            run("mtsr+corp", [spy, S("mtsr_corp_bench.py"), mine, "--no-gate", "--timeout", str(t),
                              "-o", os.path.join(res, "MTSR_CORP")], HERE, log)
        if "cats-check" in groups and tag != "ip4" and "cats" not in why:
            run("cats-check", [spy, S("mtsr_cats_check_bench.py"), mine, "--no-gate",
                               "--timeout", str(t), "-o", os.path.join(res, "MTSR_CATS_check")],
                os.path.join(res, "MTSR_CATS_check"), log)

    if "cats-sketch" in groups and not a.instances and "cats" not in why:
        print("[cats-sketch] MTSR vs CATS sketch, 100 instances", flush=True)
        run("cats-sketch", [spy, S("mtsr_cats_sketch_bench.py"), BL.NEXTS, "--timeout", "120",
                       "-o", os.path.join(out, "MTSR_CATS_sketch")], HERE, log)
    if "handcrafted" in groups and not a.instances:
        os.makedirs(os.path.join(out, "handcrafted"), exist_ok=True)
        env = dict(os.environ, BENCH_SRC=out, PAPER_OUT=os.path.join(out, "paper_out"))
        pdir = os.path.join(BENCH, "paper")
        if "corp" not in why:
            print("[handcrafted] CORP's arbiters (Table 6)", flush=True)
            for extra in (["--timeout", "120"], ["--timeout", "600", "--only", "full_4_GF"]):
                subprocess.call([spy, os.path.join(pdir, "corp_curated.py"), "--run"] + extra,
                                cwd=pdir, env=env, stdout=open(log, "a"), stderr=subprocess.STDOUT)
        if "cats" not in why:
            print("[handcrafted] CATS's hand-crafted instances (Table 4)", flush=True)
            subprocess.call([spy, os.path.join(pdir, "cats_handcrafted.py"), "--run", "--timeout", "120"],
                            cwd=pdir, env=env, stdout=open(log, "a"), stderr=subprocess.STDOUT)
    shutil.rmtree(work, ignore_errors=True)
    if own:
        summary(os.path.join(out, tag), time.monotonic() - started)
        return
    src = "rerun" if out == os.path.abspath(RERUN) else out
    print(f"\ndone in {(time.monotonic() - started) / 3600:.1f} h.  Next, from the artifact folder:\n"
          f"  python3 bench/scripts/compare_runs.py" + ("" if src == "rerun" else f" --rerun {out}") + "\n"
          f'  "$SPOT_ENV_PY" bench/paper/make_tables.py --src {src} --subset')


if __name__ == "__main__":
    main()
