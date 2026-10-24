#!/usr/bin/env python3
"""
tau_shape_rerun -- re-run MSR on the Table 3 instances whose reason has a loop
pin, to record the shape (prefix / loop length) of the reason trace tau, which
the benchmark CSVs do not store. Table 3's `smallest lasso' of a reason needs
it: a pin `x@t*' repeats every |loop of tau| steps (lasso_size.py).

    "$SPOT_ENV_PY" bench/paper/tau_shape_rerun.py        (after make_tables.py)
    "$SPOT_ENV_PY" bench/paper/tau_shape_rerun.py --list

Same code and settings as the benchmark: msr_bench.measure() with the model
checker whose answer Table 3 uses for that instance (bench_figs.msr_best),
timeout 120 s, wall limit 130 s, HyperQB defaults (k=None, pes). The only
addition is that reason_of() also records len(tau_prefix), len(tau_cycle).

Output: <PAPER_OUT>/tau_shape_rerun.csv (default bench/paper/out/), one row per
instance, with `same_reason' = whether the re-run returned the reason stored
in the CSV; the recorded one is results/stats/tau_shape_rerun.csv. Resumable
(skips instances already in the output). Reads the view make_tables.py builds
(out/work/view_ip3), so run make_tables.py once first.
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402

if not os.path.isdir(os.path.join(paths.VIEW, "MSR")):
    sys.exit(f"no results view at {paths.VIEW}; run bench/paper/make_tables.py first")
os.environ.setdefault("BENCH_STATS", paths.SHIPPED_STATS)
sys.path.insert(0, paths.BENCH_SCRIPTS)

import msr_bench as MB          # noqa: E402
import bench_figs as BF         # noqa: E402

CORPUS = paths.CORPUS_IP3
OUT = os.path.join(paths.OUT, "tau_shape_rerun.csv")
FIELDS = ["instance", "model", "P", "L", "status", "wall_s", "reason",
          "tau_prefix_len", "tau_cycle_len", "stored_reason", "same_reason", "err"]

_shape = {}
_orig = MB.reason_of


def reason_of(result):
    td = result.get("table_data") or {}
    _shape["p"] = len(td.get("tau_prefix", []))
    _shape["c"] = len(td.get("tau_cycle", []))
    return _orig(result)


MB.reason_of = reason_of


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print the instances and exit")
    a = ap.parse_args()
    todo = [n for n in BF.INST if BF.EH[n]["status"] == "OK" and BF.msr_best(n)
            and "*" in BF.msr_best(n)[1]["reason"]]
    if a.list:
        print("\n".join(todo))
        print(f"{len(todo)} instances with a loop pin")
        return
    os.makedirs(paths.OUT, exist_ok=True)
    done = set()
    if os.path.exists(OUT):
        done = {r["instance"] for r in csv.DictReader(open(OUT))}
    new = not os.path.exists(OUT)
    # AutoHyper writes aut1.hoa / autRes.hoa into its working directory
    cwd = os.path.join(paths.OUT, "work", "tau_shape_cwd")
    os.makedirs(cwd, exist_ok=True)
    os.chdir(cwd)
    f = open(OUT, "a", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if new:
        w.writeheader()
    print(f"{len(todo)} instances with a loop pin, {len(done)} done; writing {OUT}", flush=True)
    for i, n in enumerate(todo, 1):
        if n in done:
            continue
        stored = BF.msr_best(n)[1]
        model = stored["model"]
        _shape.clear()
        row = MB.measure(os.path.join(CORPUS, n + ".txt"), model, 120, None, "pes", 130)
        out = {"instance": n, "model": model, "P": row.get("P"), "L": row.get("L"),
               "status": row.get("status"), "wall_s": row.get("wall_s"),
               "reason": row.get("reason", ""), "tau_prefix_len": _shape.get("p", ""),
               "tau_cycle_len": _shape.get("c", ""), "stored_reason": stored["reason"],
               "same_reason": row.get("reason", "") == stored["reason"],
               "err": row.get("err", "")}
        w.writerow(out)
        f.flush()
        print(f"[{i}/{len(todo)}] {n} {model} {out['status']} tau={out['tau_prefix_len']}+"
              f"{out['tau_cycle_len']} same={out['same_reason']} {out['wall_s']}s", flush=True)
    f.close()


if __name__ == "__main__":
    main()
