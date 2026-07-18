#!/usr/bin/env python3
"""
Multi-system builder for the per-quantifier AutoHyper approach.

Instead of one combined LTS that multiplexes every trace via the internal
`__MSR_TAU__` (r) and `__MSR_LASSO__` (q) variables, we emit three small,
purpose-built systems and let AutoHyper resolve each quantifier on its own:

  S1  build_pi_system    -> the actual trace pi   : the lasso, projected to IP
  S2  build_real_system  -> pi2 / pi3             : the HOA unrolled to LTS, no q, no r
  S3  build_cause_system -> tau / tau2            : a clique over the 2^IP input space

All scaffolding that used to live in the .hq formula (pi fixation, the q
loop-guess, the r sink separation) is now encoded structurally in the systems,
so the formula keeps only the real causality logic.

"""
import os
import re
import sys
import itertools

# Allow `import converter` regardless of where we're launched from.
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import converter  # noqa: E402  (parse_hoa, eval_cond, get_val_tuple, check_determinism)


# --------------------------------------------------------------------------- #
# Lasso parsing (the [lasso] block: its own AP list + Prefix + Loop)
# --------------------------------------------------------------------------- #
def _parse_sets(line):
    """'{0 1} {0}' -> [ {0,1}, {0} ]   (mirrors hyperLTL.parse_sets)."""
    sets = []
    for m in re.findall(r'\{([^}]*)\}', line):
        sets.append(set() if m.strip() == "" else set(int(x) for x in m.split()))
    return sets


def parse_lasso(filepath):
    """
    Extracts the lasso's own AP order plus the prefix/loop index-sets.
    Returns (lasso_aps, prefix_sets, loop_sets).
    """
    with open(filepath, "r") as f:
        content = f.read()

    m_ap = re.search(r'\[lasso\][^A]*AP:\s*((?:"[^"]+"\s*)+)', content)
    lasso_aps = re.findall(r'"([^"]+)"', m_ap.group(1)) if m_ap else []

    m_prefix = re.search(r'Prefix:\s*(.*)', content)
    prefix_sets = _parse_sets(m_prefix.group(1)) if m_prefix else []

    m_loop = re.search(r'Loop:\s*(.*)', content)
    loop_sets = _parse_sets(m_loop.group(1)) if m_loop else []

    return lasso_aps, prefix_sets, loop_sets


# --------------------------------------------------------------------------- #
# Shared LTS emission
# --------------------------------------------------------------------------- #
def _emit_lts(variables, init_ids, state_details, n_states):
    """
    Render an explicit-state system. `state_details[i]` is a dict
    {var_name: bool, ...} and `state_details` also carries '__succ__' as a
    set of successor ids -- handled separately to keep the var dict clean.
    """
    lines = []
    lines.append("Variables: " + " ".join(f'("{v}" Bool)' for v in variables))
    lines.append("Init: " + " ".join(map(str, sorted(init_ids))))
    lines.append("--BODY--")
    for i in range(n_states):
        det = state_details[i]
        assigns = " ".join(f'("{v}" {"true" if det[v] else "false"})' for v in variables)
        lines.append(f"State: {i} {{{assigns}}}")
        succ = sorted(det["__succ__"])
        lines.append(" ".join(map(str, succ)))
    lines.append("--END--")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# S1  -- pi : the lasso projected onto the inputs
# --------------------------------------------------------------------------- #
def build_pi_system(filepath):
    """
    A single infinite path that *is* the actual trace, over inputs only:

        prefix[0] -> prefix[1] -> ... -> loop[0] -> ... -> loop[L-1] -> loop[0]

    pi's sole role in MSR/SR is to supply the input sequence that tau selects
    from, so dropping the outputs is sound (and keeps this system tiny).

    Returns (lts_string, inputs).
    """
    inputs, aps, _start, _states = converter.parse_hoa(filepath)
    lasso_aps, prefix_sets, loop_sets = parse_lasso(filepath)

    if not loop_sets:
        raise ValueError("Lasso has no Loop; cannot build pi system.")

    # Map an index-set (over the lasso's own AP order) to {input: bool}.
    def to_input_assign(idx_set):
        det = {}
        for ip in inputs:
            if ip in lasso_aps:
                det[ip] = (lasso_aps.index(ip) in idx_set)
            else:
                det[ip] = False
        return det

    chain = list(prefix_sets) + list(loop_sets)           # state order along the path
    loop_start = len(prefix_sets)                          # index where the cycle begins

    state_details = {}
    for i, idx_set in enumerate(chain):
        det = to_input_assign(idx_set)
        # successor is the next state; the final loop state wraps to loop_start
        det["__succ__"] = {i + 1} if i < len(chain) - 1 else {loop_start}
        state_details[i] = det

    init_ids = {0}
    lts = _emit_lts(inputs, init_ids, state_details, len(chain))
    return lts, inputs


