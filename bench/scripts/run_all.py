#!/usr/bin/env python3
"""
run_all -- the whole benchmark suite, as one command, stoppable and resumable.

    ./run.sh                     # everything
    ./run.sh --only MSR          # one group
    ./run.sh --sample 50         # a subset of the corpus, same subset for all
    ./run.sh --list              # what would run, and what is already done

Press Ctrl-C whenever you like. The stage in flight loses at most the instance
it was on; everything already measured is on disk. Re-running the SAME command
picks up from there -- each driver reads its own CSV back and skips what it
already has.

THE STAGES, and why they are in this order:

  0  gate      PI_VAL over the corpus (pi_gate.py). Cheap, seconds, and every
               later stage filters on it: a causal question is only well posed
               when the effect actually occurred on the trace, and half of this
               corpus fails that. Ungated rows would measure the tools'
               rejection paths.
  1  MSR       our pipeline on AutoHyper, then on HyperQB. Two CSVs, each with
               a `model` column, so "best time and which model got it" is a
               group-by later and neither run has to be repeated.
  2  MSR/EH    ExplainHyper on the same gated instances. Different environment,
               so a separate process.
  3  MTSR/CORP synthesis against synthesis, on SYNTCOMP.
  4  MTSR/CATS CATS as a cause CHECKER, on SYNTCOMP.
  5  MTSR/CATS CATS as a cause SKETCHER, on the nexts benchmark (100 random
               systems built to the CATS paper's Table 2 rules).
  6  verify    re-checks the recorded HyperQB reasons. NOT a measurement:
               it runs last, writes its own CSV, and its time is never added to
               any tool's row.

ORPHANED SOLVERS are reaped between every instance and after every stage, and
each one is appended to results/leaks.log with the stage that leaked it. Only
processes that started AFTER this run began AND are outside its process tree
are touched, so a solver already running when the suite starts is never killed
and a solver a stage is currently using never is either. A leak reaching the
log is a BUG worth reporting -- the suite should not produce one.

Every stage is a separate process under the interpreter its tools need. Ctrl-C
reaches all of them because they stay in this terminal's process group.
"""
import argparse
import os
import subprocess
import sys
import time

import bench_lib as BL
from bench_lib import RESULTS, SPOT_PY, EXPLAIN_PY

_HERE = os.path.dirname(os.path.abspath(__file__))


def S(name):
    return os.path.join(_HERE, name)


def stages(args):
    """(group, label, argv, output-files-to-report) in run order."""
    common = []
    if args.sample:
        common += ["--sample", str(args.sample), "--seed", str(args.seed)]
    out = []

    out.append(("gate", "PI_VAL gate over the corpus",
                [SPOT_PY, S("pi_gate.py"), BL.SYNTCOMP,
                 "--csv", os.path.join(RESULTS, "gate.csv"),
                 "-o", os.path.join(RESULTS, "gate_ok.txt"), "--quiet"],
                ["gate.csv"]))

    for model in ("autohyper", "hyperqb"):
        out.append(("MSR", f"MSR / {model}",
                    [sys.executable, S("msr_bench.py"), "--model", model,
                     "--timeout", str(args.msr_timeout)] + common,
                    [f"MSR/msr_{model}.csv"]))
    out.append(("MSR", "MSR / ExplainHyper",
                [EXPLAIN_PY, S("explainhyper_bench.py"),
                 "--timeout", str(args.msr_timeout)] + common,
                ["MSR/explainhyper.csv"]))

    out.append(("MTSR_CORP", "MTSR vs CORP",
                [SPOT_PY, S("mtsr_corp_bench.py"),
                 "--timeout", str(args.mtsr_timeout)] + common,
                ["MTSR_CORP/mtsr.csv", "MTSR_CORP/corp.csv"]))
    out.append(("MTSR_CATS_check", "MTSR vs CATS --check",
                [SPOT_PY, S("mtsr_cats_check_bench.py"),
                 "--timeout", str(args.mtsr_timeout)] + common,
                ["MTSR_CATS_check/mtsr.csv",
                 "MTSR_CATS_check/cats_check.csv"]))
    # The sketch benchmark has its own 100-instance corpus, so --sample (which
    # selects from SYNTCOMP) does not apply to it.
    out.append(("MTSR_CATS_sketch", "MTSR vs CATS --sketch",
                [SPOT_PY, S("mtsr_cats_sketch_bench.py"),
                 "--timeout", str(args.mtsr_timeout)],
                ["MTSR_CATS_sketch/mtsr.csv",
                 "MTSR_CATS_sketch/cats_sketch.csv"]))

    out.append(("verify", "verify the recorded HyperQB reasons (untimed)",
                [SPOT_PY, S("verify_msr.py"),
                 "--timeout", str(args.mtsr_timeout)],
                ["MSR/verify_hyperqb.csv"]))
    return out


def rows_in(path):
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        return max(0, sum(1 for _ in f) - 1)


