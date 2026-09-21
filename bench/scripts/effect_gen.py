#!/usr/bin/env python3
"""
Effect generation + the non-vacuity filter, for the MSR/MTSR benchmark flow.

MUST run under the spot environment python (~/micromamba/envs/spot/bin/python3);
the system python has no spot module.

Why a filter at all
-------------------
`pi_finder` (MSR for Phi -- find a PI) and `MTSR_one_example` both read only
[inputs]/[effect]/[system]; the [lasso] is ignored. So the interesting question
is decided entirely by the effect, and two degenerate cases must be excluded:

    system ^ effect      == empty  ->  no trace exhibits the effect; find-PI is
                                       trivially UNSAT.
    system ^ !effect     == empty  ->  EVERY trace exhibits the effect, so the
                                       minimal reason is "nothing": MTSR collapses
                                       to the one-state universal automaton
                                       (`States: 1 / AP: 0 / [t] 0`) and the MSR
                                       cause is empty.

Only effects that are possible *and* avoidable within the system pose a real
causality question. This is the concrete form of the warning already recorded in
benchmark/README.md ("the synthesis-from-effect fuzzer always yields an empty
cause").

Effects are drawn over the OUTPUTS only (as in CATS, where causes range over
inputs and effects over outputs), from two sources:
  * templates -- F / G / GF / FG over one output, optionally negated; these line
    up with the effect-class taxonomy the repo's ladder and dispatch rule use;
  * randltl   -- spot's random LTL, the unbiased/CATS-comparable source.
"""
import re
import subprocess

import spot

# One reaper, shared with the demo tool, rather than another copy: the
# solvers we shell out to are not leaf processes, so a plain
# subprocess.run timeout leaves their children running at ~100% CPU.
import os
import sys
# bench/ ships inside the tool repo (MSR_Finder/bench); the recorded runs
# were taken with it beside the tool (demo_tool/bench). Accept both, and let
# $MSR_FINDER_SCRIPTS override.
def _msr_finder_scripts():
    _here = os.path.dirname(os.path.abspath(__file__))
    _c = [os.environ.get("MSR_FINDER_SCRIPTS"),
          os.path.join(_here, "..", "..", "scripts"),
          os.path.join(_here, "..", "..", "MSR_Finder", "scripts")]
    for _p in _c:
        if _p and os.path.exists(os.path.join(_p, "proc_guard.py")):
            return os.path.abspath(_p)
    return os.path.abspath(_c[-1])


_PG_DEMO = _msr_finder_scripts()
if _PG_DEMO not in sys.path:
    sys.path.insert(0, _PG_DEMO)

import proc_guard  # noqa: E402  (group kill + group-scoped reaper)

# AutoHyper's HyperLTL parser is the narrower consumer, so keep to operators that
# both it and spot accept: no `xor`, no `M`.
_RANDLTL_PRIORITIES = "xor=0,M=0"

_UNARY = {'Not': '!', 'X': 'X', 'F': 'F', 'G': 'G'}
_BINARY = {'U': 'U', 'R': 'R', 'W': 'W', 'Implies': '->', 'Equiv': '<->'}
_NARY = {'And': '&', 'Or': '|'}


def to_quoted(f):
    """Print a spot formula with every atomic proposition double-quoted.

    Necessary because hyperLTL.rename_effect binds APs to trace variables with
    re.sub(r'"([^"]+)"', ...) -- an unquoted AP is silently left unbound. Spot's
    own printer emits `FXFhmastlock`, which cannot be re-quoted by string
    substitution, so we render from the AST instead.
    """
    k = f.kindstr()
    if k == 'ap':
        return f'"{f.ap_name()}"'
    if k == 'tt':
        return 'true'
    if k == 'ff':
        return 'false'
    if k in _UNARY:
        return f'({_UNARY[k]} {to_quoted(f[0])})'
    if k in _BINARY:
        return f'({to_quoted(f[0])} {_BINARY[k]} {to_quoted(f[1])})'
    if k in _NARY:
        op = f' {_NARY[k]} '
        return '(' + op.join(to_quoted(c) for c in f) + ')'
    raise ValueError(f"unsupported operator {k} in {f}")


def template_effects(outputs):
    """F / G / GF / FG over each output, plain and negated."""
    out = []
    for o in outputs:
        for lit in (f'"{o}"', f'(! "{o}")'):
            out.append(('template', f'(F {lit})'))
            out.append(('template', f'(G {lit})'))
            out.append(('template', f'(G (F {lit}))'))
            out.append(('template', f'(F (G {lit}))'))
    return out


def randltl_effects(outputs, n, seed, tree_size=8):
    """Random LTL over the outputs, re-printed with quoted APs."""
    if not outputs:
        return []
    cmd = ['randltl', '-n', str(n), '--seed', str(seed),
           f'--tree-size={tree_size}',
           f'--ltl-priorities={_RANDLTL_PRIORITIES}'] + list(outputs)
    try:
        p = proc_guard.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in p.stdout.split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            f = spot.formula(line)
            if f.is_tt() or f.is_ff():
                continue
            out.append(('randltl', to_quoted(f)))
        except Exception:
            continue
    return out


def load_system(hoa_text):
    return spot.automaton(hoa_text)


def classify(aut_system, effect_str):
    """Return one of: 'ok' | 'impossible' | 'universal' | 'error'.

    'ok' means the effect is both reachable and avoidable in the system, i.e.
    non-vacuous for MSR/MTSR.
    """
    try:
        f = spot.formula(effect_str)
        pos = spot.translate(f)
        neg = spot.translate(spot.formula.Not(f))
    except Exception:
        return 'error'
    try:
        if spot.product(aut_system, pos).is_empty():
            return 'impossible'
        if spot.product(aut_system, neg).is_empty():
            return 'universal'
        return 'ok'
    except Exception:
        return 'error'


def pick_effects(aut_system, outputs, n_wanted, seed, rng, randltl_budget=40):
    """Choose up to n_wanted non-vacuous effects, balanced across both sources.

    Every candidate from both sources is classified before any selection, so the
    reported yield is unbiased -- selecting greedily from templates first would
    only ever exercise randltl on the systems where templates already failed.

    Returns (chosen, stats); stats counts each candidate's classification.
    """
    from collections import Counter
    stats = Counter()

    pools = {'template': [], 'randltl': []}
    seen = set()
    cands = template_effects(outputs) + \
        randltl_effects(outputs, randltl_budget, seed)
    for source, eff in cands:
        if eff in seen:
            continue
        seen.add(eff)
        verdict = classify(aut_system, eff)
        stats[f"{source}:{verdict}"] += 1
        if verdict == 'ok':
            pools[source].append((source, eff))

    for v in pools.values():
        rng.shuffle(v)

    # Interleave so the instance set keeps both sources represented.
    chosen = []
    it = {k: iter(v) for k, v in pools.items()}
    order = ['template', 'randltl']
    while len(chosen) < n_wanted and any(it):
        progressed = False
        for k in order:
            if k not in it or len(chosen) >= n_wanted:
                continue
            nxt = next(it[k], None)
            if nxt is None:
                del it[k]
            else:
                chosen.append(nxt)
                progressed = True
        if not progressed:
            break
    return chosen, stats


if __name__ == '__main__':
    import sys
    print(to_quoted(spot.formula(sys.argv[1] if len(sys.argv) > 1
                                 else 'F X F a & (b U c)')))
