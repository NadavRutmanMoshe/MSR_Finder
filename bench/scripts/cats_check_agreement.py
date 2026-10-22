#!/usr/bin/env python3
"""
cats_check_agreement -- do \\tool and CATS agree about CATS's own candidate?

    ~/micromamba/envs/spot/bin/python3 cats_check_agreement.py
    -> results/MTSR_CATS_check/agreement.csv

WHAT IS COMPARED
----------------
mtsr_cats_check_bench.py hands each instance's candidate phi_C to both tools:

    CATS asks   is phi_C an ACTUAL cause of the effect on this lasso?
    we ask      is phi_C THE MTSR?  -- the tool's own --check
                (MSR_Finder/scripts/cause_check.py: L(phi_C), restricted to the
                system's input words, equals L(MTSR)).  MTSR or NOT_MTSR,
                nothing in between (Nadav, 2026-10-11).

This script only joins the two answers the driver recorded (cats_check.csv and
mtsr.csv's `mtsr_verdict`); it decides nothing itself.  Until 2026-10-11 it
computed its own L(phi_C) subset-of L(MTSR) here, a sufficiency test that is not
what the tool checks.

`agree` is True when both say yes (actual cause, and the MTSR) or both say no.
The two notions differ, so a disagreement is a finding rather than a bug.
"""
import csv
import os

import bench_lib as BL
from bench_lib import RESULTS

FIELDS = ["instance", "cats_status", "cats_s", "mtsr_s", "is_mtsr", "agree",
          "note"]


def main():
    d = os.path.join(RESULTS, "ip3", "MTSR_CATS_check")
    mt = {r["instance"]: r for r in
          csv.DictReader(open(os.path.join(d, "mtsr.csv"), newline=""))}
    rows = [r for r in csv.DictReader(open(os.path.join(d, "cats_check.csv"),
                                           newline=""))
            if r["cats_status"] in ("IS_CAUSE", "NO_CAUSE")]
    out_path = os.path.join(d, "agreement.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            m = mt.get(r["instance"]) or {}
            verdict = m.get("mtsr_verdict", "")
            rec = {"instance": r["instance"], "cats_status": r["cats_status"],
                   "cats_s": r["cats_s"], "mtsr_s": m.get("mtsr_s", ""),
                   "is_mtsr": "", "agree": "", "note": ""}
            if verdict in ("MTSR", "NOT_MTSR"):
                rec["is_mtsr"] = verdict == "MTSR"
                rec["agree"] = rec["is_mtsr"] == (r["cats_status"] == "IS_CAUSE")
            else:
                rec["note"] = (f"no verdict: mtsr {m.get('mtsr_status', 'missing')}"
                               f" {verdict}").strip()
            w.writerow(rec)
    print(f"wrote {out_path} ({len(rows)} candidates CATS answered)")


if __name__ == "__main__":
    BL.install_sigint()
    main()
