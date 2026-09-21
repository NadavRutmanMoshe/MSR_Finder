#!/usr/bin/env python3
"""
corp_compare.py -- run CORP and TraceMSR's MTSR synthesis on the same instances
and record runtime AND witness size.

    python3 claude_scripts/corp_compare.py <dir-or-files...> -o results.csv \
            [--timeout 120] [--sample N] [--seed 0]

The two tools answer the *same shape* of question -- given a system S, a trace
pi and an omega-regular effect, synthesize an automaton characterizing the cause
-- so unlike the CATS comparison this one has a comparable result column, not
just a runtime column.

  CORP     Finkbeiner, Frenkel, Metzger, Siber, "Synthesis of Temporal
           Causality", CAV 2024.  Builds a nondeterministic Buchi automaton for
           the *actual cause* (counterfactual: complementation of the
           closest-counterfactual-world product), over the input APs.
  TraceMSR MTSR: the minimal automaton whose language is the set of input
           sequences that *guarantee* the effect, over the input APs.

Both outputs are NBAs over the same alphabet, so `states` is directly
comparable -- but only alongside the determinism flag, since a nondeterministic
3-state witness is not the same object as a deterministic one.

What is measured
----------------
Both numbers are whole-process wall clock, so both carry a constant ~0.3s of
spot import; at these sizes that is a large share of each, and the honest
reading is the size column plus the shape of the runtime distribution, not a
ratio of two sub-second medians.
TraceMSR wall clock is `mtsr_min.py --json`, NOT `tracemsr.py --run 4`: the demo
entry point's single `autfilt --sat-minimize -B` call fails on the whole `F G`
class (see mtsr_min.py's docstring).

`--check-mtsr` adds the result column: whether CORP's cause and our MTSR witness
are the same language.  Expect mostly "no", and that is the point -- CORP
characterizes the *actual cause* on one trace (counterfactual necessity), MTSR
characterizes the input sequences that *guarantee* the effect on every trace.
The column says which way the inclusion goes when it goes one way.

Input translation (TraceMSR .txt -> CORP's three files)
-------------------------------------------------------
  [system] + [inputs]  ->  system.hoa with a `controllable-AP:` line listing the
                           OUTPUT indices (spot's `get_synthesis_output_aps`)
  [effect]             ->  effect.txt (LTL; CORP negates it itself)
  [lasso]              ->  trace.txt in spot's word format,
                           `(a & !b); cycle{(!a & b)}`
Each step of the lasso is written as a FULL conjunction over every AP, positive
or negated, so the word is a concrete omega-word and not a set of them.
"""
import argparse
import csv
import glob
import json
import os
import random
import re
import subprocess
import sys
import time

import bench_lib as BL

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

SPOT_PY = os.environ.get("SPOT_ENV_PY",
    os.path.expanduser("~/micromamba/envs/spot/bin/python3"))
CORP_DIR = os.environ.get("CORP_DIR",
                          os.path.join(_ROOT, "tools", "CORP"))
MTSR_MIN = os.path.join(_HERE, "mtsr_min.py")

# proc_guard, not subprocess.run(timeout=): both tools shell out to spot
# helpers, so a plain timeout leaves grandchildren spinning at 100% CPU.
# bench/ ships inside the tool repo (MSR_Finder/bench); the recorded runs
# were taken with it beside the tool (demo_tool/bench). Accept both, and let
# $MSR_FINDER_SCRIPTS override.
def _msr_finder_scripts():
    _here = _HERE
    _c = [os.environ.get("MSR_FINDER_SCRIPTS"),
          os.path.join(_here, "..", "..", "scripts"),
          os.path.join(_here, "..", "..", "MSR_Finder", "scripts")]
    for _p in _c:
        if _p and os.path.exists(os.path.join(_p, "proc_guard.py")):
            return os.path.abspath(_p)
    return os.path.abspath(_c[-1])