def main():
    ap = argparse.ArgumentParser(
        description="Run the whole benchmark suite. Ctrl-C is safe; re-run to "
                    "resume.")
    ap.add_argument("--only", action="append", default=[],
                    help="run only these groups (gate, MSR, MTSR_CORP, "
                         "MTSR_CATS_check, MTSR_CATS_sketch, verify); "
                         "repeatable")
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--sample", type=int, default=0,
                    help="use N SYNTCOMP instances instead of all of them; the "
                         "same N for every stage, so the CSVs still join")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds per solver call, every stage (default 120)")
    ap.add_argument("--msr-timeout", type=int, default=None,
                    help="override --timeout for the MSR stages")
    ap.add_argument("--mtsr-timeout", type=int, default=None,
                    help="override --timeout for the MTSR stages")
    ap.add_argument("--cooldown", type=float, default=20,
                    help="seconds to pause for cooling (0 disables)")
    ap.add_argument("--cooldown-every", type=float, default=300,
                    help="pause after this many seconds of work")
    ap.add_argument("--stage-pause", type=float, default=60,
                    help="seconds between stages, so the machine is not hot "
                         "when the next tool starts being timed")
    ap.add_argument("--no-reap", action="store_true",
                    help="log orphaned solvers but do not kill them")
    ap.add_argument("--list", action="store_true",
                    help="show the plan and what is already on disk, run nothing")
    args = ap.parse_args()
    args.msr_timeout = args.msr_timeout or args.timeout
    args.mtsr_timeout = args.mtsr_timeout or args.timeout
    # Passed as environment so a driver started on its own honours them too.
    os.environ["BENCH_COOLDOWN_S"] = str(args.cooldown)
    os.environ["BENCH_COOLDOWN_EVERY_S"] = str(args.cooldown_every)
    # The reaper's ownership test: a solver counts as ours only if it started
    # after this pid did. Exported so every driver tests descent from the
    # SUITE, not from itself -- a driver is a child, and its own children are
    # legitimately running.
    os.environ["BENCH_SUITE_PID"] = str(os.getpid())
    os.environ["BENCH_SUITE_START"] = str(BL.SUITE_START)
    if args.no_reap:
        os.environ["BENCH_NO_REAP"] = "1"
        BL.REAP = False

    plan = [s for s in stages(args)
            if (not args.only or s[0] in args.only) and s[0] not in args.skip]

    if args.list:
        print(f"{BL._B}plan{BL._0}   (results under {RESULTS})")
        for group, label, _cmd, outs in plan:
            done = ", ".join(f"{o}: {rows_in(os.path.join(RESULTS, o))} rows"
                             for o in outs)
            print(f"  {group:<18} {label:<42} {BL._D}{done}{BL._0}")
        return 0

    print(f"{BL._D}reaping solver processes that start after now and end up "
          f"orphaned; anything already running is left alone. "
          f"Log: {os.path.relpath(BL.LEAK_LOG, BL.BENCH)}{BL._0}")
    pre = BL.survivors()
    if pre:
        print(f"{BL._D}  (already running, will not be touched: "
              f"{', '.join(pre)}){BL._0}")

    for i, (group, label, cmd, outs) in enumerate(plan, 1):
        if i > 1 and args.stage_pause > 0:
            # Between stages, not just between instances: the next tool's first
            # instances are timed on whatever thermal state the last one left,
            # and a throttled CPU makes a tool look slow for reasons that have
            # nothing to do with it.
            print(f"{BL._D}[cooling {args.stage_pause:.0f}s before the next "
                  f"stage]{BL._0}", flush=True)
            time.sleep(args.stage_pause)
        BL.banner(f"[{i}/{len(plan)}]  {label}")
        os.environ["BENCH_STAGE"] = label
        # Same process group on purpose: Ctrl-C in the terminal must reach the
        # child, which is what makes an interrupt a clean stop rather than an
        # orphaned solver. The drivers use proc_guard for their own children.
        rc = subprocess.run(cmd).returncode
        # The stage is over, so anything of ours that is still alive is by
        # definition an orphan: reap it here rather than letting it tax the
        # next stage's measurements.
        killed = BL.reap_orphans(stage=group)
        if killed:
            print(f"{BL._Y}[!] reaped {len(killed)} orphan"
                  f"{'s' if len(killed) > 1 else ''} left by this stage: "
                  f"{', '.join(killed)}{BL._0}\n"
                  f"{BL._D}    logged to "
                  f"{os.path.relpath(BL.LEAK_LOG, BL.BENCH)} -- worth "
                  f"reporting, a leak here is a bug.{BL._0}")
        left = BL.survivors()
        if left:
            print(f"{BL._D}[i] still running, but not ours to kill (they "
                  f"predate this run): {', '.join(left)}{BL._0}")
        if rc == 130:
            print(f"{BL._Y}stopped. Re-run the same command to continue.{BL._0}")
            return 130
        if rc != 0:
            print(f"{BL._R}[{label}] exited {rc} -- continuing with the next "
                  f"stage; its rows so far are kept.{BL._0}")
    BL.reap_orphans(stage="final")
    print(f"\n{BL._G}suite finished.{BL._0} Results:")
    for _g, _l, _c, outs in plan:
        for o in outs:
            print(f"  {o:<40} {rows_in(os.path.join(RESULTS, o))} rows")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{BL._Y}stopped. Re-run the same command to continue.{BL._0}")
        sys.exit(130)
