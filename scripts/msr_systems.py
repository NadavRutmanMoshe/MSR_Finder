#!/usr/bin/env python3
"""
Builder for the CliqueQ system -- the carrier of the `pi` quantifier.

`q` is the lasso-loop marker `__MSR_LASSO__`; duplicating every input valuation
into q=False/q=True lets `pi` be fixated to the user's actual trace *in the
formula* (msr_formulas builds that fixation), while the system itself stays a
tiny inputs-only clique: pi's only job in the MSR/SR queries is to supply the
input sequence that tau pins against, so outputs are unnecessary.

Reuses system_builder._emit_lts and converter.parse_hoa.
"""
import os
import sys
import itertools

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import converter          # noqa: E402
import system_builder     # noqa: E402  (reuse _emit_lts)

LASSO_VAR = "__MSR_LASSO__"     # q


def build_cause_system_with_q(filepath):
    """CliqueQ: one state per input valuation, q-duplicated (2^IP * 2 states),
    fully connected (incl. q flips and self loops).  Lets pi be fixated to the
    lasso inputs via q."""
    inputs, _aps, _start, _states = converter.parse_hoa(filepath)
    input_combos = list(itertools.product([False, True], repeat=len(inputs)))

    state_details = {}
    idx = 0
    for iv in input_combos:
        for q in (False, True):
            det = {ip: bool(iv[j]) for j, ip in enumerate(inputs)}
            det[LASSO_VAR] = q
            state_details[idx] = det
            idx += 1

    all_ids = set(range(idx))
    for sid in all_ids:
        state_details[sid]["__succ__"] = set(all_ids)   # complete clique

    variables = [LASSO_VAR] + inputs
    lts = system_builder._emit_lts(variables, all_ids, state_details, idx)
    return lts, inputs


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../examples/example_1.txt"
    cq, ip = build_cause_system_with_q(path)
    print(f"# inputs={ip}")
    print(f"# CliqueQ: {len(cq.splitlines())} lines")
    print(cq)
