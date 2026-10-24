#!/usr/bin/env python3
"""
check_text -- the numbers the TEXT of the paper quotes (abstract, Section 5,
Appendix D), recomputed from the results. The tables and figures are checked
by make_tables.py; this covers the sentences.

    python3 check_text.py                  # from the recorded results
    python3 check_text.py --src ../rerun   # from a full re-run of yours

Each line: the claim as the paper words it, the number written there, the
number the data gives, and ok / MISMATCH. Counting rules are the paper's:
an MSR instance is solved if TraceMSR returns an MSR or proves that none exists
(AutoHyper's SAT_SR) within the timeout plus 10 s of model building (120 s for
|IP| <= 3, 300 s for |IP| = 4); HyperQB's SAT_SR never counts (its MSR query is
bounded, so it proves nothing).
"""
import argparse
import csv
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
# --src rerun = your re-run: bench/rerun, or $RERUN_OUT (the Docker image sets
# it to folders under /shared, which is on the host)
RERUN = os.environ.get("RERUN_OUT") or os.path.join(BENCH, "rerun")


def rows(path, key="instance"):
    with open(path, newline="") as f:
        return {r[key].removesuffix(".txt"): r for r in csv.DictReader(f)}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def answered(r, cap):
    ok = r["status"] == "SAT_MSR" or (r["status"] == "SAT_SR" and r.get("model") != "hyperqb")
    return ok and num(r["wall_s"]) is not None and num(r["wall_s"]) <= cap


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=os.path.join(BENCH, "results"))
    src = ap.parse_args().src
    src = RERUN if src == "rerun" else src
    out, bad = [], 0

    def claim(where, text, written, got):
        nonlocal bad
        good = str(written) == str(got)
        bad += not good
        out.append((where, text, written, got, good))

    P = {p: os.path.join(src, p) for p in ("ip3", "ip4")}
    ah = {p: rows(os.path.join(P[p], "MSR/msr_autohyper.csv")) for p in P}
    hq = {p: rows(os.path.join(P[p], "MSR/msr_hyperqb.csv")) for p in P}
    mt = {p: rows(os.path.join(P[p], "MTSR_CORP/mtsr.csv")) for p in P}
    co = {p: rows(os.path.join(P[p], "MTSR_CORP/corp.csv")) for p in P}
    cap = {"ip3": 130.0, "ip4": 310.0}
    n3, n4 = len(ah["ip3"]), len(ah["ip4"])
    systems = {n.split("__")[0] for p in P for n in ah[p]}

    def solved(p, c=None):
        c = c or cap[p]
        return [n for n in ah[p] if answered(ah[p][n], c) or answered(hq[p][n], c)]

    s3, s4 = len(solved("ip3")), len(solved("ip4"))
    # "within 120 s" is wall clock, strictly: lilydemo21__e04 answered at 129.5 s
    # (120 s of solver + model building) and is not among the 23
    s4_120 = len(solved("ip4", 120.0))
    m3 = sum(r["mtsr_status"] == "OK" for r in mt["ip3"].values())
    m4 = sum(r["mtsr_status"] == "OK" for r in mt["ip4"].values())
    claim("abstract", "instances", 987, n3 + n4)
    claim("abstract", "derived from ... SYNTCOMP systems", 197, len(systems))
    claim("abstract", "computes the MTSR for ... of the instances", 915, m3 + m4)
    claim("abstract", "an MSR, or a proof that none exists, for ... of them", 717, s3 + s4)
    claim("5.2", "solves ... of the 823 instances with |IP| <= 3", 669, s3)
    claim("5.2", "of the 823 instances with |IP| <= 3", 823, n3)
    claim("5.2", "and ... of the 164 instances with |IP| = 4 (300 s)", 48, s4)
    claim("5.2", "of the 164 instances with |IP| = 4", 164, n4)
    claim("5.2", "(... of them within 120 s)", 23, s4_120)

    # CATS check (|IP| <= 3)
    cc = rows(os.path.join(P["ip3"], "MTSR_CATS_check/cats_check.csv"))
    ccm = rows(os.path.join(P["ip3"], "MTSR_CATS_check/mtsr.csv"))
    sys.path.insert(0, HERE)
    import spot  # noqa: E402  (run this with the spot environment's python)
    kind = {}
    for n, r in cc.items():
        f = spot.formula(r["cats_cause"])
        kind[n] = ("const" if f.is_tt() or f.is_ff() else
                   "no-AP" if not spot.atomic_prop_collect(f) else "proper")
    proper = [n for n in cc if kind[n] == "proper"]
    both = [n for n in proper if cc[n]["cats_status"] in ("IS_CAUSE", "NO_CAUSE")
            and ccm[n]["mtsr_status"] == "OK"]
    claim("5.3.2.1", "randltl simplified the formula to one without inputs on ...", 325, len(cc) - len(proper))
    claim("5.3.2.1", "left with ... candidates", 498, len(proper))
    claim("5.3.2.1", "on ... of them, one of the tools timed out or failed", 30, len(proper) - len(both))
    claim("5.3.2.1", "MTSR is faster on ...", 446, sum(num(ccm[n]["mtsr_s"]) < num(cc[n]["cats_s"]) for n in both))
    claim("5.3.2.1", "of the ... candidates both tools answered", 468, len(both))
    claim("5.3.2.1", "average runtime ... s (MTSR)", "0.62", f"{st.mean(num(ccm[n]['mtsr_s']) for n in both):.2f}")
    claim("5.3.2.1", "against ... s (CATS)", "1.00", f"{st.mean(num(cc[n]['cats_s']) for n in both):.2f}")
    claim("5.3.2.1", "the ... hand-crafted instances of Table 1 of CATS", 11,
          len(rows(os.path.join(BENCH, "results", "curated", "cats_handcrafted.csv"), "label")))

    # CATS sketch
    sk_dir = os.path.join(src, "MTSR_CATS_sketch")
    if not os.path.isdir(sk_dir):
        sk_dir = os.path.join(BENCH, "results", "MTSR_CATS_sketch")
    sk, skm = rows(os.path.join(sk_dir, "cats_sketch.csv"), "name"), rows(os.path.join(sk_dir, "mtsr.csv"), "name")
    claim("5.3.2.2", "MTSR solves all ... instances", 100, sum(r["mtsr_status"] == "OK" for r in skm.values()))
    claim("5.3.2.2", "CATS solves ... of them within 120 s", 96, sum(r["cats_status"] == "FOUND" for r in sk.values()))
    claim("5.3.2.2", "both tools return the same language in ... of the 96", 83, sum(r["agree"] == "yes" for r in sk.values()))
    claim("5.3.2.2", "in the other ..., L(CATS) strictly inside L(MTSR)", 13,
          sum(r["compare_note"].startswith("CATS_SUBSET_OF_MTSR") for r in sk.values()))

    # CORP
    bc = [(p, n) for p in P for n in mt[p] if mt[p][n]["mtsr_status"] == "OK" and co[p][n]["corp_status"] == "OK"]
    claim("5.3.3", "both tools solve ... (of the 987) within the time", 902, len(bc))
    claim("5.3.3", "CORP is faster (... s", "0.25", f"{st.mean(num(co[p][n]['corp_s']) for p, n in bc):.2f}")
    claim("5.3.3", "vs. ... s on average)", "0.86", f"{st.mean(num(mt[p][n]['mtsr_s']) for p, n in bc):.2f}")
    claim("5.3.3", "larger automata (... states", "19.8", f"{st.mean(num(co[p][n]['corp_states']) for p, n in bc):.1f}")
    claim("5.3.3", "against ... on average)", "4.9", f"{st.mean(num(mt[p][n]['mtsr_states']) for p, n in bc):.1f}")
    cur = rows(os.path.join(BENCH, "results", "curated", "corp_curated.csv"))
    f4 = cur["full_4_GF"]
    claim("5.3.3", "full 4 with GF g0: CORP's automaton has ... states", 284, f4["corp_states"])
    claim("5.3.3", "before minimization, our automaton has ... states", 46, f4["mtsr_premin"])

    # Appendix D
    import json  # noqa: E402
    meta = json.load(open(os.path.join(BENCH, "results", "stats", "mtsr_ltl_meta.json")))
    ltl = list(csv.DictReader(open(os.path.join(BENCH, "results", "stats", "mtsr_ltl.csv"))))
    claim("App. D", "found an LTL formula for ... of the", 436, sum(1 for r in ltl if r["ltl_size"]))
    claim("App. D", "... automata", 913, len(ltl))
    claim("App. D", "searched up to size ... (|IP| <= 2)", 9, meta["reached"]["1"])

    def ok_both(p, n, c):
        return answered(ah[p][n], c), answered(hq[p][n], c)
    a = sum(ok_both(p, n, cap[p])[0] for p in P for n in ah[p])
    h = sum(ok_both(p, n, cap[p])[1] for p in P for n in ah[p])
    b = [(p, n) for p in P for n in ah[p] if all(ok_both(p, n, cap[p]))]
    claim("App. D", "AutoHyper solves ... of the 987 instances", 703, a)
    claim("App. D", "and HyperQB ...", 381, h)
    claim("App. D", "on the ... instances that both solve", 367, len(b))
    claim("App. D", "HyperQB is faster on ...", 240, sum(num(hq[p][n]["wall_s"]) < num(ah[p][n]["wall_s"]) for p, n in b))
    claim("App. D", "AutoHyper solves ... instances that HyperQB does not", 336, a - len(b))
    claim("App. D", "and HyperQB only ... that AutoHyper does not", 14, h - len(b))

    w = max(len(t) for _, t, *_ in out)
    for where, text, written, got, good in out:
        print(f"  {where:8s} {text:{w}s}  paper {str(written):>6s}  data {str(got):>6s}  "
              + ("ok" if good else "MISMATCH"))
    print(f"\n{len(out) - bad} of {len(out)} numbers in the text agree with the data")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
