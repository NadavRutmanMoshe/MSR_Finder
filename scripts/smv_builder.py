#!/usr/bin/env python3
"""
NuSMV module builders -- the systems the HyperQB backend runs on.

HyperQB 2.0 is a *bounded* model checker over symbolic NuSMV models: one model
per trace quantifier, exactly the convention AutoHyper uses for its explicit
systems.  So the HyperQB path needs the same three roles the AutoHyper pipeline
has (msr_analyzer._layout), only symbolic:

  build_real_smv     REAL  (pi2/pi3)  one integer `st` variable (the HOA state)
                           + one free boolean VAR per *input*.  Outputs are
                           DEFINEd functions of (st, inputs) -- sound because
                           converter.check_determinism guarantees
                           input-determinism.  A DEFINEd `valid` flag marks the
                           steps whose letter enables an HOA edge; `G valid`
                           <=> the trace is a real system word, which is how
                           the formula keeps pi2/pi3 inside the system.
  build_pi_smv       PI    (pi)  the lasso as a counter `c : 0..P+L-1`, with
                           every input a DEFINE over c.  The module has exactly
                           ONE trace -- the user's lasso projected onto the
                           inputs -- so pi needs no fixation conjunct in the
                           formula at all.
  build_cause_smv    CAUSE (tau/tau2)  one free boolean per input: the whole
                           2^IP candidate-cause clique in two lines per input.
  build_pival_smv    PIVAL (PI_VAL)  the synchronous product lasso x HOA in one
                           module: counter c DEFINEs *all* APs, `st` follows
                           delta, and `valid` additionally checks the lasso's
                           outputs against the HOA's.  It has exactly one trace,
                           so PI_VAL collapses to a trivial check.

WHY pi IS A MODULE AND NOT A FORMULA (this is the whole reason the backend
works).  The AutoHyper pipeline pins pi to the lasso *in the formula*, with a
positively-asserted `G`.  Under HyperQB's pessimistic bounded semantics such a
`G` is satisfied only if a loop is found inside the unrolling bound; it never
is, so that encoding returns `unsat` at every bound, on every instance.
Putting the lasso into the transition relation instead is an EXACT restatement
-- every step is constrained by construction -- and it removes the `G`
entirely.  Do not "simplify" this back into a formula-side fixation.

The word convention matches converter.generate_lts: the valuation at t=0 is the
letter of the first edge taken from the HOA start state (here: st starts AT the
start state and the letter is read from it, which yields the same word set).
"""
import os
import re
import sys
import itertools

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import converter  # noqa: E402

# NuSMV identifier: starts with letter or _, then letters/digits/_/$/#/-/[/]/.
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_$#\-\[\].]*$')


def _check_names(names):
    bad = [n for n in names if not _IDENT_RE.match(n)]
    if bad:
        raise ValueError(f"AP name(s) {bad} are not valid NuSMV identifiers; "
                         f"the SMV pipeline cannot encode this file.")


def _safe(base, taken):
    """A name based on `base` that collides with nothing in `taken`."""
    n = base
    while n in taken:
        n += "_"
    return n


# --------------------------------------------------------------------------- #
# Lasso parsing -- same as system_builder.parse_lasso but with the fixed
# `Prefix:` regex ([^\S\n]* instead of \s*) so a truly-empty prefix does not
# slurp the following `Loop:` line (see the claude_scripts.txt bug log).
# --------------------------------------------------------------------------- #
def _parse_sets(line):
    out = []
    for m in re.findall(r'\{([^}]*)\}', line):
        out.append(set() if m.strip() == "" else set(int(x) for x in m.split()))
    return out


def parse_lasso(filepath):
    with open(filepath, "r") as f:
        content = f.read()
    m_ap = re.search(r'\[lasso\][^A]*AP:\s*((?:"[^"]+"\s*)+)', content)
    lasso_aps = re.findall(r'"([^"]+)"', m_ap.group(1)) if m_ap else []
    m_prefix = re.search(r'Prefix:[^\S\n]*(.*)', content)
    prefix_sets = _parse_sets(m_prefix.group(1)) if m_prefix else []
    m_loop = re.search(r'Loop:[^\S\n]*(.*)', content)
    loop_sets = _parse_sets(m_loop.group(1)) if m_loop else []
    return lasso_aps, prefix_sets, loop_sets


