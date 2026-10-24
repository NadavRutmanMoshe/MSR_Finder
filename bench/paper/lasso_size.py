#!/usr/bin/env python3
"""
lasso_size -- size of an explanation as the SMALLEST LASSO that writes it down
(Nadav, 2026-10-14), for MSR vs ExplainHyper (Table 2 / Table 6).

An explanation fixes some inputs at some time steps to their values on pi.  As
an infinite word, position t carries the partial valuation {input: value} of
the events at t (empty where nothing is fixed).  Its size is the minimal
|u| + |v| with  word = u v^omega,  |v| >= 1.  Example: {(i, 0)} -> {i}{}^w = 2.

  MSR (ours): pin x@t      -> x fixed at time t once;
              pin x@t*     -> x fixed at t, t+c, t+2c, ... (c = |loop of tau|,
                              from tau_shape_rerun.py; tau's lasso is not in
                              the benchmark CSVs).  Values are pi's values.
  ExplainHyper: event (x, t, value, trace 0) -> x fixed at time t once.  Their
              counterfactual check flips the event on the lasso written out
              once and never sets a loop (explain_hyper.py, minimality():
              set_values only), so an event at a loop position is ONE event.
              With several minimal causes, the smallest is taken (as Table 2).

    BENCH_RESULTS=... BENCH_STATS=... ~/micromamba/envs/spot/bin/python3 bench/paper/lasso_size.py
"""
import ast
import csv
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
sys.path.insert(0, paths.TOOL_SCRIPTS)
import bench_figs as BF                 # noqa: E402
from system_builder import parse_lasso  # noqa: E402

CORPORA = [paths.CORPUS_IP3, paths.CORPUS_IP4]
CORPUS = CORPORA[0]
SHAPES = os.path.join(paths.STATS, "tau_shape_rerun.csv")
_SHAPES = None


def instance_path(n):
    for d in CORPORA:
        p = os.path.join(d, n + ".txt")
        if os.path.exists(p):
            return p
    raise FileNotFoundError(n)


def sizes(n, row):
    """(smallest lasso of our reason `row`, smallest lasso of ExplainHyper's
    smallest cause) for instance n.  A reason with a loop pin needs tau's loop
    length from tau_shape_rerun.csv; a missing one is an error, not a skip, so
    a table can never average over a silently smaller set (2026-10-14)."""
    global _SHAPES
    if _SHAPES is None:
        _SHAPES = ({r["instance"]: r for r in csv.DictReader(open(SHAPES))}
                   if os.path.exists(SHAPES) else {})
    # re-runs store tau's shape in the MSR CSV; the recorded runs did not, so
    # for them it comes from tau_shape_rerun.csv
    shape = row if row.get("tau_cycle_len") else _SHAPES.get(n)
    if "*" in row["reason"]:
        if not shape or shape["reason"] != row["reason"]:
            raise RuntimeError(f"{n}: reason with a loop pin but no matching tau shape; "
                               f"run tau_shape_rerun.py first")
    return msr_size(row, instance_path(n), shape), eh_size(n)


def smallest_lasso(word, pre, per):
    """word(t) for every t >= 0, ultimately periodic: periodic with period
    `per` from `pre` on.  Minimal p + l with word(t) == word(t + l) for t >= p."""
    horizon = pre + 2 * per + 2
    best = None
    for l in range(1, per + 1):
        for p in range(0, pre + per + 1):
            if best is not None and p + l >= best:
                break
            if all(word(t) == word(t + l) for t in range(p, horizon + l)):
                best = p + l
                break
    return best


def pi_value(path):
    aps, pfx, lp = parse_lasso(path)
    P, L = len(pfx), len(lp)

    def val(ap, t):
        s = pfx[t] if t < P else lp[(t - P) % L]
        return aps.index(ap) in s
    return val, P, L


def msr_size(row, path, shape):
    val, P, L = pi_value(path)
    fixed, loop = [], []
    for x in row["reason"].split(","):
        if not x:
            continue
        ap, pos = x.rsplit("@", 1)
        (loop if pos.endswith("*") else fixed).append((ap, int(pos.rstrip("*"))))
    c = int(shape["tau_cycle_len"]) if loop else 1

    def pinned(t):
        s = {ap for ap, tt in fixed if tt == t}
        s |= {ap for ap, tt in loop if t >= tt and (t - tt) % c == 0}
        return frozenset((ap, val(ap, t)) for ap in s)
    last = max([t for _, t in fixed + loop] + [0])
    pre = max(last, P) + 1
    per = math.lcm(c, L) if loop else 1
    return smallest_lasso(pinned, pre, per)


def eh_size(n):
    best = None
    for cause in ast.literal_eval(BF.EH[n]["causes"]):
        ev = {}
        for ap, t, v, tr in cause:
            if str(tr) == "0":
                ev.setdefault(t, set()).add((ap, v))

        def w(t, ev=ev):
            return frozenset(ev.get(t, ()))
        s = smallest_lasso(w, max(ev, default=0) + 1, 1)
        best = s if best is None else min(best, s)
    return best


def main():
    shapes = {r["instance"]: r for r in csv.DictReader(open(SHAPES))} if os.path.exists(SHAPES) else {}
    inst = [n for n in BF.INST if BF.EH[n]["status"] == "OK" and BF.msr_best(n)]
    R, C, miss, rows = [], [], [], []
    for n in inst:
        row = BF.msr_best(n)[1]
        if "*" in row["reason"] and (n not in shapes or shapes[n]["same_reason"] != "True"):
            miss.append(n)
            continue
        r = msr_size(row, os.path.join(CORPUS, n + ".txt"), shapes.get(n))
        c = eh_size(n)
        R.append(r); C.append(c); rows.append((n, r, c))
    print(f"{len(rows)} of {len(inst)} instances ({len(miss)} wait for tau's shape)")
    print(f"smallest lasso: avg |R| {st.mean(R):.2f}  avg |C| {st.mean(C):.2f}")
    print(f"R > C {sum(r > c for r, c in zip(R, C))}  R = C {sum(r == c for r, c in zip(R, C))}  "
          f"R < C {sum(r < c for r, c in zip(R, C))}")
    out = os.path.join(paths.STATS, "lasso_size.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["instance", "msr_lasso", "eh_lasso"])
        w.writerows(rows)
    print("wrote", out)


if __name__ == "__main__":
    main()