_PG = _msr_finder_scripts()
for _p in (_HERE, _PG):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import proc_guard          # noqa: E402
except ImportError:            # stand-alone fallback
    proc_guard = None

FIELDS = [
    "instance", "inputs", "aps", "sys_states", "effect",
    "corp_status", "corp_states", "corp_edges", "corp_det", "corp_s",
    "mtsr_status", "mtsr_states", "mtsr_aps", "mtsr_det", "mtsr_dba_exists",
    "mtsr_mode", "mtsr_s",
    "equiv", "equiv_note",
]


# --------------------------------------------------------------------------- #
# parsing the TraceMSR .txt
# --------------------------------------------------------------------------- #
_HEADERS = ('info', 'inputs', 'cause', 'effect', 'lasso', 'system')


def parse_instance(path):
    data, cur, buf = {}, None, []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith('[') and s.endswith(']') and s[1:-1] in _HEADERS:
                if cur:
                    data[cur] = "".join(buf).strip()
                cur, buf = s[1:-1], []
            elif cur:
                buf.append(line)
    if cur:
        data[cur] = "".join(buf).strip()
    return data


def parse_lasso(text):
    aps, prefix, loop = [], [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("AP:"):
            aps = re.findall(r'"([^"]+)"', line)
        elif line.startswith("Prefix:"):
            prefix = re.findall(r'\{([^}]*)\}', line)
        elif line.startswith("Loop:"):
            loop = re.findall(r'\{([^}]*)\}', line)
    return aps, prefix, loop


def lasso_to_word(aps, prefix, loop):
    """`{0} {}` + `{1}` -> `("a" & !"b"); cycle{(!"a" & "b")}` (spot word syntax)."""
    def step(idx_str):
        on = set(int(i) for i in idx_str.split()) if idx_str.strip() else set()
        return "(" + " & ".join(
            ('"%s"' % a) if i in on else ('!"%s"' % a) for i, a in enumerate(aps)
        ) + ")"

    parts = [step(s) for s in prefix]
    body = "; ".join(parts)
    if loop:
        cyc = "cycle{" + "; ".join(step(s) for s in loop) + "}"
        body = (body + "; " + cyc) if body else cyc
    return body


def with_controllable(system_text, inputs_text):
    """Add spot's `controllable-AP:` line naming the OUTPUT indices."""
    inputs = set(re.findall(r'"([^"]+)"', inputs_text))
    m = re.search(r'AP:\s*\d+\s*(.*)', system_text)
    if not m:
        raise ValueError("no AP line in [system]")
    aps = re.findall(r'"([^"]+)"', m.group(1))
    outs = [str(i) for i, a in enumerate(aps) if a not in inputs]
    line = "controllable-AP: " + " ".join(outs)
    if "--BODY--" in system_text:
        return system_text.replace("--BODY--", line + "\n--BODY--", 1)
    return system_text + "\n" + line


# --------------------------------------------------------------------------- #
# running the tools
# --------------------------------------------------------------------------- #
def _run(cmd, timeout, cwd=None):
    t0 = time.monotonic()
    try:
        if proc_guard is not None:
            p = proc_guard.run(cmd, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd)
        else:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd)
        return p, time.monotonic() - t0, False
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - t0, True


def _sub_graceful(cmd, timeout, grace=25):
    """Bound a child that itself owns solver process groups.

    proc_guard's group kill cannot reach a grandchild that called setsid, and
    EVERY AutoHyper started through proc_guard does exactly that. SIGKILLing
    the middle process therefore orphans the solver at 100% CPU --
    the leak that pegged a core for 37 minutes on 2026-08-25 and silently taxed
    every measurement taken alongside it.

    So the inner bound is coreutils `timeout --signal=TERM`, which gives the
    child a chance to unwind through proc_guard.run's `finally` and killpg its
    own solver group. `--kill-after` is the backstop
    if that unwinding hangs, and the proc_guard wall outside it (timeout + grace
    + slack) is the backstop to the backstop -- by then there is nothing left to
    orphan.
    """
    wrapped = ["timeout", "--signal=TERM", f"--kill-after={grace}",
               str(timeout)] + list(cmd)
    p, dt, killed = _run(wrapped, timeout + grace + 15)
    # coreutils reports 124 for the TERM deadline, 137 for the KILL backstop.
    if not killed and p is not None and p.returncode in (124, 137):
        killed = True
    return p, dt, killed