# --------------------------------------------------------------------------- #
# S2  -- pi2 / pi3 : the real HOA unrolled to an LTS (no q duplication, no sink)
# --------------------------------------------------------------------------- #
def build_real_system(filepath):
    """
    The plain unrolled HOA over *all* APs. This is converter.generate_lts
    stripped of the q geometric expansion and the r sink clique -- exactly the
    system on which the effect must be evaluated for the universal/existential
    runs pi2 and pi3.

    Runs the same determinism firewall as the original pipeline.
    Returns (lts_string, aps).
    """
    inputs, aps, start_state, states = converter.parse_hoa(filepath)
    outputs = [ap for ap in aps if ap not in inputs]

    converter.check_determinism(inputs, outputs, aps, states)

    input_combos = list(itertools.product([False, True], repeat=len(inputs)))
    output_combos = list(itertools.product([False, True], repeat=len(outputs)))

    discovered = {}          # (hoa_target, val_tuple) -> id
    state_details = {}
    next_id = 0
    queue = []
    init_ids = set()

    def ensure_state(target, val):
        nonlocal next_id
        key = (target, val)
        if key not in discovered:
            discovered[key] = next_id
            det = {ap: bool(val[j]) for j, ap in enumerate(aps)}
            det["__succ__"] = set()
            det["__hoa__"] = target
            state_details[next_id] = det
            queue.append(next_id)
            next_id += 1
        return discovered[key]

    # Init = first-hop targets of the HOA start state (same t=0 convention as
    # converter.generate_lts, so pi2's t=0 lines up with pi's prefix[0]).
    for cond, target in states.get(start_state, []):
        for i_vals in input_combos:
            for o_vals in output_combos:
                val = converter.get_val_tuple(inputs, outputs, aps, i_vals, o_vals)
                if converter.eval_cond(cond, val):
                    init_ids.add(ensure_state(target, val))

    # BFS unroll.
    while queue:
        cur = queue.pop(0)
        cur_hoa = state_details[cur]["__hoa__"]
        for cond, target in states.get(cur_hoa, []):
            for i_vals in input_combos:
                for o_vals in output_combos:
                    val = converter.get_val_tuple(inputs, outputs, aps, i_vals, o_vals)
                    if converter.eval_cond(cond, val):
                        nxt = ensure_state(target, val)
                        state_details[cur]["__succ__"].add(nxt)

    # Drop the bookkeeping key before emission.
    for det in state_details.values():
        det.pop("__hoa__", None)

    lts = _emit_lts(aps, init_ids, state_details, next_id)
    return lts, aps


# --------------------------------------------------------------------------- #
# S3  -- tau / tau2 : a clique over the 2^IP input space
# --------------------------------------------------------------------------- #
def build_cause_system(filepath):
    """
    One state per input valuation, fully connected (incl. self-loops). This
    lets tau/tau2 freely pick any subset of inputs at any timestep -- the
    candidate-cause space. Over inputs only; no outputs, no r, no q.

    Returns (lts_string, inputs).
    """
    inputs, _aps, _start, _states = converter.parse_hoa(filepath)
    input_combos = list(itertools.product([False, True], repeat=len(inputs)))

    state_details = {}
    n = len(input_combos)
    all_ids = set(range(n))
    for i, i_vals in enumerate(input_combos):
        det = {ip: bool(i_vals[j]) for j, ip in enumerate(inputs)}
        det["__succ__"] = set(all_ids)      # clique
        state_details[i] = det

    lts = _emit_lts(inputs, all_ids, state_details, n)
    return lts, inputs


if __name__ == "__main__":
    # Quick smoke test: dump the three systems for a given file.
    path = sys.argv[1] if len(sys.argv) > 1 else "../examples/example_1.txt"
    s1, ip = build_pi_system(path)
    s2, aps = build_real_system(path)
    s3, _ = build_cause_system(path)
    print(f"# inputs={ip}  aps={aps}")
    print(f"# S1 pi   ({len(s1.splitlines())} lines)\n{s1}\n")
    print(f"# S2 real ({len(s2.splitlines())} lines)\n{s2}\n")
    print(f"# S3 cause({len(s3.splitlines())} lines)\n{s3}\n")
