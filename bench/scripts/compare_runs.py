#!/usr/bin/env python3
"""
compare_runs -- a re-run (scripts/rerun.py) against the recorded results,
instance by instance and tool by tool.

    python3 scripts/compare_runs.py                    # bench/rerun vs bench/results
    python3 scripts/compare_runs.py --rerun DIR -v     # also list every instance that differs

For every row of the re-run it finds the recorded row of the same instance and
compares the ANSWER, never the time:

    MSR (AutoHyper, HyperQB)   status, and |R| (pins, loop pins) when an MSR was found
    ExplainHyper               status, smallest and largest cause
    MTSR                       status, number of states, and the --check verdict
    CORP                       status, number of states, language equivalence with the MTSR
    CATS check                 the candidate (must be identical), CATS's verdict
    CATS sketch                status, the found cause's size, agreement with the MTSR
    handcrafted (Tables 4, 6)  as above, per named instance

Each comparison is one of
    same       the same answer
    timeout    one side ran out of time or memory: it timed out, or it used the
               whole time limit and returned what it had by then (HyperQB falls
               back from an MSR to a sufficient reason). A question of machine
               speed, not of the answer; both times shown (-v lists them)
    DIFFERENT  both answered, and the answers differ

and the times are summarized as the ratio re-run / recorded (median per tool),
which is how much faster or slower this machine is than the paper's.

Exit status: 0 when nothing is DIFFERENT, 1 otherwise.
"""
import argparse
import csv
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
# where re-runs go: bench/rerun, or $RERUN_OUT (the Docker image sets it to
# /shared/rerun, so a re-run in the container lands on the host)
RERUN = os.environ.get("RERUN_OUT") or os.path.join(BENCH, "rerun")

# (file, key column, answer columns, time column)
CHECKS = [
    ("MSR/msr_autohyper.csv", "instance", ["status", "size", "loop_size"], "wall_s", "MSR, AutoHyper"),
    ("MSR/msr_hyperqb.csv", "instance", ["status", "size", "loop_size"], "wall_s", "MSR, HyperQB"),
    ("MSR/explainhyper.csv", "instance", ["status", "cause_min", "cause_max"], "wall_s", "ExplainHyper"),
    ("MTSR_CORP/mtsr.csv", "instance", ["mtsr_status", "mtsr_states"], "mtsr_s", "MTSR"),
    ("MTSR_CORP/corp.csv", "instance", ["corp_status", "corp_states", "equiv"], "corp_s", "CORP"),
    ("MTSR_CATS_check/mtsr.csv", "instance", ["mtsr_status", "mtsr_verdict"], "mtsr_s", "MTSR --check"),
    ("MTSR_CATS_check/cats_check.csv", "instance", ["cats_cause", "cats_status"], "cats_s", "CATS check"),
]
FIXED = [
    ("MTSR_CATS_sketch/mtsr.csv", "name", ["mtsr_status", "mtsr_states"], "mtsr_s", "sketch: MTSR"),
    ("MTSR_CATS_sketch/cats_sketch.csv", "name", ["cats_status", "cats_size", "agree"], "cats_s", "sketch: CATS"),
    ("handcrafted/corp_curated.csv", "instance", ["corp_status", "corp_states", "mtsr_status", "mtsr_states", "equiv"],
     "mtsr_s", "Table 6 (CORP arbiters)"),
    ("handcrafted/cats_handcrafted.csv", "label", ["cats_status", "mtsr_status", "mtsr_states", "verdict"],
     "cats_s", "Table 4 (CATS hand-crafted)"),
]
TIMEOUTISH = ("TIMEOUT", "MO", "MEMOUT", "UNKNOWN_BOUNDED")


def rows(p, key):
    if not os.path.exists(p):
        return None
    with open(p, newline="") as f:
        return {r[key].removesuffix(".txt"): r for r in csv.DictReader(f)}


def timed_out(r):
    s = " ".join(str(r.get(c, "")) for c in r if c.endswith("status"))
    return any(t in s.split() for t in TIMEOUTISH) or "TIMED OUT" in (r.get("err") or "") \
        or "TIMED OUT" in (r.get("mtsr_err") or "")


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def compare(rec, new, cols, tcol, limit=None):
    """same / timeout / DIFFERENT. `timeout` when either side ran out of time:
    it timed out, or it used up the whole time limit and returned what it had
    by then (HyperQB falls back from MSR to SR at the limit)."""
    a = tuple((rec.get(c) or "").strip() for c in cols)
    b = tuple((new.get(c) or "").strip() for c in cols)
    if a == b:
        return "same", a, b
    if timed_out(rec) or timed_out(new):
        return "timeout", a, b
    if limit and any((num(r.get(tcol)) or 0) >= limit for r in (rec, new)):
        return "timeout", a, b
    return "DIFFERENT", a, b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rerun", default=RERUN)
    ap.add_argument("--recorded", default=os.path.join(BENCH, "results"))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    bad = 0
    print(f"re-run:   {a.rerun}\nrecorded: {a.recorded}\n")
    print(f"{'':30s} {'rows':>5s} {'same':>5s} {'timeout':>8s} {'DIFFERENT':>10s}   time re-run/recorded (median)")
    jobs = [(os.path.join(part, f), k, c, t, f"{lab} ({part}, |IP|{'<=3' if part == 'ip3' else '=4'})",
             120 if part == "ip3" else 300)
            for part in ("ip3", "ip4") for f, k, c, t, lab in CHECKS] + [j + (None,) for j in FIXED]
    details = []
    for rel, key, cols, tcol, label, limit in jobs:
        new = rows(os.path.join(a.rerun, rel), key)
        if not new:
            continue
        rec = rows(os.path.join(a.recorded, rel), key) or {}
        count = {"same": 0, "timeout": 0, "DIFFERENT": 0}
        ratios = []
        for k, r in new.items():
            if k not in rec:
                continue
            v, x, y = compare(rec[k], r, cols, tcol, limit)
            count[v] += 1
            ta, tb = num(rec[k].get(tcol)), num(r.get(tcol))
            if v == "same" and ta and tb and ta > 0.05:
                ratios.append(tb / ta)
            if v != "same":
                details.append((label, k, v, x, y, ta, tb))
        bad += count["DIFFERENT"]
        ratio = f"{st.median(ratios):.2f}x" if ratios else "-"
        print(f"{label:30s} {sum(count.values()):5d} {count['same']:5d} {count['timeout']:8d} "
              f"{count['DIFFERENT']:10d}   {ratio}")
    if details and (a.verbose or bad):
        print("\nnot the same (columns as listed in this file's header; recorded -> re-run):")
        for label, k, v, x, y, ta, tb in details:
            if v == "DIFFERENT" or a.verbose:
                print(f"  {v:9s} {label:30s} {k}\n            {x} {ta}s\n         -> {y} {tb}s")
    print("\nnothing DIFFERENT" if not bad else f"\n{bad} answers DIFFERENT -- please report them")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
