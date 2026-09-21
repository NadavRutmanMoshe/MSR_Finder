#!/usr/bin/env python3
"""
cats_check_agreement -- do \\tool and CATS agree about CATS's own candidate?

    ~/micromamba/envs/spot/bin/python3 cats_check_agreement.py
    -> results/MTSR_CATS_check/agreement.csv          (~40s for 817 instances)

WHY THERE IS SOMETHING TO COMPARE AFTER ALL
-------------------------------------------
mtsr_cats_check_bench.py says a runtime-only comparison is all that is available
there, and for the two tools' OUTPUTS that is right: CATS verifies a candidate,
we synthesize one, and the objects are not the same kind. But both tools can be
asked about ONE shared object -- the candidate CATS was handed:

    CATS asks   is phi_C an ACTUAL cause of the effect on this lasso?
    we ask      is phi_C SUFFICIENT, i.e. L(phi_C) subset-of L(MTSR)?

MTSR's language is exactly the input sequences that force the effect, so the
second question is language inclusion and spot decides it. That makes a real
agreement column, and a disagreement is a FINDING rather than a bug: actual
causality is counterfactual (what would have had to be different), sufficiency
is a guarantee (what may never be different), and neither implies the other.

Measured on the corpus: they agree on 631 of 817, and every disagreement is in
one direction -- 186 candidates are sufficient without being actual causes,
while nothing CATS calls a cause fails our sufficiency check.
"""
import collections
import csv
import os
import sys
import time

import bench_lib as BL
from bench_lib import RESULTS, SYNTCOMP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spot        # noqa: E402
import mtsr_min    # noqa: E402

FIELDS = ["instance", "cats_status", "cats_s", "mtsr_s", "sufficient", "agree",
          "note"]


def mtsr_automaton(path):
    """L(A) = the input sequences that guarantee the effect."""
    inputs, effect, hoa = mtsr_min.parse_instance(path)
    prod = spot.product(spot.automaton(hoa),
                        spot.translate(spot.formula(effect)))
    proj, _ = mtsr_min.project_out_outputs(prod, inputs)
    return proj


def main():
    BL.install_sigint()
    src = os.path.join(RESULTS, "MTSR_CATS_check", "cats_check.csv")
    mt = {r["instance"]: r for r in
          csv.DictReader(open(os.path.join(RESULTS, "MTSR_CATS_check",
                                           "mtsr.csv"), newline=""))}
    rows = [r for r in csv.DictReader(open(src, newline=""))
            if r["cats_status"] in ("IS_CAUSE", "NO_CAUSE")]
    out = BL.Rows(os.path.join(RESULTS, "MTSR_CATS_check", "agreement.csv"),
                  FIELDS)
    BL.banner(f"CATS candidate vs MTSR sufficiency ({len(rows)} instances)")
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        name = r["instance"]
        rec = {"instance": name, "cats_status": r["cats_status"],
               "cats_s": r["cats_s"],
               "mtsr_s": (mt.get(name) or {}).get("mtsr_s", ""),
               "sufficient": "", "agree": "", "note": ""}
        path = os.path.join(SYNTCOMP,
                            name if name.endswith(".txt") else name + ".txt")
        try:
            A = mtsr_automaton(path)
            C = spot.translate(spot.formula(r["cats_cause"]))
            suff = spot.product(C, spot.complement(A)).is_empty()
            rec["sufficient"] = suff
            rec["agree"] = (suff == (r["cats_status"] == "IS_CAUSE"))
        except Exception as e:
            rec["note"] = f"{type(e).__name__}: {e}"[:80]
        out.write(rec)
        BL.tick()
        BL.progress(i, len(rows), name, rec["note"] or
                    f"CATS={r['cats_status']} sufficient={rec['sufficient']}")
    out.close()
    print(f"\ndone in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