# --------------------------------------------------------------------------- #
# HOA -> symbolic transition tables
# --------------------------------------------------------------------------- #
def _enumerate_transitions(inputs, outputs, aps, start_state, states):
    """For every (state, input valuation): the unique enabled
    (output valuation, target) or None.  States are remapped to 0..N-1.
    Returns (idmap, input_combos, trans)."""
    ids = set(states.keys()) | {start_state}
    for trs in states.values():
        ids.update(t for _, t in trs)
    ids = sorted(ids)
    idmap = {sid: k for k, sid in enumerate(ids)}

    input_combos = list(itertools.product([False, True], repeat=len(inputs)))
    output_combos = list(itertools.product([False, True], repeat=len(outputs)))

    trans = {}
    for sid in ids:
        s = idmap[sid]
        for iv in input_combos:
            hit = None
            for cond, target in states.get(sid, []):
                for ov in output_combos:
                    val = converter.get_val_tuple(inputs, outputs, aps, iv, ov)
                    if converter.eval_cond(cond, val):
                        hit = (ov, idmap[target])
            trans[(s, iv)] = hit
    return idmap, input_combos, trans


def _cube(inputs, iv):
    """`i1 & !i2` for one input valuation (TRUE when there are no inputs)."""
    if not inputs:
        return "TRUE"
    return " & ".join(n if v else f"!{n}" for n, v in zip(inputs, iv))


def _cubes_or(inputs, ivs, n_total):
    """Disjunction of input cubes; collapses to TRUE when it covers all 2^IP."""
    if len(ivs) == n_total:
        return "TRUE"
    if not ivs:
        return "FALSE"
    if len(ivs) == 1:
        return _cube(inputs, ivs[0])
    return " | ".join(f"({_cube(inputs, iv)})" for iv in ivs)


def _delta_case_lines(st, inputs, input_combos, trans, n_states):
    """`next(st)` case lines: one per (state, target) group of input cubes."""
    lines = []
    for s in range(n_states):
        by_target = {}
        for iv in input_combos:
            hit = trans[(s, iv)]
            if hit is not None:
                by_target.setdefault(hit[1], []).append(iv)
        for tgt in sorted(by_target):
            cond = _cubes_or(inputs, by_target[tgt], len(input_combos))
            guard = f"({st} = {s})" if cond == "TRUE" else f"({st} = {s}) & ({cond})"
            lines.append(f"    {guard} : {tgt};")
    lines.append(f"    TRUE : {st};")     # unreachable under `G valid`
    return lines


def _grouped_expr(st, inputs, input_combos, trans, n_states, pick):
    """Disjunction over states of (st = s) & (cubes where pick(hit) is true).
    Returns 'TRUE'/'FALSE' when degenerate."""
    terms = []
    total = 0
    for s in range(n_states):
        ivs = [iv for iv in input_combos if trans[(s, iv)] is not None
               and pick(trans[(s, iv)])]
        total += len(ivs)
        if not ivs:
            continue
        cond = _cubes_or(inputs, ivs, len(input_combos))
        if cond == "TRUE":
            terms.append(f"({st} = {s})")
        else:
            terms.append(f"(({st} = {s}) & ({cond}))")
    if total == n_states * len(input_combos):
        return "TRUE"
    if not terms:
        return "FALSE"
    return " | ".join(terms)


