#!/usr/bin/env python3
"""
msr_bench -- the MSR side of the MSR/ExplainHyper comparison, one model per run.

    python3 msr_bench.py --model autohyper [--sample N] [--timeout 60]
    python3 msr_bench.py --model hyperqb   [--k N] [--semantics pes]

Runs the demo tool's MSR pipeline over the SYNTCOMP corpus and records, per
instance, the STATUS, the REASON (as data, not prose), its SIZE and the WALL
CLOCK. One CSV per model, because the model is the thing being varied:

    results/MSR/msr_autohyper.csv
    results/MSR/msr_hyperqb.csv

Every row carries a `model` column, so the two files concatenate into one table
and the "best time, and which model got it" column is a group-by afterwards.
Nothing here decides that -- the CSVs keep both measurements so the tables can
be built later without re-running anything, which is the point: these runs are
long.

WHAT `wall_s` IS. Build + solve (+ certify), measured in-process around the
pipeline call.
It excludes interpreter startup, which is the right choice for AutoHyper vs
HyperQB (identical overhead on both sides, so it would only add noise) and the
wrong one for a cross-tool ratio against ExplainHyper, which is a separate
process. `build_s` is broken out so the solver's share is visible, and so is
`certify_s`: the HyperQB backend does not trust its own `sat` (see
hyperqb_analyzer's docstring -- HyperQB's bounded `G` is optimistic in every
semantics, so a `forall`-quantified sufficiency claim is only checked as far as
the bound) and decides every witness exactly with spot before reporting it.
That check is part of the answer, so it is inside `wall_s`; it is broken out so
a solver-only comparison is still available.

THE WALL LIMIT. `--timeout` is handed to the pipeline, which applies it to each
solver call separately -- MSR, SR and PI_VAL can each use all of it, and
building the systems is not covered at all (a 20-AP instance once spent 3.7 h
there and was recorded as a SAT_MSR "answered" in 13,400 s). The tables count
an answer only inside timeout + 10 s of wall clock, so `--wall-limit` (default
exactly that) stops the whole instance there; it is recorded as
status=ERROR / err "... TIMED OUT ...", the same as a solver timeout.

THE REASON, AS DATA. `reason` is the R the tool found, rendered as a
comma-separated list of `<input>@<t>` pairs; a pin inside the lasso's loop gets
a `*` suffix, because it recurs at t, t+L, t+2L, ... and is not one position.
`size` counts the finite pins and `loop_size` the per-loop ones, so the two are
never silently added together. `reason_complete` is False when a bounded run
could only report a prefix of R (HyperQB); such a row's `size` is a lower bound
and must not be compared with an exact one.

THE GATE. Instances are filtered through results/gate.csv (see pi_gate.py):
a causal question is only well posed when the effect actually occurred on the
trace, and on this corpus most instances fail that. Ungated rows would measure
the tools' rejection paths. `--no-gate` runs everything anyway.
"""
import argparse
import csv
import glob
import os
import signal
import sys
import time

import bench_lib as BL
from bench_lib import RESULTS, SYNTCOMP

sys.path.insert(0, BL._DEMO_SCRIPTS)
import converter            # noqa: E402
import hyperqb_analyzer     # noqa: E402
import msr_analyzer         # noqa: E402
import proc_guard           # noqa: E402

FIELDS = [
    "instance", "model", "effect", "sys_states", "inputs", "outputs", "P", "L",
    "status", "size", "loop_size", "reason", "reason_complete",
    "k", "semantics", "build_s", "certify_s", "wall_s", "err",
]


