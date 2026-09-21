#!/usr/bin/env python3
"""
Resolve an ltlsynt strategy into a concrete, input-deterministic Mealy machine.

Why this is needed
------------------
ltlsynt emits a *strategy*, not an implementation: an edge may leave some
outputs unconstrained ("don't care"). Example (ltl2dba22, AP 0="acc" output,
1="p" input):

    State: 0
    [0&1] 0        # p=T  -> acc must be T
    [!1]  1        # p=F  -> acc is a don't-care

TraceMSR's `converter.check_determinism` counts, per input valuation, every
(edge, output-valuation) pair that fits. The `[!1]` edge fits with acc=T *and*
acc=F, so the firewall reports "2 possible paths" and rejects the system --
55 of 172 synthesized SYNTCOMP specs die exactly this way.

Resolving a don't-care to a fixed value is what any real implementation does
(ltlsynt's own AIGER back-end does it too): the result is a refinement of the
strategy, so it still satisfies the specification. This module picks, for every
(state, input valuation), the first admissible (edge, output valuation) pair in
a fixed order, yielding exactly 2^|IP| fully-labelled edges per state.

Caveat worth stating in any write-up: the choice of resolution is arbitrary
among winning implementations, and it does affect which causes exist. We fix the
order (edges as listed, outputs false-before-true) so runs are reproducible.
"""
import itertools
import os
import sys

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import converter  # noqa: E402


class IncompleteStrategy(Exception):
    """A state has an input valuation with no admissible successor at all."""


def canonicalize(inputs, aps, states):
    """states: {id: [(cond, target)]} -> {id: [(exact_cond, target)]}.

    Every returned condition is a full conjunction over all APs, so each input
    valuation has exactly one outgoing edge.
    """
    outputs = [ap for ap in aps if ap not in inputs]
    input_combos = list(itertools.product([False, True], repeat=len(inputs)))
    output_combos = list(itertools.product([False, True], repeat=len(outputs)))

    new_states = {}
    for sid, edges in states.items():
        chosen = []
        for i_vals in input_combos:
            pick = None
            for cond, target in edges:
                for o_vals in output_combos:
                    val = converter.get_val_tuple(inputs, outputs, aps, i_vals, o_vals)
                    if converter.eval_cond(cond, val):
                        pick = (val, target)
                        break
                if pick:
                    break
            if pick is None:
                raise IncompleteStrategy(
                    f"state {sid} has no successor for inputs "
                    + ", ".join(f"{inputs[k]}={'T' if i_vals[k] else 'F'}"
                                for k in range(len(inputs))))
            val, target = pick
            cond_str = "&".join(('' if v else '!') + str(i) for i, v in enumerate(val))
            chosen.append((cond_str, target))
        new_states[sid] = chosen
    return new_states


def emit_hoa(aps, start, states):
    """Render canonicalized states as the [system] block TraceMSR parses."""
    lines = [
        "HOA: v1",
        f"Start: {start}",
        "AP: %d %s" % (len(aps), " ".join(f'"{a}"' for a in aps)),
        "Acceptance: 0 t",
        "--BODY--",
    ]
    for sid in sorted(states):
        lines.append(f"State: {sid}")
        for cond, target in states[sid]:
            lines.append(f"[{cond}] {target}")
    lines.append("--END--")
    return "\n".join(lines)


if __name__ == "__main__":
    path = sys.argv[1]
    ins, aps, start, states = converter.parse_hoa(path)
    new = canonicalize(ins, aps, states)
    print(emit_hoa(aps, start, new))
