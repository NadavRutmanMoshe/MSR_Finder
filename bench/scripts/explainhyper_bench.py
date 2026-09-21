#!/usr/bin/env python3
"""
explainhyper_bench -- the ExplainHyper side of the MSR comparison.

MUST run under the explainhyper environment python (py-aiger, pysat, satispy,
pygraphviz, spot):

    ~/micromamba/envs/explainhyper/bin/python3 explainhyper_bench.py \
        [--sample N] [--timeout 60]

    -> results/MSR/explainhyper.csv

WHAT IS BEING COMPARED, AND WHAT IS NOT
---------------------------------------
The two tools answer different questions about the same triple <S, pi, eff>:

  MSR           a MINIMAL SUFFICIENT REASON R -- a minimal set of (input, time)
                pairs of pi such that EVERY system trace agreeing with pi on R
                satisfies eff. One R, describing an infinite set of traces.
  ExplainHyper  the ACTUAL CAUSES C -- minimal sets of input events on pi whose
                flip, under some contingency, makes pi stop satisfying eff. A
                statement about the one trace, and there can be many.

So this is not a race between two implementations of one definition. The
comparable columns are |R| against the C sizes (subsets of the same N x IP on
the same trace) and the wall clocks.

EXPRESSIBILITY. Their front end cannot take every instance, and the two
limitations are theirs, not the theory's: `cube_ok` (their edge labels go
through satispy, which mis-parses a negated mixed and/or) and `moore_ok` (their
candidate cause is read off the state path, which presumes outputs are
determined by the state). Both are recorded per instance rather than silently
dropped, so the coverage number is in the data.

TIMING. `wall_s` is whole-process wall clock for their tool, which includes
their interpreter startup. `t_ctilde` and `t_all` are their own internal
timings, printed by their tool, and are the ones to quote against a solver time.
"""
import argparse
import csv
import glob
import os
import re
import subprocess
import sys
import time

import bench_lib as BL
from bench_lib import RESULTS, SYNTCOMP, TOOLS

import explainhyper_export as X

EXPLAIN = os.path.join(TOOLS, "ExplainHyper", "explainhyper")

FIELDS = [
    "instance", "effect", "sys_states", "inputs", "outputs", "P", "L",
    "cex_steps", "expressible", "skip_reason",
    "status", "gamma", "phi", "ctilde", "n_causes", "cause_min", "cause_max",
    "t_ctilde", "t_all", "wall_s", "causes", "err",
]

_RE_GAMMA = re.compile(r"Size of the counter example \(\|Gamma\|\) is: (\d+)")
_RE_PHI = re.compile(r"The size of the formula \(\|phi\|\) is (\d+)")
_RE_T0 = re.compile(r"for trace 0: (\[.*?\])\s*$", re.M)
_RE_TC = re.compile(r"time\(C tilde\)\):\s*\n\s*([0-9.eE+-]+)")
_RE_ALL = re.compile(r"All minimal causes \(forall C\): (\[.*?\])\s*$", re.M)
_RE_N = re.compile(r"Number of minimal causes \(# C\): (\d+)")
_RE_TALL = re.compile(r"time\(forall C\)\) took:\s*\n\s*([0-9.eE+-]+)")


def load_gate(path):
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {os.path.splitext(r["file"])[0]: r["verdict"]
                for r in csv.DictReader(f) if r.get("file")}


