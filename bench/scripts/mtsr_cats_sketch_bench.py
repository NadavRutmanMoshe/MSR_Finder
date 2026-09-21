#!/usr/bin/env python3
"""
cats_next_compare.py -- CATS cause SKETCHING over a next-only window vs
TraceMSR's MTSR synthesis, on identical instances.

    ~/micromamba/envs/spot/bin/python3 claude_scripts/cats_next_compare.py \
        ~/tracemsr_bench/next_bench -o claude_scripts/cats_next.csv --timeout 120

Instances come from `cats_next_gen.py`, which re-implements CATS's own Table 2
generator and emits both formats side by side (`<dir>/cats/<n>.txt` and
`<dir>/tracemsr/<n>.txt`).

TEMPORAL AGAINST TEMPORAL
-------------------------
CATS's sketch returns a temporal cause: a QPTL formula over the inputs.  The
matching object on our side is MTSR -- the automaton characterizing the input
sequences that guarantee the effect.  Both denote an omega-language over the
same input alphabet, so spot can decide equivalence directly, and that is the
comparison this driver makes.

MSR is deliberately absent.  It is a non-temporal notion (a finite set of
(input, position) pairs on one trace); scoring it against a temporal cause
compares two different kinds of object.  An earlier version of this script did
exactly that -- it reported "100% agreement" between CATS's formula and MSR's
literal conjunction, which was really a statement about a set of positions, not
about a temporal property.

Why this is the comparison where RESULTS are comparable
--------------------------------------------------------
The effect is `X^k "error"` and the sketch is `?{IP} & X ?{IP} & ... &
X^(k-1) ?{IP}`.  CATS fills each hole with a conjunction of positive and
negative input literals and keeps the first filling that is an actual cause;
candidates that do not hold on the lasso are pruned.  MTSR, built from the
system and the effect alone, characterizes exactly the input sequences that
force `X^k "error"`.  Both are omega-languages over the inputs, and
`spot.are_equivalent` settles the question.

Where they may legitimately differ, and why that is a finding rather than a bug:

  * CATS's cause is TRACE-RELATIVE -- it explains the effect on the one lasso it
    was given -- while MTSR is trace-independent.  If several input prefixes
    drive the system to the error state, MTSR is the union of them and CATS's
    answer is one; the inclusion column says so.
  * CATS returns the FIRST candidate in `computeFinitePowerSet [0;1;2]` order
    that passes, which need not be the smallest.
  * The notions differ.  CATS certifies an *actual cause* (counterfactual
    necessity under contingencies); MTSR certifies sufficiency (every trace
    whose inputs are in the language shows the effect).

Runtime is process wall clock for both, so on the smallest instances a large
part of each number is constant startup (.NET for CATS, spot's import for us).
Read the trend across k rather than any single row.
"""
import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
import time

import bench_lib as BL

import spot

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
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


_DEMO = _msr_finder_scripts()
for p in (_HERE, _DEMO):
    if p not in sys.path:
        sys.path.insert(0, p)

import cats_util  # noqa: E402

SPOT_PY = os.environ.get("SPOT_ENV_PY",
    os.path.expanduser("~/micromamba/envs/spot/bin/python3"))
MTSR_MIN = os.path.join(_HERE, "mtsr_min.py")

FIELDS = [
    "name", "size", "sys_states", "inputs", "k", "candidates",
    "cats_status", "cats_cause", "cats_size", "cats_s",
    # CATS re-run in --check mode on the cause its own --sketch found.  This is
    # the ONE column that times a full, POSITIVE cause verification: all five
    # obligations run and all five hold.  Everywhere else CATS's check column is
    # a rejection, which short-circuits at the first failing obligation.
    "cats_verify_status", "cats_verify_stages", "cats_verify_s",
    "mtsr_status", "mtsr_states", "mtsr_det", "mtsr_s",
    "agree", "compare_note",
]


def _sub(cmd, timeout):
    """proc_guard, not subprocess.run(timeout=): the child shells out to spot,
    and a plain timeout kills only the direct child."""
    t0 = time.monotonic()
    runner = cats_util.proc_guard.run if cats_util.proc_guard else subprocess.run
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=timeout)
        return p, time.monotonic() - t0, False
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - t0, True


