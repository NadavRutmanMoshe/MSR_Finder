#!/usr/bin/env python3
"""
rerun -- re-measure the paper's benchmark, the way the recorded results were
measured: every tool on one instance before moving on to the next, so that all
tools see the same machine state.

    python3 scripts/rerun.py --plan             # what would run, and how long it took us
    python3 scripts/rerun.py --quick            # a few instances of every dataset (~10 min)
    python3 scripts/rerun.py                    # the subset (about 1/10, 4.5 h on our laptop)
    python3 scripts/rerun.py --full             # all 987 instances (~45 h)
    python3 scripts/rerun.py --dataset cats-sketch      # one dataset (see below)
    python3 scripts/rerun.py --instances arbiter2__e09 TorcsGearing__e02
    python3 scripts/rerun.py --corpus my_folder   # your own instances (see below)

Run from bench/ with the system python3; each tool is started under the
interpreter it needs (bench_lib: SPOT_PY, EXPLAIN_PY). Results go to
bench/rerun/ (--out to change) in the same layout as bench/results/, so

    python3 scripts/compare_runs.py               # instance by instance against the recorded rows
    ~/micromamba/envs/spot/bin/python3 paper/make_tables.py --src rerun

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

DATASETS
--------
Every table and figure of the paper is made from exactly one dataset
(Table 7 sums up three comparisons: its first two rows come from `main`, the
third from `cats-sketch`). --dataset picks some (comma-separated; default all):

    main             the 987 instances: per instance, in this order (as for the
                     recorded runs)                                  Tables 1, 2, 3, 7
                       MSR with AutoHyper, then with HyperQB      msr_bench.py      Figs. 6, 9, 10
                       ExplainHyper, the tool of [11]             explainhyper_bench.py
                       the MTSR, then CORP                        mtsr_corp_bench.py
    cats-check       the 823 instances with |IP| <= 3: the MTSR --check
                     and CATS --check on the same random candidate
                                              mtsr_cats_check_bench.py    Fig. 7
    cats-sketch      MTSR vs CATS sketch on CATS's X^k error family (100)
                                              mtsr_cats_sketch_bench.py   Tables 5, 7
    cats-handcrafted CATS's hand-crafted instances (11)
                                              paper/cats_handcrafted.py   Table 4
    corp-arbiters    the arbiters of CORP's Table 1 (15)
                                              paper/corp_curated.py       Table 6
    ltl              the LTL search over every MTSR of `main`
                                              paper/ltl_rerun_catsrl.py   Table 8

Timeout: 120 s per tool per instance for |IP| <= 3, 300 s for |IP| = 4.
The parts of `main` can also be named on their own: msr, explainhyper,
mtsr-corp (handcrafted = cats-handcrafted + corp-arbiters).

THE SUBSET
----------
Every 10th instance of each |IP| class, in name order (83 of 823 with
|IP| <= 3, 17 of 164 with |IP| = 4), plus the other datasets whole. Systematic
rather than random, so it spreads over the systems and is the same for
everyone. --plan prints how long these instances took on the machine of the
paper; budget more on a slower machine or in a VM.

THE QUICK TEST
--------------
--quick: every dataset on a few instances, about 10 minutes. The 16 instances
of QUICK below (4, 5, 5 and 2 with |IP| = 1, 2, 3, 4; different systems and
different answers, and every tool finished them well within its timeout, so a
slower machine gives the same answers), every 10th CATS sketch instance, the
hand-crafted instances of CATS and CORP (but full_4_GF), and the LTL search
over the MTSRs of the 16. 8 minutes on the machine of the paper.
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
STEPS = ("msr", "explainhyper", "mtsr-corp", "cats-check", "cats-sketch", "cats-handcrafted",
         "corp-arbiters", "ltl")
DATASETS = ("main", "cats-check", "cats-sketch", "cats-handcrafted", "corp-arbiters", "ltl")
ALIASES = {"main": ("msr", "explainhyper", "mtsr-corp"),
           "handcrafted": ("cats-handcrafted", "corp-arbiters"),
           "eh": ("explainhyper",), "sketch": ("cats-sketch",), "curated": ("corp-arbiters",)}
STRIDE = 10
# --quick: see THE QUICK TEST above
QUICK = ("example101__e02", "lilydemo09__e05", "ltl2dba_U11__e09", "ltl2dba_Q1__e03",
         "TorcsAccelerating__e03", "ltl2dba_alpha4__e02", "ltl2dpa16__e02", "ltl2dba_beta1__e04",
         "lift2__e02", "lilydemo24__e06", "LightsMin_6bea956e__e05", "ltl2dba_U13__e00",
         "ltl2dba09__e00", "ltl2dpa06__e09", "Gamemodule__e07", "lilydemo21__e06")
# minutes on the machine of the paper, for --plan
FIXED_MIN = {"cats-sketch": 20, "cats-handcrafted": 1, "corp-arbiters": 3, "ltl": 6}
QUICK_MIN = {"cats-sketch": 2, "cats-handcrafted": 1, "corp-arbiters": 1, "ltl": 1}


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
def run(label, cmd, cwd, log, env=None):
    t0 = time.monotonic()
    with open(log, "a") as lf:
        lf.write(f"\n== {label}  {time.strftime('%F %T')}\n$ {' '.join(cmd)}\n")
        lf.flush()
        rc = subprocess.call(cmd, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT, env=env)
    if rc != 0:
        print(f"      [{label}] exit {rc} -- see {log}")
    return time.monotonic() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="all instances, not the subset")
    ap.add_argument("--quick", action="store_true", help="a few instances of every dataset (~10 min)")
    ap.add_argument("--instances", nargs="+", metavar="NAME", help="only these instances")
    ap.add_argument("--dataset", "--only", dest="only", default=",".join(DATASETS),
                    help="comma-separated datasets (default: all): " + ", ".join(DATASETS))
    ap.add_argument("--corpus", default="both", metavar="{ip3,ip4,both,DIR}",
                    help="one of the paper's corpora, or a folder of your own instances")
    ap.add_argument("--timeout", type=int, default=120, help="per tool, for --corpus DIR (default 120 s)")
    ap.add_argument("--no-gate", action="store_true", help="--corpus DIR: do not skip instances that fail the gate")
    ap.add_argument("--out", default=RERUN)
    ap.add_argument("--plan", action="store_true", help="print the plan and exit")
    a = ap.parse_args()
    groups = []
    for g in (g.strip() for g in a.only.split(",") if g.strip()):
        for x in ALIASES.get(g, (g,)):
            if x not in STEPS:
                sys.exit(f"unknown dataset {g}; datasets: {', '.join(DATASETS)}")
            if x not in groups:
                groups.append(x)
    groups.sort(key=STEPS.index)
    if a.quick and (a.full or a.instances):
        sys.exit("--quick is its own selection; drop --full / --instances")
    out = os.path.abspath(a.out)
    own = a.corpus not in ("ip3", "ip4", "both")
    if own:
        folder = os.path.abspath(a.corpus)
        if not os.path.isdir(folder):
            sys.exit(f"--corpus: {a.corpus} is neither ip3, ip4, both nor a folder")
        tag = os.path.basename(folder)
        sel = selection(True, set(a.instances or []), ((tag, folder, a.timeout),))
        groups = [g for g in groups if g in ("msr", "explainhyper", "mtsr-corp", "cats-check")]
    else:
        sel = selection(a.full, set(QUICK if a.quick else a.instances or []))
        if a.corpus != "both":
            sel = [s for s in sel if s[0] == a.corpus]
        if not set(groups) & {"msr", "explainhyper", "mtsr-corp", "cats-check"}:
            sel = []                 # only datasets that are not per instance
    why = available()

    if own:
        per = sum({"msr": 2, "explainhyper": 1, "mtsr-corp": 2, "cats-check": 2}.get(g, 0) for g in groups)
        print(f"instances: {len(sel)} in {folder} ({a.timeout} s per tool); groups: {', '.join(groups)}")
        print(f"at most {len(sel) * per * a.timeout / 3600:.1f} h (if every tool ran out of time)")
    else:
        n3, n4 = sum(s[0] == "ip3" for s in sel), sum(s[0] == "ip4" for s in sel)
        est = recorded_seconds(sel, groups)
        if not a.instances:          # the datasets that are not per instance
            est += 60 * sum((QUICK_MIN if a.quick else FIXED_MIN).get(g, 0) for g in groups)
        if sel:
            print(f"instances: {n3} with |IP| <= 3 (120 s), {n4} with |IP| = 4 (300 s)")
        print(f"runs: {', '.join(groups)}")
        took = f"{est / 3600:.1f} h" if est >= 3600 else f"{max(1, round(est / 60))} min"
        print(f"on the machine of the paper this took {took} (expect more in a VM)")
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
    spy, epy = BL.SPOT_PY, BL.EXPLAIN_PY
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
            # under spot's python, as for the recorded runs: the HyperQB
            # pipeline shrinks a non-minimal witness only with spot in-process
            run("msr autohyper", [spy, S("msr_bench.py"), "--model", "autohyper", "--corpus", one,
                                  "--no-gate", "--timeout", str(t), "--out",
                                  os.path.join(res, "MSR", "msr_autohyper.csv")],
                os.path.join(res, "cwd_autohyper"), log)
            if "hyperqb" not in why:
                run("msr hyperqb", [spy, S("msr_bench.py"), "--model", "hyperqb", "--corpus", one,
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

    # the datasets that are not per instance (each skipped once its results exist)
    if "cats-sketch" in groups and not a.instances and "cats" not in why:
        n = "10" if a.quick else "100"
        print(f"[cats-sketch] MTSR vs CATS sketch, {n} instances", flush=True)
        run("cats-sketch", [spy, S("mtsr_cats_sketch_bench.py"), BL.NEXTS, "--timeout", "120",
                            "--every", "10" if a.quick else "1",
                            "-o", os.path.join(out, "MTSR_CATS_sketch")], HERE, log)
    pdir = os.path.join(BENCH, "paper")
    env = dict(os.environ, BENCH_SRC=out, PAPER_OUT=os.path.join(out, "paper_out"))
    hand = os.path.join(out, "handcrafted")
    if "corp-arbiters" in groups and not a.instances and "corp" not in why:
        if os.path.exists(os.path.join(hand, "corp_curated.csv")):
            print("[corp-arbiters] already done", flush=True)
        else:
            print("[corp-arbiters] CORP's arbiters (Table 6)", flush=True)
            os.makedirs(hand, exist_ok=True)
            # full_4_GF again with 600 s, as recorded; the quick test leaves it out
            # (the heaviest: MTSR runs into its 6 GB memory cap after about a minute)
            for extra in ([["--timeout", "120", "--exclude", "full_4_GF"]] if a.quick else
                          [["--timeout", "120"], ["--timeout", "600", "--only", "full_4_GF"]]):
                run("corp-arbiters", [spy, os.path.join(pdir, "corp_curated.py"), "--run"] + extra,
                    pdir, log, env)
    if "cats-handcrafted" in groups and not a.instances and "cats" not in why:
        if os.path.exists(os.path.join(hand, "cats_handcrafted.csv")):
            print("[cats-handcrafted] already done", flush=True)
        else:
            print("[cats-handcrafted] CATS's hand-crafted instances (Table 4)", flush=True)
            os.makedirs(hand, exist_ok=True)
            run("cats-handcrafted", [spy, os.path.join(pdir, "cats_handcrafted.py"), "--run",
                                     "--timeout", "120"], pdir, log, env)
    if "ltl" in groups and not a.instances:
        # the MTSRs of our recorded `main` run, so that this dataset stands alone
        print("[ltl] the LTL search over the MTSRs (Table 8; uses 4 cores)", flush=True)
        run("ltl", [spy, os.path.join(pdir, "ltl_rerun_catsrl.py"), "--max-size", "9", "--budget", "240"]
            + (["--instances", *QUICK] if a.quick else []),
            pdir, log, dict(os.environ, LTL_OUT=os.path.join(out, "stats")))
    shutil.rmtree(work, ignore_errors=True)
    if own:
        summary(os.path.join(out, tag), time.monotonic() - started)
        return
    src = "rerun" if out == os.path.abspath(RERUN) else out
    print(f"\ndone in {(time.monotonic() - started) / 3600:.1f} h.  Next, from the artifact folder:\n"
          f"  python3 bench/scripts/compare_runs.py" + ("" if src == "rerun" else f" --rerun {out}") + "\n"
          f'  "$SPOT_ENV_PY" bench/paper/make_tables.py --src {src}')


if __name__ == "__main__":
    main()