def survivors():
    """Solver processes still alive. Reported, never killed -- this driver has
    no way to tell a stray of ours from one the user started in another shell,
    and proc_guard.reap_leaked_solvers deliberately refuses to guess either."""
    out = []
    if not os.path.isdir('/proc'):
        return out
    for e in os.listdir('/proc'):
        if not e.isdigit():
            continue
        try:
            with open(f'/proc/{e}/comm') as f:
                name = f.read().strip()
            if name not in ('autfilt', 'ltl2tgba', 'AutoHyper', 'CATS'):
                continue
            # Skip zombies. A killed solver sits in Z for the instant between
            # its death and its parent reaping it, holds no CPU, and vanishes on
            # its own -- reporting it as a survivor is a false alarm, and a
            # false alarm here is worse than no alarm, because the next person
            # to see one will start killing things by name.
            # /proc/<pid>/stat field 3 is the state; the comm in field 2 can
            # contain spaces and ')', so split after the LAST ')'.
            with open(f'/proc/{e}/stat') as f:
                if f.read().rsplit(')', 1)[1].split()[0] == 'Z':
                    continue
            out.append(f"{name}({e})")
        except (OSError, IndexError):
            continue
    return out


_RE_STATES = re.compile(r'^States:\s*(\d+)', re.M)


def hoa_stats(path):
    """(states, edges, deterministic) from a saved HOA file."""
    with open(path) as f:
        txt = f.read()
    m = _RE_STATES.search(txt)
    states = int(m.group(1)) if m else None
    body = txt.split("--BODY--", 1)[-1]
    edges = sum(1 for ln in body.splitlines() if ln.strip().startswith('['))
    det = "deterministic" in (txt.split("--BODY--", 1)[0])
    return states, edges, det


def run_corp(data, workdir, timeout):
    os.makedirs(workdir, exist_ok=True)
    p_sys = os.path.join(workdir, "system.hoa")
    p_eff = os.path.join(workdir, "effect.txt")
    p_trc = os.path.join(workdir, "trace.txt")
    p_res = os.path.join(workdir, "result.hoa")
    if os.path.exists(p_res):
        os.remove(p_res)

    aps, prefix, loop = parse_lasso(data['lasso'])
    with open(p_sys, 'w') as f:
        f.write(with_controllable(data['system'], data['inputs']))
    with open(p_eff, 'w') as f:
        f.write(data['effect'])
    with open(p_trc, 'w') as f:
        f.write(lasso_to_word(aps, prefix, loop))

    cmd = [SPOT_PY, os.path.join(CORP_DIR, "corp.py"),
           "-s", p_sys, "-e", p_eff, "-t", p_trc, "-o", p_res]
    p, dt, killed = _run(cmd, timeout, cwd=CORP_DIR)
    if killed:
        return {"corp_status": "TIMEOUT", "corp_s": round(dt, 3)}
    out = (p.stdout or "") + (p.stderr or "")
    if "No cause exists" in out:
        return {"corp_status": "NO_CAUSE", "corp_s": round(dt, 3)}
    if p.returncode != 0 or not os.path.exists(p_res):
        first = next((ln for ln in out.strip().splitlines()[::-1] if ln.strip()),
                     "")
        return {"corp_status": "ERROR", "corp_s": round(dt, 3),
                "corp_err": first[:160]}
    st, ed, det = hoa_stats(p_res)
    return {"corp_status": "OK", "corp_states": st, "corp_edges": ed,
            "corp_det": det, "corp_s": round(dt, 3)}