def _sub_graceful(cmd, timeout, grace=25):
    """Bound a child that itself owns solver process groups.

    proc_guard's group kill cannot reach a grandchild that called setsid, and
    every solver started through proc_guard does exactly that.  SIGKILLing the
    middle process orphans the solver at 100% CPU -- the leak that pegged a core
    for 37 minutes on 2026-08-25.  `timeout --signal=TERM` gives the child a
    chance to unwind through proc_guard.run's `finally` and killpg its own
    group; `--kill-after` is the backstop.
    """
    wrapped = ["timeout", "--signal=TERM", f"--kill-after={grace}",
               str(timeout)] + list(cmd)
    p, dt, killed = _sub(wrapped, timeout + grace + 15)
    if not killed and p is not None and p.returncode in (124, 137):
        killed = True
    return p, dt, killed


def survivors():
    """Solver processes still alive.  Reported, never killed; zombies skipped."""
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
            with open(f'/proc/{e}/stat') as f:
                if f.read().rsplit(')', 1)[1].split()[0] == 'Z':
                    continue
            out.append(f"{name}({e})")
        except (OSError, IndexError):
            continue
    return out


def run_mtsr(path, timeout, save_hoa):
    """MTSR synthesis.  `save_hoa` is required: the witness automaton IS the
    answer being compared, so it has to reach the comparison step."""
    cmd = [SPOT_PY, MTSR_MIN, os.path.abspath(path), "--json",
           "--save-hoa", save_hoa]
    p, dt, killed = _sub_graceful(cmd, timeout)
    if killed:
        return {"mtsr_status": "TIMEOUT", "mtsr_s": round(dt, 3)}
    if p is None or p.returncode != 0:
        return {"mtsr_status": "ERROR", "mtsr_s": round(dt, 3)}
    try:
        r = json.loads(p.stdout.strip().split("\n")[-1])
    except Exception:
        return {"mtsr_status": "ERROR", "mtsr_s": round(dt, 3)}
    return {"mtsr_status": "OK", "mtsr_states": r.get("states"),
            "mtsr_det": r.get("deterministic"), "mtsr_s": round(dt, 3)}


def n_literals(f):
    """Conjunct count of a cause formula, as a crude size for CATS's answer."""
    try:
        g = spot.formula(f)
    except Exception:
        return None
    n, stack = 0, [g]
    while stack:
        x = stack.pop()
        k = x.kindstr()
        if k in ('And', 'X'):
            stack.extend(list(x))
        elif k in ('Not', 'ap'):
            n += 1
        elif k == 'tt':
            pass
        else:
            return None      # not a next-chain conjunction; do not guess
    return n


def compare(cats_cause, mtsr_hoa):
    """(agree, note) for CATS's temporal cause against our MTSR automaton.

    Both denote an omega-language over the input APs, so this is a language
    question and spot answers it exactly.  Inclusion is named by DIRECTION, not
    by "subsumes" -- that wording inverted a finding once already in
    corp_compare.
    """
    if not cats_cause or not mtsr_hoa or not os.path.exists(mtsr_hoa):
        return "", "one side missing"
    try:
        a = spot.translate(spot.formula(cats_cause))
        b = spot.automaton(mtsr_hoa)
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"[:80]
    if spot.are_equivalent(a, b):
        return "yes", "equivalent"
    a_in_b = spot.product(a, spot.complement(b)).is_empty()
    b_in_a = spot.product(b, spot.complement(a)).is_empty()
    if a_in_b:
        return "no", "CATS_SUBSET_OF_MTSR (their cause is strictly stronger)"
    if b_in_a:
        return "no", "MTSR_SUBSET_OF_CATS (our witness is strictly stronger)"
    return "no", "INCOMPARABLE"


MTSR_FIELDS = ["name", "size", "sys_states", "inputs", "k", "candidates",
               "mtsr_status", "mtsr_states", "mtsr_det", "mtsr_s"]
CATS_FIELDS = ["name", "size", "sys_states", "inputs", "k", "candidates",
               "cats_status", "cats_cause", "cats_size", "cats_s",
               "cats_verify_status", "cats_verify_stages", "cats_verify_s",
               "agree", "compare_note"]