# --------------------------------------------------------------------------- #
# REAL -- the symbolic HOA, for pi2 / pi3
# --------------------------------------------------------------------------- #
def build_real_smv(filepath):
    """Returns (smv_string, meta).  meta: state_var, valid_var, inputs,
    outputs, n_states.  Raises ValueError('...non-deterministic...') exactly
    like converter.generate_lts (same firewall)."""
    inputs, aps, start_state, states = converter.parse_hoa(filepath)
    _check_names(aps)
    outputs = [ap for ap in aps if ap not in inputs]
    converter.check_determinism(inputs, outputs, aps, states)
    idmap, input_combos, trans = _enumerate_transitions(
        inputs, outputs, aps, start_state, states)
    n = len(idmap)

    st = _safe("st", set(aps))
    valid = _safe("valid", set(aps))

    lines = ["MODULE main", "VAR", f"  {st} : 0..{n - 1};"]
    for ip in inputs:
        lines.append(f"  {ip} : boolean;")
    lines.append("ASSIGN")
    lines.append(f"  init({st}) := {idmap[start_state]};")
    lines.append(f"  next({st}) := case")
    lines += _delta_case_lines(st, inputs, input_combos, trans, n)
    lines.append("  esac;")
    for ip in inputs:
        lines.append(f"  init({ip}) := {{TRUE, FALSE}};")
        lines.append(f"  next({ip}) := {{TRUE, FALSE}};")
    lines.append("DEFINE")
    for j, op in enumerate(outputs):
        expr = _grouped_expr(st, inputs, input_combos, trans, n,
                             pick=lambda hit, j=j: hit[0][j])
        lines.append(f"  {op} := {expr};")
    enabled = _grouped_expr(st, inputs, input_combos, trans, n,
                            pick=lambda hit: True)
    lines.append(f"  {valid} := {enabled};")
    # valid_const: the HOA is input-complete, every trace is a real system
    # word, and the formula can drop the `G valid` guard entirely (each guard
    # atom doubles the alphabet of the automata spot complements).
    meta = {"state_var": st, "valid_var": valid, "inputs": inputs,
            "outputs": outputs, "n_states": n,
            "valid_const": enabled == "TRUE"}
    return "\n".join(lines) + "\n", meta


# --------------------------------------------------------------------------- #
# PI -- the lasso as a counter, projected to inputs (for the "smv" layout)
# --------------------------------------------------------------------------- #
def build_pi_smv(filepath):
    """Returns (smv_string, meta).  meta: counter_var, chain (list of
    frozensets of true inputs per position, prefix then loop), loop_start."""
    inputs, _aps, _start, _states = converter.parse_hoa(filepath)
    _check_names(inputs)
    lasso_aps, prefix_sets, loop_sets = parse_lasso(filepath)
    if not loop_sets:
        raise ValueError("Lasso has no Loop; cannot build pi system.")

    def true_inputs(idx_set):
        return frozenset(ip for ip in inputs
                         if ip in lasso_aps and lasso_aps.index(ip) in idx_set)

    chain = [true_inputs(s) for s in list(prefix_sets) + list(loop_sets)]
    L = len(chain)
    loop_start = len(prefix_sets)
    c = _safe("c", set(inputs))

    lines = ["MODULE main", "VAR", f"  {c} : 0..{L - 1};",
             "ASSIGN", f"  init({c}) := 0;"]
    if L == 1:
        lines.append(f"  next({c}) := 0;")
    else:
        lines.append(f"  next({c}) := case ({c} = {L - 1}) : {loop_start}; "
                     f"TRUE : {c} + 1; esac;")
    lines.append("DEFINE")
    for ip in inputs:
        ts = [t for t in range(L) if ip in chain[t]]
        if not ts:
            expr = "FALSE"
        elif len(ts) == L:
            expr = "TRUE"
        else:
            expr = " | ".join(f"({c} = {t})" for t in ts)
        lines.append(f"  {ip} := {expr};")
    meta = {"counter_var": c, "chain": chain, "loop_start": loop_start,
            "inputs": inputs}
    return "\n".join(lines) + "\n", meta


# --------------------------------------------------------------------------- #
# CAUSE -- free input booleans (tau / tau2): the candidate-cause space
# --------------------------------------------------------------------------- #
def _free_bools_smv(varnames):
    lines = ["MODULE main", "VAR"]
    for v in varnames:
        lines.append(f"  {v} : boolean;")
    lines.append("ASSIGN")
    for v in varnames:
        lines.append(f"  init({v}) := {{TRUE, FALSE}};")
        lines.append(f"  next({v}) := {{TRUE, FALSE}};")
    return "\n".join(lines) + "\n"


