#!/usr/bin/env python3
"""
trace_check -- is the [lasso] a word of the [system]?  Decided exactly, in
plain Python, before any solver runs.

WHY IT RUNS FIRST
-----------------
The MSR and SR queries fixate pi over the INPUTS only and quantify pi2/pi3
over the system.  If the lasso is not a system trace, a cause can pin an input
to a value the system never takes there; then no system trace agrees with pi
on the pins, "every agreeing trace satisfies Phi" holds vacuously, and the
solver answers SAT.  Reproduced on a system where `F "o"` is impossible: the
pipeline reported "MSR found, R = {(i,0)}" -- R pinned the one value of `i`
the system cannot take at t=0 -- while analysis 2 in the same run said no
trace satisfies Phi.  The PI_VAL query that would have caught it only ran
after MSR and SR had both failed, i.e. never.

When the lasso IS a system trace, pi agrees with itself on every pin, so
sufficiency is never vacuous: a SAT then also shows Phi holds on pi, and
MSR/SR both UNSAT means it does not (PI_VAL still runs last to say which).

WHAT IS CHECKED
---------------
The HOA is read exactly as the pipelines read it (converter.parse_hoa): the
valuation at t is the label of the t-th edge taken from the start state, and
the acceptance condition is not consulted.  A system AP that the [lasso] AP
line does not list is left free, as the PI_VAL query leaves it; with no AP
line at all the indices refer to the system's AP order, as in msr_formulas.
The lasso is ultimately periodic, so the set of HOA states reachable after
each step is simulated until it repeats at the same loop position.  With no
acceptance to meet, the word is a system word iff that set never becomes
empty (every finite prefix has a run and the automaton is finitely branching,
so by Koenig's lemma an infinite run exists).
"""
import itertools
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import converter      # noqa: E402  (parse_hoa, eval_cond)
import smv_builder    # noqa: E402  (parse_lasso: the fixed empty-Prefix regex)


def check_lasso(path):
    """(True, "") when the [lasso] is a word of the [system], otherwise
    (False, one sentence saying at which step it leaves the system)."""
    _inputs, aps, start, states = converter.parse_hoa(path)
    lasso_aps, prefix, loop = smv_builder.parse_lasso(path)
    if not loop:
        return False, "the [lasso] has no Loop, so it is not an infinite trace"
    if not lasso_aps:
        lasso_aps = list(aps)
    free = [i for i, a in enumerate(aps) if a not in lasso_aps]

    def valuations(idx_set):
        base = [lasso_aps.index(a) in idx_set if a in lasso_aps else False
                for a in aps]
        out = []
        for bits in itertools.product([False, True], repeat=len(free)):
            v = list(base)
            for i, b in zip(free, bits):
                v[i] = b
            out.append(tuple(v))
        return out

    steps = [valuations(s) for s in list(prefix) + list(loop)]
    P, L = len(prefix), len(loop)
    memo = {}

    def enabled(cond, val):
        if (cond, val) not in memo:
            memo[(cond, val)] = converter.eval_cond(cond, val)
        return memo[(cond, val)]

    cur, seen, t = frozenset([start]), set(), 0
    while True:
        pos = t if t < P else P + (t - P) % L
        if t >= P:
            if (cur, pos) in seen:
                return True, ""
            seen.add((cur, pos))
        nxt = frozenset(tgt for s in cur for cond, tgt in states.get(s, [])
                        if any(enabled(cond, v) for v in steps[pos]))
        if not nxt:
            shown = ", ".join(f"{a}={'T' if v else 'F'}"
                              for a, v in zip(aps, steps[pos][0])
                              if a in lasso_aps)
            anyof = (f" (for every value of {', '.join(aps[i] for i in free)})"
                     if free else "")
            where = (f"state {next(iter(cur))}" if len(cur) == 1
                     else f"any of states {sorted(cur)}")
            return False, (f"at t={t} the [lasso] reads {{{shown}}}{anyof}, and "
                           f"no edge of the [system] from {where} allows it")
        cur, t = nxt, t + 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 trace_check.py <file.txt>")
        sys.exit(2)
    ok, why = check_lasso(sys.argv[1])
    print("the [lasso] is a word of the [system]" if ok else why)
    sys.exit(0 if ok else 1)
