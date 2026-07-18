#!/usr/bin/env python3
"""
HyperLTL formula generator for the MSR/SR queries.

Unlike multi (which puts the lasso in S1's structure and drops the fixation),
the variants fixate `pi` *in the formula* with the q marker -- exactly the
legacy `full_fixation` block -- but with NO `r` literals (the cause space is a
separate system) and with the slimmed causality core from hyperLTL_multi.

Two fixation scopes:
  scope="all"     -> fixate pi over ALL APs (idea 1: pi lives on the real HOA+q).
  scope="inputs"  -> fixate pi over INPUTS only (ideas 2/3: pi lives on an
                     inputs-only clique+q; the effect is only ever evaluated on
                     pi2/pi3 so pi needs no outputs).

The body (sufficiency + minimality) is byte-identical to hyperLTL_multi; only
the pi-fixation conjunct is prepended.  PI_VAL is not handled here (legacy path).
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
for p in (_HERE, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

LASSO_VAR = "__MSR_LASSO__"     # q


def _parse_lasso_and_inputs(filepath):
    with open(filepath, "r") as f:
        content = f.read()

    m_inputs = re.search(r'\[inputs\]\s+((?:"[^"]+"\s*)+)', content)
    inputs = re.findall(r'"([^"]+)"', m_inputs.group(1)) if m_inputs else []

    m_effect = re.search(r'\[effect\]\s+(.*)', content)
    effect = m_effect.group(1).strip() if m_effect else ""

    # lasso AP order (the [lasso] AP: line), falling back to the system AP line
    m_ap = re.search(r'\[lasso\][^A]*AP:\s*((?:"[^"]+"\s*)+)', content)
    if m_ap:
        lasso_aps = re.findall(r'"([^"]+)"', m_ap.group(1))
    else:
        m_sys = re.search(r'AP:\s*\d+\s+((?:"[^"]+"\s*)+)', content)
        lasso_aps = re.findall(r'"([^"]+)"', m_sys.group(1)) if m_sys else []

    inputs = [ip for ip in inputs if ip in lasso_aps]

    def _sets(line):
        out = []
        for m in re.findall(r'\{([^}]*)\}', line):
            out.append(set() if m.strip() == "" else set(int(x) for x in m.split()))
        return out

    m_prefix = re.search(r'Prefix:\s*(.*)', content)
    prefix_sets = _sets(m_prefix.group(1)) if m_prefix else []
    m_loop = re.search(r'Loop:\s*(.*)', content)
    loop_sets = _sets(m_loop.group(1)) if m_loop else []

    return inputs, effect, lasso_aps, prefix_sets, loop_sets


def _project_to_inputs(idx_set, lasso_aps, inputs):
    """Map a lasso-AP-indexed set to an inputs-indexed set (drop outputs)."""
    out = set()
    for j, ip in enumerate(inputs):
        if ip in lasso_aps and lasso_aps.index(ip) in idx_set:
            out.add(j)
    return out


def _rename_effect(effect_str, trace_name):
    return re.sub(r'"([^"]+)"', r'{"\1"_' + trace_name + '}', effect_str)


def _build_fixation(fix_aps, prefix_sets, loop_sets):
    """Replicates hyperLTL.generate_hq's q-based pi fixation, over `fix_aps`
    (sets must already be indexed relative to fix_aps)."""
    def build_state(active, q_val):
        parts = []
        for i, ap in enumerate(fix_aps):
            parts.append(f'"{ap}"_pi' if i in active else f'!"{ap}"_pi')
        if q_val is True:
            parts.append(f'"{LASSO_VAR}"_pi')
        elif q_val is False:
            parts.append(f'!"{LASSO_VAR}"_pi')
        return " & ".join(parts) if parts else "true"

    lasso_block = f'"{LASSO_VAR}"_pi\n    & G (\n'
    for i, l_state in enumerate(loop_sets):
        sc = build_state(l_state, None)
        if i == 0:
            step = f'      ("{LASSO_VAR}"_pi -> ({sc}))'
        elif i == 1:
            step = f'      ("{LASSO_VAR}"_pi -> X(!"{LASSO_VAR}"_pi & {sc}))'
        else:
            xs = "X" * i
            step = f'      ("{LASSO_VAR}"_pi -> {xs}(!"{LASSO_VAR}"_pi & {sc}))'
        lasso_block += step + '\n      &\n'
    xs_loop = "X" * len(loop_sets)
    lasso_block += f'      ("{LASSO_VAR}"_pi -> {xs_loop}("{LASSO_VAR}"_pi))\n    )'

    full = lasso_block
    for p_state in reversed(prefix_sets):
        sc = build_state(p_state, False)
        full = f'{sc}\n  &\n  X(\n    {full}\n  )'
    return full


def generate_hq_variant(filepath, mode="MSR", scope="inputs"):
    """mode in {SR, MSR}; scope in {all, inputs}.  Returns the .hq string."""
    inputs, effect, lasso_aps, prefix_sets, loop_sets = _parse_lasso_and_inputs(filepath)

    if scope == "all":
        fix_aps = lasso_aps
        pfx, lp = prefix_sets, loop_sets
    elif scope == "inputs":
        fix_aps = inputs
        pfx = [_project_to_inputs(s, lasso_aps, inputs) for s in prefix_sets]
        lp = [_project_to_inputs(s, lasso_aps, inputs) for s in loop_sets]
    else:
        raise ValueError(f"bad scope {scope!r}")

    fixation = _build_fixation(fix_aps, pfx, lp)

    if inputs:
        ip_pi2 = "\n      & \n      ".join(
            f'({{"{ip}"_tau}} -> ({{"{ip}"_pi}} <-> {{"{ip}"_pi2}}))' for ip in inputs)
        ip_pi3 = "\n          & \n          ".join(
            f'({{"{ip}"_tau2}} -> ({{"{ip}"_pi}} <-> {{"{ip}"_pi3}}))' for ip in inputs)
        ip_eq = "\n      & \n      ".join(
            f'("{ip}"_tau <-> "{ip}"_tau2)' for ip in inputs)
        ip_imp = "\n        & \n        ".join(
            f'("{ip}"_tau2 -> ("{ip}"_tau))' for ip in inputs)
    else:
        ip_pi2 = ip_pi3 = ip_eq = ip_imp = "true"

    if mode == "SR":
        return f"""exists pi. exists tau. forall pi2.
(
  {fixation}
)
&
(
  ( G ( {ip_pi2} ) )
  ->
  ( {_rename_effect(effect, 'pi2')} )
)"""

    if mode == "MSR":
        return f"""exists pi. exists tau. forall pi2. forall tau2. exists pi3.
(
  {fixation}
)
&
(
  ( G ( {ip_pi2} ) )
  ->
  ( {_rename_effect(effect, 'pi2')} )
)
&
(
  !
  (
    ( G ( {ip_pi3} ) )
    ->
    ( {_rename_effect(effect, 'pi3')} )
  )
  |
  G ( {ip_eq} )
  |
  !
  ( G ( {ip_imp} ) )
)"""

    raise ValueError(f"unsupported mode {mode!r} (use SR or MSR)")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../examples/example_1.txt"
    mode = sys.argv[2].upper() if len(sys.argv) > 2 else "MSR"
    scope = sys.argv[3] if len(sys.argv) > 3 else "inputs"
    print(generate_hq_variant(path, mode, scope))