def load_gate(path):
    """instance -> verdict, from pi_gate.py's CSV. Empty dict if not run yet."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {os.path.splitext(r["file"])[0]: r["verdict"]
                for r in csv.DictReader(f) if r.get("file")}


def reason_of(result):
    """(size, loop_size, reason string) for the R a run reported."""
    td = result.get("table_data") or {}
    allowed = set(result.get("cause_vars") or [])
    fixed, loop = [], []
    for t, step in enumerate(td.get("tau_prefix", [])):
        fixed += [f"{v}@{t}" for v in sorted(step & allowed)]
    base = len(td.get("tau_prefix", []))
    for i, step in enumerate(td.get("tau_cycle", [])):
        loop += [f"{v}@{base + i}*" for v in sorted(step & allowed)]
    return len(fixed), len(loop), ",".join(fixed + loop)


class _WallLimit(BaseException):
    """Raised from SIGALRM. A BaseException, so the pipelines' `except
    Exception` handlers do not swallow it; their `finally` blocks (temp dirs,
    proc_guard's group kill of a running solver) still run on the way out."""


def _on_alarm(_sig, _frm):
    raise _WallLimit()


def measure(path, model, timeout, k, semantics, wall_limit=None):
    inputs, aps, _s, states = converter.parse_hoa(path)
    row = {"instance": os.path.splitext(os.path.basename(path))[0],
           "model": model, "sys_states": len(states),
           "inputs": len(inputs), "outputs": len(aps) - len(inputs),
           "k": "", "semantics": "", "err": ""}
    from smv_builder import parse_lasso
    _a, pfx, lp = parse_lasso(path)
    row["P"], row["L"] = len(pfx), len(lp)

    if model == "hyperqb":
        pipe = hyperqb_analyzer.MSR
        pipe.K, pipe.SEMANTICS = k, semantics
        row["semantics"] = semantics
    else:
        pipe = msr_analyzer.MSR
    pipe.TIMEOUT_SECONDS = timeout

    use_alarm = bool(wall_limit) and hasattr(signal, "SIGALRM")
    if use_alarm:
        prev = signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(wall_limit)
    t0 = time.perf_counter()
    try:
        res = pipe.analyze_trace(path)
    except _WallLimit:
        proc_guard.reap_leaked_solvers()
        row.update(status="ERROR",
                   err=f"{model} TIMED OUT (wall > {wall_limit}s, whole instance)",
                   wall_s=round(time.perf_counter() - t0, 3))
        return row
    except Exception as e:
        row.update(status="EXCEPTION", err=f"{type(e).__name__}: {e}"[:200],
                   wall_s=round(time.perf_counter() - t0, 3))
        return row
    finally:
        if use_alarm:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, prev)
    row["wall_s"] = round(time.perf_counter() - t0, 3)
    row["effect"] = res.get("effect", "")
    row["status"] = res.get("status", "")
    row["build_s"] = round((res.get("timings") or {}).get("build", 0), 3)
    row["certify_s"] = round((res.get("timings") or {}).get("certify", 0), 3)
    row["err"] = (res.get("error_msg") or "")[:200]
    row["k"] = res.get("k", "")
    row["reason_complete"] = res.get("reason_complete", True)
    if res.get("status") in ("SAT_MSR", "SAT_SR"):
        row["size"], row["loop_size"], row["reason"] = reason_of(res)
    return row


def main():
    ap = argparse.ArgumentParser(description="MSR over SYNTCOMP, one model.")
    ap.add_argument("--model", required=True, choices=("autohyper", "hyperqb"))
    ap.add_argument("--corpus", default=SYNTCOMP)
    ap.add_argument("--out", default=None)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--wall-limit", type=int, default=None,
                    help="stop a whole instance (build + every solver call) "
                         "after this many seconds; default timeout + 10, the "
                         "tables' cut-off. 0 disables it")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=None, help="hyperqb bound")
    ap.add_argument("--semantics", default="pes")
    ap.add_argument("--no-gate", action="store_true",
                    help="run ungated instances too (they measure the "
                         "pipeline's rejection path, not its work)")
    args = ap.parse_args()
    BL.install_sigint()
    wall_limit = args.timeout + 10 if args.wall_limit is None else args.wall_limit

    out = args.out or os.path.join(RESULTS, "MSR", f"msr_{args.model}.csv")
    rows = BL.Rows(out, FIELDS)
    gate = load_gate(os.path.join(RESULTS, "gate.csv"))
    paths = sorted(glob.glob(os.path.join(args.corpus, "*.txt")))
    # Gate BEFORE sampling, so --sample N means N instances that actually run.
    # Both steps are deterministic, so every driver given the same N and seed
    # still measures the same instances and the CSVs join.
    if gate and not args.no_gate:
        paths = [p for p in paths
                 if gate.get(os.path.splitext(os.path.basename(p))[0]) == "OK"]
    paths = BL.pick(paths, args.sample, args.seed)

    BL.banner(f"MSR / {args.model}  ->  {os.path.relpath(out, RESULTS)}")
    todo = [p for p in paths
            if os.path.splitext(os.path.basename(p))[0] not in rows.done]
    print(f"{len(paths)} gated instances, {len(rows.done)} already done, "
          f"{len(todo)} to run\n", flush=True)
    for i, p in enumerate(todo, 1):
        row = measure(p, args.model, args.timeout, args.k, args.semantics,
                      wall_limit)
        rows.write(row)
        BL.progress(i, len(todo), row["instance"],
                    f"{row.get('status', ''):<16} |R|={row.get('size', '')} "
                    f"{row.get('wall_s', '')}s")
        BL.tick()
    rows.close()


if __name__ == "__main__":
    main()