def build_cause_smv(filepath):
    """The 2^IP clique: every input free at every step.  Returns (smv, meta)."""
    inputs, _aps, _start, _states = converter.parse_hoa(filepath)
    _check_names(inputs)
    if not inputs:
        # degenerate: a 1-state system with a dummy always-false variable
        return _free_bools_smv(["_nocause"]).replace("{TRUE, FALSE}", "FALSE"), \
            {"inputs": []}
    return _free_bools_smv(inputs), {"inputs": inputs}


# --------------------------------------------------------------------------- #
# PIVAL -- the synchronous product lasso x HOA (single trace), for PI_VAL
# --------------------------------------------------------------------------- #
def build_pival_smv(filepath):
    """Counter c DEFINEs *all* APs from the lasso; `st` follows the HOA on the
    lasso's inputs; `valid` = an edge is enabled AND the lasso's outputs match
    the HOA's.  `G valid & effect` on this single-trace module is PI_VAL.
    Returns (smv_string, meta)."""
    inputs, aps, start_state, states = converter.parse_hoa(filepath)
    _check_names(aps)
    outputs = [ap for ap in aps if ap not in inputs]
    converter.check_determinism(inputs, outputs, aps, states)
    idmap, input_combos, trans = _enumerate_transitions(
        inputs, outputs, aps, start_state, states)
    n = len(idmap)

    lasso_aps, prefix_sets, loop_sets = parse_lasso(filepath)
    if not loop_sets:
        raise ValueError("Lasso has no Loop; cannot build PI_VAL system.")

    def true_aps(idx_set):
        return frozenset(ap for ap in aps
                         if ap in lasso_aps and lasso_aps.index(ap) in idx_set)

    chain = [true_aps(s) for s in list(prefix_sets) + list(loop_sets)]
    L = len(chain)
    loop_start = len(prefix_sets)

    taken = set(aps)
    c = _safe("c", taken)
    st = _safe("st", taken)
    valid = _safe("valid", taken)
    sysop = {op: _safe(f"_sys_{op}", taken) for op in outputs}

    lines = ["MODULE main", "VAR",
             f"  {c} : 0..{L - 1};", f"  {st} : 0..{n - 1};",
             "ASSIGN", f"  init({c}) := 0;"]
    if L == 1:
        lines.append(f"  next({c}) := 0;")
    else:
        lines.append(f"  next({c}) := case ({c} = {L - 1}) : {loop_start}; "
                     f"TRUE : {c} + 1; esac;")
    lines.append(f"  init({st}) := {idmap[start_state]};")
    lines.append(f"  next({st}) := case")
    lines += _delta_case_lines(st, inputs, input_combos, trans, n)
    lines.append("  esac;")

    lines.append("DEFINE")
    # every AP (inputs and outputs) is the lasso's value at position c
    for ap in aps:
        ts = [t for t in range(L) if ap in chain[t]]
        if not ts:
            expr = "FALSE"
        elif len(ts) == L:
            expr = "TRUE"
        else:
            expr = " | ".join(f"({c} = {t})" for t in ts)
        lines.append(f"  {ap} := {expr};")
    # the HOA-computed outputs, for the agreement check
    for j, op in enumerate(outputs):
        expr = _grouped_expr(st, inputs, input_combos, trans, n,
                             pick=lambda hit, j=j: hit[0][j])
        lines.append(f"  {sysop[op]} := {expr};")
    enabled = _grouped_expr(st, inputs, input_combos, trans, n,
                            pick=lambda hit: True)
    agree = " & ".join(f"(({op} -> {sysop[op]}) & ({sysop[op]} -> {op}))"
                       for op in outputs)
    expr = enabled if not agree else \
        (agree if enabled == "TRUE" else f"({enabled}) & {agree}")
    lines.append(f"  {valid} := {expr};")

    meta = {"counter_var": c, "state_var": st, "valid_var": valid,
            "chain": chain, "loop_start": loop_start, "aps": aps,
            "valid_const": expr == "TRUE"}
    return "\n".join(lines) + "\n", meta


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../examples/example_1.txt"
    for name, fn in (("REAL", build_real_smv), ("PI", build_pi_smv),
                     ("CAUSE", build_cause_smv), ("PIVAL", build_pival_smv)):
        smv, meta = fn(path)
        print(f"--- {name} ({len(smv.splitlines())} lines) ---")
        print(smv)