def run_mtsr(path, timeout, save_hoa=None):
    cmd = [SPOT_PY, MTSR_MIN, os.path.abspath(path), "--json"]
    if save_hoa:
        cmd += ["--save-hoa", save_hoa]
    # mtsr_min drives autfilt through proc_guard, so it owns a group of its
    # own -- terminate it with TERM, not KILL. See _sub_graceful.
    p, dt, killed = _sub_graceful(cmd, timeout)
    if killed:
        return {"mtsr_status": "TIMEOUT", "mtsr_s": round(dt, 3)}
    if p.returncode != 0:
        first = next((ln for ln in (p.stderr or "").strip().splitlines()[::-1]
                      if ln.strip()), "")
        return {"mtsr_status": "ERROR", "mtsr_s": round(dt, 3),
                "mtsr_err": first[:160]}
    try:
        r = json.loads(p.stdout.strip().split("\n")[-1])
    except Exception:
        return {"mtsr_status": "ERROR", "mtsr_s": round(dt, 3),
                "mtsr_err": "unparseable json"}
    return {"mtsr_status": "OK", "mtsr_states": r.get("states"),
            "mtsr_aps": r.get("aps"), "mtsr_det": r.get("deterministic"),
            "mtsr_dba_exists": r.get("dba_exists"), "mtsr_mode": r.get("mode"),
            "mtsr_s": round(dt, 3)}


# --------------------------------------------------------------------------- #
# is our witness the same language as CORP's cause?
# --------------------------------------------------------------------------- #
_EQUIV_SRC = r"""
import sys, spot
a = spot.automaton(sys.argv[1])
b = spot.automaton(sys.argv[2])
if spot.are_equivalent(a, b):
    print("EQUIVALENT"); raise SystemExit
ca, cb = spot.complement(a), spot.complement(b)
# Named by INCLUSION, not by "subsumes": a_in_b is L(CORP) subset of L(MTSR),
# which reads as MTSR being the WEAKER (larger-language) cause -- the opposite
# of what "CORP_SUBSUMES_MTSR" would suggest. That wording inverted the finding
# once already; inclusion names cannot be misread.
a_in_b = spot.product(a, cb).is_empty()
b_in_a = spot.product(b, ca).is_empty()
print("CORP_SUBSET_OF_MTSR" if a_in_b else
      "MTSR_SUBSET_OF_CORP" if b_in_a else "INCOMPARABLE")
"""


def compare_languages(corp_hoa, mtsr_hoa, timeout):
    """corp.py --check does `candidate.equivalent_to(result)` AFTER re-running
    synthesis.  We already have both automata, so decide it directly -- same
    question, without paying for the synthesis a second time and without
    polluting the runtime column."""
    p, _dt, killed = _run([SPOT_PY, "-c", _EQUIV_SRC, corp_hoa, mtsr_hoa],
                          timeout)
    if killed:
        return "", "equivalence check timed out"
    if p.returncode != 0:
        return "", (p.stderr or "").strip().splitlines()[-1][:120]
    v = (p.stdout or "").strip().splitlines()[-1] if p.stdout.strip() else ""
    return ("yes" if v == "EQUIVALENT" else "no" if v else ""), v


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.txt")))
        elif p.endswith(".list"):
            # a file of instance paths, one per line -- pi_gate.py's -o output,
            # so the gate and the comparison always agree on the population
            with open(p) as f:
                files += [l.strip() for l in f if l.strip()]
        else:
            files += sorted(glob.glob(p))
    return files


MTSR_FIELDS = ["instance", "inputs", "aps", "sys_states", "effect",
               "mtsr_status", "mtsr_states", "mtsr_aps", "mtsr_det",
               "mtsr_dba_exists", "mtsr_mode", "mtsr_s", "mtsr_err"]
CORP_FIELDS = ["instance", "inputs", "aps", "sys_states", "effect",
               "corp_status", "corp_states", "corp_edges", "corp_det",
               "corp_s", "corp_err", "equiv", "equiv_note"]