def main():
    ap = argparse.ArgumentParser(
        description="MTSR synthesis vs CATS cause SKETCHING, on the nexts "
                    "benchmark. Writes two CSVs.")
    ap.add_argument('bench', nargs='?', default=BL.NEXTS,
                    help="directory made by cats_next_gen.py (cats/, tracemsr/)")
    ap.add_argument('-o', '--outdir',
                    default=os.path.join(BL.RESULTS, "MTSR_CATS_sketch"))
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workdir', default=None)
    args = ap.parse_args()
    BL.install_sigint()

    idx_path = os.path.join(args.bench, "index.csv")
    index = {}
    if os.path.exists(idx_path):
        with open(idx_path) as f:
            index = {r['name']: r for r in csv.DictReader(f)}

    cats_files = sorted(glob.glob(os.path.join(args.bench, "cats", "*.txt")))
    if args.limit:
        cats_files = cats_files[:args.limit]

    workdir = args.workdir or os.path.join(
        os.environ.get('TMPDIR', '/tmp'), f"cats_sketch_{os.getpid()}")
    os.makedirs(workdir, exist_ok=True)

    rows = BL.PairRows(args.outdir, "mtsr.csv", MTSR_FIELDS,
                       "cats_sketch.csv", CATS_FIELDS, key="name")
    BL.banner("MTSR vs CATS --sketch  ->  "
              "results/MTSR_CATS_sketch/{mtsr,cats_sketch}.csv")
    todo = [c for c in cats_files
            if os.path.basename(c)[:-4] not in rows.done]
    print(f"{len(cats_files)} instances, {len(rows.done)} already done, "
          f"{len(todo)} to run\n", flush=True)

    for i, cpath in enumerate(todo, 1):
        name = os.path.basename(cpath)[:-4]
        tpath = os.path.join(args.bench, "tracemsr", name + ".txt")
        if not os.path.exists(tpath):
            print(f"  [{i}] {name}: no TraceMSR twin, skipped")
            continue
        meta = index.get(name, {})
        row = {"name": name, "size": meta.get("size"),
               "inputs": meta.get("inputs"), "k": meta.get("k"),
               "candidates": meta.get("candidates"),
               "sys_states": meta.get("states")}

        r = cats_util.run("sketch", cpath, args.timeout)
        row.update({"cats_status": r["status"], "cats_cause": r.get("cause", ""),
                    "cats_s": r["wall_s"]})
        row["cats_size"] = n_literals(r.get("cause", "")) if r.get("cause") else None

        # Time CATS VERIFYING the answer it just searched for: search cost vs
        # check cost, same instance, same formula. This is the only column
        # anywhere that times a full POSITIVE cause check -- everywhere else
        # CATS's check is a rejection, which short-circuits at the first
        # failing obligation.
        if r.get("cause"):
            vpath = os.path.join(workdir, f"_verify_{name}.txt")
            data = cats_util.parse_instance(cpath)
            ins = re.findall(r'"([^"]+)"', data["inputs"])
            cats_util.write_instance(vpath, name, ins, r["cause"],
                                     data["effect"], data["lasso"],
                                     data["system"])
            v = cats_util.run("check", vpath, args.timeout)
            row.update({"cats_verify_status": v["status"],
                        "cats_verify_stages": v.get("stages", ""),
                        "cats_verify_s": v["wall_s"]})

        mtsr_hoa = os.path.join(workdir, name + "_mtsr.hoa")
        row.update(run_mtsr(tpath, args.timeout, mtsr_hoa))
        agree, note = compare(row.get("cats_cause"), mtsr_hoa)
        row["agree"], row["compare_note"] = agree, note
        rows.write(row)
        BL.progress(i, len(todo), name,
                    f"k={row['k']:<3} cats={row['cats_status']:<9}"
                    f"{row['cats_s']:>7}s | mtsr={row.get('mtsr_states')}st "
                    f"{row.get('mtsr_s')}s | agree={agree or '-'} ({note})")
        BL.tick()
    rows.close()
    left = survivors()
    if left:
        print(f"WARNING: solver processes still alive: {', '.join(left)}")


if __name__ == '__main__':
    main()