def measure(path, workdir, timeout):
    name = os.path.splitext(os.path.basename(path))[0]
    row = {"instance": name, "err": "", "skip_reason": ""}
    try:
        meta = X.export(path, workdir, "inst")
    except Exception as e:
        row.update(expressible="no", status="EXPORT_FAILED",
                   skip_reason=f"{type(e).__name__}: {e}"[:150])
        return row
    row.update(effect=meta["effect"], sys_states=meta["n_states"],
               inputs=meta["n_inputs"], outputs=meta["n_outputs"],
               cex_steps=meta["cex_steps"], P=meta["lasso_prefix"],
               L=meta["lasso_loop"])
    if not meta.get("cube_ok") or not meta.get("moore_ok"):
        row.update(expressible="no", status="UNSUPPORTED",
                   skip_reason=("non-cube edge label on the counterexample path"
                                if not meta.get("cube_ok")
                                else "Mealy system (their causes are read off "
                                     "the state path)"))
        return row
    row["expressible"] = "yes"

    cmd = [sys.executable, os.path.join(EXPLAIN, "explain_hyper.py"),
           "-aag", os.path.join(workdir, "inst.aag"),
           "-cex", os.path.join(workdir, "inst.cex"),
           "-f", meta["formula"],
           "-outName", os.path.join(workdir, "out"),
           "-stateMachine", os.path.join(workdir, "inst.dot"),
           "-traceProperty"]
    t0 = time.perf_counter()
    try:
        out, wall = BL.run(cmd, timeout, cwd=EXPLAIN)
    except BL.Timeout as t:
        row.update(status="TIMEOUT", wall_s=t.args[0])
        return row
    row["wall_s"] = wall
    n = _RE_N.search(out)
    if n is None:
        last = [l for l in out.strip().splitlines() if l.strip()]
        row.update(status="ERROR", err=(last[-1][:150] if last else "no output"))
        return row
    causes = eval(_RE_ALL.search(out).group(1)) if _RE_ALL.search(out) else []
    sizes = [len(c) for c in causes]
    ct = eval(_RE_T0.search(out).group(1)) if _RE_T0.search(out) else []
    g, ph = _RE_GAMMA.search(out), _RE_PHI.search(out)
    tc, ta = _RE_TC.search(out), _RE_TALL.search(out)
    row.update(
        status="OK" if sizes else "NO_CAUSE",
        gamma=int(g.group(1)) if g else "",
        phi=int(ph.group(1)) if ph else "",
        ctilde=len(ct), n_causes=int(n.group(1)),
        cause_min=min(sizes) if sizes else "",
        cause_max=max(sizes) if sizes else "",
        t_ctilde=round(float(tc.group(1)), 4) if tc else "",
        t_all=round(float(ta.group(1)), 4) if ta else "",
        # Their causes as data: (AP, step, value, trace). Kept whole so the
        # comparison against R can be made later without re-running them.
        causes=str(causes)[:600])
    return row


def main():
    ap = argparse.ArgumentParser(description="ExplainHyper over SYNTCOMP.")
    ap.add_argument("--corpus", default=SYNTCOMP)
    ap.add_argument("--out", default=os.path.join(RESULTS, "MSR",
                                                  "explainhyper.csv"))
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--no-gate", action="store_true")
    args = ap.parse_args()
    BL.install_sigint()

    workdir = args.workdir or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), f"eh_bench_{os.getpid()}")
    os.makedirs(workdir, exist_ok=True)

    rows = BL.Rows(args.out, FIELDS)
    gate = load_gate(os.path.join(RESULTS, "gate.csv"))
    paths = sorted(glob.glob(os.path.join(args.corpus, "*.txt")))
    if gate and not args.no_gate:      # gate first: see msr_bench for why
        paths = [p for p in paths
                 if gate.get(os.path.splitext(os.path.basename(p))[0]) == "OK"]
    paths = BL.pick(paths, args.sample, args.seed)

    BL.banner(f"ExplainHyper  ->  {os.path.relpath(args.out, RESULTS)}")
    todo = [p for p in paths
            if os.path.splitext(os.path.basename(p))[0] not in rows.done]
    print(f"{len(paths)} gated instances, {len(rows.done)} already done, "
          f"{len(todo)} to run\n", flush=True)
    for i, p in enumerate(todo, 1):
        row = measure(p, workdir, args.timeout)
        rows.write(row)
        BL.progress(i, len(todo), row["instance"],
                    f"{row.get('status', ''):<14} #C={row.get('n_causes', '')} "
                    f"{row.get('wall_s', '')}s")
        BL.tick()
    rows.close()


if __name__ == "__main__":
    main()