def main():
    ap = argparse.ArgumentParser(
        description="MTSR vs CORP on the SYNTCOMP corpus. Writes two CSVs.")
    ap.add_argument('paths', nargs='*', default=[BL.SYNTCOMP])
    ap.add_argument('-o', '--outdir',
                    default=os.path.join(BL.RESULTS, "MTSR_CORP"))
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--sample', type=int, default=0)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--workdir', default=None)
    ap.add_argument('--no-check-mtsr', action='store_true',
                    help="skip the language-equivalence column (it costs a "
                         "complementation per instance)")
    ap.add_argument('--no-gate', action='store_true')
    args = ap.parse_args()
    BL.install_sigint()
    check_mtsr = not args.no_check_mtsr

    if not os.path.exists(os.path.join(CORP_DIR, "corp.py")):
        sys.exit(f"CORP not found at {CORP_DIR} (set CORP_DIR)")

    files = sorted(collect(args.paths))
    gate = BL.load_gate()
    if gate and not args.no_gate:      # gate first: see msr_bench for why
        files = [f for f in files
                 if gate.get(os.path.splitext(os.path.basename(f))[0]) == "OK"]
    files = BL.pick(files, args.sample, args.seed)

    workdir = args.workdir or os.path.join(
        os.environ.get('TMPDIR', '/tmp'), f"corp_bench_{os.getpid()}")
    os.makedirs(workdir, exist_ok=True)

    rows = BL.PairRows(args.outdir, "mtsr.csv", MTSR_FIELDS,
                       "corp.csv", CORP_FIELDS)
    BL.banner("MTSR vs CORP  ->  results/MTSR_CORP/{mtsr,corp}.csv")
    todo = [f for f in files if os.path.basename(f) not in rows.done]
    print(f"{len(files)} gated instances, {len(rows.done)} already done, "
          f"{len(todo)} to run\n", flush=True)

    for i, path in enumerate(todo, 1):
        name = os.path.basename(path)
        data = parse_instance(path)
        if not all(k in data for k in ('system', 'effect', 'lasso', 'inputs')):
            print(f"  [{i}/{len(todo)}] {name}: missing sections, skipped")
            continue
        ins = re.findall(r'"([^"]+)"', data['inputs'])
        m = re.search(r'AP:\s*\d+\s*(.*)', data['system'])
        aps = re.findall(r'"([^"]+)"', m.group(1)) if m else []
        nst = len(re.findall(r'^State:', data['system'], re.M))
        row = {"instance": name, "inputs": len(ins), "aps": len(aps),
               "sys_states": nst, "effect": data['effect'].replace("\n", " ")}
        wd = os.path.join(workdir, name[:-4])
        row.update(run_corp(data, wd, args.timeout))
        mtsr_hoa = os.path.join(wd, "mtsr.hoa") if check_mtsr else None
        row.update(run_mtsr(path, args.timeout, mtsr_hoa))
        if (check_mtsr and row.get("corp_status") == "OK"
                and row.get("mtsr_status") == "OK"
                and os.path.exists(os.path.join(wd, "result.hoa"))
                and mtsr_hoa and os.path.exists(mtsr_hoa)):
            eq, note = compare_languages(os.path.join(wd, "result.hoa"),
                                         mtsr_hoa, args.timeout)
            row["equiv"], row["equiv_note"] = eq, note
        rows.write(row)
        BL.progress(i, len(todo), name,
                    f"corp={row.get('corp_status'):<8}{row.get('corp_states')}st "
                    f"{row.get('corp_s')}s | mtsr={row.get('mtsr_status'):<8}"
                    f"{row.get('mtsr_states')}st {row.get('mtsr_s')}s")
        BL.tick()
    rows.close()
    left = survivors()
    if left:
        print(f"WARNING: solver processes still alive after the sweep: "
              f"{', '.join(left)}")


if __name__ == '__main__':
    main()
