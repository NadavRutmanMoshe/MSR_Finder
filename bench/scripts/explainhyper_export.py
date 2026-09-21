#!/usr/bin/env python3
"""
cav22_export.py -- turn a TraceMSR instance into an input for the CAV'22 tool
"Explaining Hyperproperty Violations" (Coenen et al., arXiv 2206.02074).

Their tool explains a counterexample Gamma to a HyperLTL formula Phi.  We only
compare on TRACE properties, so the instance <S, pi, eff> maps over as

    Phi   = Exists (Exists (eff_0))              -- a trace property, lifted
    Gamma = <pi, pi>                             -- pi satisfies eff

WHY THE PREFIX IS `Exists` AND THE EFFECT IS NOT NEGATED
--------------------------------------------------------
Their front end does not read the quantifier prefix.  `Translator.mchyper_to_spot`
(translator.py:124) DELETES every `Forall`/`Exists` and keeps exactly one bit --
"did the formula start with Forall" -- and if it did, explain_hyper.py:233
NEGATES the body before explaining it.  So what their highlighter ends up
explaining is:

    Forall (Forall (Neg eff))   ->  body `!eff`, negate -> explains  eff
    Exists (Exists (eff))       ->  body ` eff`, no negate -> explains eff
    Forall (Forall (eff))       ->  body ` eff`, negate -> explains !eff   WRONG

The first two are the same question and, measured on TorcsGearing e00/e07,
return bit-identical causes.  We write the second, so the formula handed to
their tool literally IS the effect -- no negation to reason about when reading
the benchmark.  The third is the naive reading of "give it Phi" and is a
different question: their tool does not check that Gamma violates Phi, it simply
explains the negation, and on those two instances it lost the real cause on one
and invented five spurious ones on the other.

The second copy of pi is padding: explain_hyper.py is hardwired to exactly two
traces ("# Assume 2 traces only"), and Phi never mentions trace 1, so nothing on
trace 1 can ever end up in a minimal cause.

Their front end wants four artefacts, which this module writes:

  <name>.aag   the system as an AIGER circuit (py-aiger simulates it to recover
               the outputs of the counterexample).  Latches are the state bits,
               named state[0..k] exactly as in their yosys-produced examples.
  <name>.dot   the state graph.  Node NAMES are latch valuations -- their
               Highlighter.formula_for_node parses the name, not the label -- and
               edge labels are input cubes written with the same U+2227/U+00AC
               glyphs their examples use.
  <name>.cex   an MCHyper counterexample: input and latch values per step, with
               I:remember_state@k=1 marking the start of the lasso.  Everything
               else in a real MCHyper .cex is skipped by their parser.
  <name>.f     the HyperLTL formula in MCHyper prefix syntax.

Two structural checks decide whether an instance is expressible at all; both are
limitations of their front end, not of the theory, and both are recorded in the
metadata rather than raised:

  cube_ok    Their edge labels go through satispy, which mis-parses a negated
             mixed and/or string (`-(a & -b | -a & b)` silently loses a clause)
             and crashes on an unparenthesised one.  So the condition guarding a
             transition must be a single cube.  Transitions guarded by no input
             at all get a fresh free variable, which is what their `*` label
             does in effect.
  moore_ok   Their candidate cause is derived purely from "which inputs must
             flip to leave the observed state path", which presumes the outputs
             are determined by the state -- true for the Moore machines they
             evaluate on.  On a Mealy machine an input can change the output
             without changing the successor, and such an input can never enter
             their candidate set.

Usage:
    python3 cav22_export.py <instance.txt> [-o outdir] [--effect "F \"g1\""]
"""
import argparse
import itertools
import json
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
for p in (_HERE, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import converter          # noqa: E402  parse_hoa / eval_cond / get_val_tuple
import smv_builder        # noqa: E402  parse_lasso / _enumerate_transitions

AND = "∧"   # the glyph their dot_minimization splits edge labels on
NEG = "¬"
FREE_AP = "anyinput"   # stands in for their `*`: a variable the trace never pins


class ExportError(Exception):
    pass


# --------------------------------------------------------------------------- #
# transition enumeration
# --------------------------------------------------------------------------- #
# smv_builder._enumerate_transitions calls converter.eval_cond once per
# (state, edge, input valuation, output valuation); each call re-runs a regex
# substitution and a Python eval on the condition string, which is ~2 orders of
# magnitude slower than it needs to be and does not finish on the larger CATS
# systems.  Compile each condition to a lambda once, and enumerate only the
# outputs the condition actually mentions -- the rest are don't-cares that
# hoa_canonicalize has already resolved, so they never discriminate.
def _compile_cond(cond):
    s = cond.strip()
    if s == "t":
        return (lambda v: True), set()
    if s == "f":
        return (lambda v: False), set()
    idxs = {int(m) for m in re.findall(r"\b\d+\b", s)}
    body = s.replace("!", " not ").replace("&", " and ").replace("|", " or ")
    body = re.sub(r"\b(\d+)\b", r"v[\1]", body)
    return eval("lambda v: (" + body + ")"), idxs


def enumerate_transitions(inputs, outputs, aps, start_state, states):
    """(state, input valuation) -> (output valuation, target) | None, with
    states renumbered 0..N-1.  Same contract as
    smv_builder._enumerate_transitions, but fast enough for the CATS corpus."""
    ids = set(states.keys()) | {start_state}
    for trs in states.values():
        ids.update(t for _, t in trs)
    ids = sorted(ids)
    idmap = {sid: k for k, sid in enumerate(ids)}
    ap_idx = {n: i for i, n in enumerate(aps)}
    in_idx = [ap_idx[n] for n in inputs]
    out_idx = [ap_idx[n] for n in outputs]
    input_combos = list(itertools.product([False, True], repeat=len(inputs)))

    trans = {}
    for sid in ids:
        s = idmap[sid]
        compiled = [(_compile_cond(c), t) for c, t in states.get(sid, [])]
        for iv in input_combos:
            hit = None
            for ((fn, mentioned), target) in compiled:
                free = [j for j, ai in enumerate(out_idx) if ai not in mentioned]
                probe = [j for j, ai in enumerate(out_idx) if ai in mentioned]
                for ov_probe in itertools.product([False, True], repeat=len(probe)):
                    val = [False] * len(aps)
                    for ai, b in zip(in_idx, iv):
                        val[ai] = b
                    ov = [False] * len(outputs)
                    for j, b in zip(probe, ov_probe):
                        ov[j] = b
                        val[out_idx[j]] = b
                    # don't-care outputs are pinned to False, matching the
                    # resolution hoa_canonicalize applies when it concretizes a
                    # strategy into an implementation.
                    if fn(val):
                        hit = (tuple(ov), idmap[target])
                        break
                if hit is not None and not free:
                    break
            trans[(s, iv)] = hit
    return idmap, input_combos, trans


# --------------------------------------------------------------------------- #
# reading the instance
# --------------------------------------------------------------------------- #
def read_effect(path):
    with open(path) as f:
        content = f.read()
    m = re.search(r'\[effect\]\s*\n(.*?)(?=\n\s*\[|\Z)', content, re.DOTALL)
    if not m:
        raise ExportError("no [effect] block")
    return m.group(1).strip().splitlines()[0].strip()


def load_instance(path):
    """Everything the export needs: the Mealy machine, the lasso, the effect."""
    inputs, aps, start_state, states = converter.parse_hoa(path)
    outputs = [a for a in aps if a not in inputs]
    if not inputs:
        raise ExportError("system has no inputs")
    idmap, input_combos, trans = enumerate_transitions(
        inputs, outputs, aps, start_state, states)
    lasso_aps, prefix_sets, loop_sets = smv_builder.parse_lasso(path)
    if not loop_sets:
        raise ExportError("lasso has an empty [Loop]")
    letters = [set(lasso_aps[i] for i in s) for s in prefix_sets + loop_sets]
    return dict(inputs=inputs, outputs=outputs, aps=aps,
                idmap=idmap, input_combos=input_combos, trans=trans,
                init=idmap[start_state], n_states=len(idmap),
                letters=letters, n_prefix=len(prefix_sets), n_loop=len(loop_sets),
                effect=read_effect(path))


def walk(inst):
    """Replay the lasso through the Mealy machine, unrolling until the STATE
    repeats.

    The [lasso] block is periodic in the labels, which does not make it periodic
    in the states: `atva2022_arbiter` loops on {r1,g1}{r0,g0} from index 1, but
    the state sequence is 0,1,3,4,3,4,... and only becomes periodic at index 2.
    The counterexample their tool consumes is a finite unrolling, so we walk
    until some (state, offset in the label loop) pair recurs and report the
    state-aligned decomposition.

    Returns (states, ivals, letters, p, l): states[k] is the state before step
    k, the trace is letters[:p] . letters[p:p+l]^omega, and len(states)==p+l+1
    with states[p+l] == states[p]."""
    inputs, outputs = inst["inputs"], inst["outputs"]
    letters, p0, l0 = inst["letters"], inst["n_prefix"], inst["n_loop"]
    limit = p0 + l0 * (inst["n_states"] + 1)

    q = inst["init"]
    states, ivals, letts, seen = [q], [], [], {}
    k = 0
    while True:
        if k >= p0:
            key = (q, (k - p0) % l0)
            if key in seen:
                p = seen[key]
                return states[:k + 1], ivals[:k], letts[:k], p, k - p
            seen[key] = k
        if k > limit:
            raise ExportError("lasso never becomes state-periodic")
        letter = letters[k] if k < p0 else letters[p0 + (k - p0) % l0]
        iv = tuple(n in letter for n in inputs)
        hit = inst["trans"].get((q, iv))
        if hit is None:
            raise ExportError(f"trace step {k}: no transition from state {q}")
        ov, nxt = hit
        want = set(n for n, v in zip(outputs, ov) if v)
        if want != (letter & set(outputs)):
            raise ExportError(
                f"trace step {k}: system emits {sorted(want)} but the lasso says "
                f"{sorted(letter & set(outputs))}")
        ivals.append(iv)
        letts.append(letter)
        states.append(nxt)
        q = nxt
        k += 1


def unroll(states, ivals, letters, p, l, min_len):
    """Repeat the loop until the finite unrolling is at least `min_len` letters
    long.

    Their dot_minimization reads the edge taken at each step by zipping the
    state path with its own tail, so a counterexample of a single letter has an
    empty edge list and the tool raises IndexError.  A one-letter lasso is
    common here -- `G o` is witnessed by a self-loop -- and repeating the loop
    denotes exactly the same infinite trace, so this costs nothing but a longer
    file."""
    while p + l < min_len:
        states = states[:p + l] + [states[p + i] for i in range(l)] + [states[p + l]]
        ivals = ivals + ivals[p:p + l]
        letters = letters + letters[p:p + l]
        l *= 2
    return states, ivals, letters, p, l


# --------------------------------------------------------------------------- #
# structural checks on the state graph
# --------------------------------------------------------------------------- #
def edge_conditions(inst):
    """(s, s') -> set of input valuations taking that edge."""
    cond = {}
    for (s, iv), hit in inst["trans"].items():
        if hit is None:
            continue
        cond.setdefault((s, hit[1]), set()).add(iv)
    return cond


def as_cube(inst, ivs):
    """The set of valuations `ivs` as a cube, or None if it is not one.

    A cube is fixed on some inputs and free on the rest, so it is exactly the
    set of valuations agreeing with the fixed part -- check both that every
    input is either constant across `ivs` or unconstrained, and that `ivs` has
    the size that implies."""
    n = len(inst["inputs"])
    fixed = {}
    for j in range(n):
        vals = {iv[j] for iv in ivs}
        if len(vals) == 1:
            fixed[j] = vals.pop()
    if len(ivs) != 2 ** (n - len(fixed)):
        return None
    return fixed


def cube_label(inst, fixed):
    if not fixed:
        return FREE_AP
    return (" " + AND + " ").join(
        (inst["inputs"][j] if v else NEG + inst["inputs"][j])
        for j, v in sorted(fixed.items()))


def check_moore(inst):
    """True when no input can change an output without also changing the
    successor state -- the assumption their candidate-cause heuristic makes."""
    by_target = {}
    for (s, iv), hit in inst["trans"].items():
        if hit is None:
            continue
        ov, nxt = hit
        prev = by_target.setdefault((s, nxt), ov)
        if prev != ov:
            return False
    return True


# --------------------------------------------------------------------------- #
# the four artefacts
# --------------------------------------------------------------------------- #
def n_latch_bits(n_states):
    return max(1, math.ceil(math.log2(max(n_states, 2))))


def state_name(bits, s):
    return "; ".join(f"'state[{j}]' = {(s >> j) & 1}" for j in range(bits)) + ";"


def write_dot(inst, path):
    """State graph.  Node names are latch valuations (their parser reads the
    NAME); labels are cosmetic.  One edge per (source, target) pair, since
    dot_minimization resolves an edge by endpoints only."""
    bits = n_latch_bits(inst["n_states"])
    cond = edge_conditions(inst)
    non_cube = []
    lines = ["digraph mealy {"]
    for s in range(inst["n_states"]):
        lines.append(f'    "{state_name(bits, s)}" [label="q{s}"]')
    for (s, t), ivs in sorted(cond.items()):
        fixed = as_cube(inst, ivs)
        if fixed is None:
            non_cube.append((s, t))
            # Still emit something so the file is well formed; the instance is
            # flagged unusable in the metadata.
            fixed = {}
        lines.append(f'    "{state_name(bits, s)}" -> "{state_name(bits, t)}"'
                     f' [label="{cube_label(inst, fixed)}"]')
    lines.append("}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return non_cube


def build_circuit(inst):
    """The Mealy machine as an AIGER circuit: latches hold the current state,
    every output is combinational in (state, inputs), exactly the alignment the
    lasso uses (step k reads inputs[k] in states[k] and emits outputs[k])."""
    import aiger
    from aiger import atom

    bits = n_latch_bits(inst["n_states"])
    sb = [atom(f"state[{j}]") for j in range(bits)]
    ia = {n: atom(n) for n in inst["inputs"]}
    false = atom(False)

    def st_expr(s):
        e = None
        for j in range(bits):
            lit = sb[j] if (s >> j) & 1 else ~sb[j]
            e = lit if e is None else e & lit
        return e

    def iv_expr(iv):
        e = None
        for n, v in zip(inst["inputs"], iv):
            lit = ia[n] if v else ~ia[n]
            e = lit if e is None else e & lit
        return e

    exprs = {o: false for o in inst["outputs"]}
    exprs.update({f"next_state[{j}]": false for j in range(bits)})
    for (s, iv), hit in inst["trans"].items():
        if hit is None:
            continue
        ov, nxt = hit
        guard = st_expr(s) & iv_expr(iv)
        for o, v in zip(inst["outputs"], ov):
            if v:
                exprs[o] = exprs[o] | guard
        for j in range(bits):
            if (nxt >> j) & 1:
                exprs[f"next_state[{j}]"] = exprs[f"next_state[{j}]"] | guard

    circ = None
    for name, e in exprs.items():
        piece = e.with_output(name).aig
        circ = piece if circ is None else circ | piece
    init = inst["init"]
    return circ.loopback(*[
        dict(input=f"state[{j}]", output=f"next_state[{j}]",
             latch=f"state[{j}]", init=bool((init >> j) & 1), keep_output=False)
        for j in range(bits)])


def write_cex(inst, states, ivals, p, l, path):
    """MCHyper counterexample.  Their reader keeps traces[:lasso_start] as the
    prefix and traces[lasso_start:-1] as the loop, so the unrolling carries one
    extra step that repeats the loop entry and is then dropped."""
    bits = n_latch_bits(inst["n_states"])
    steps = list(range(p + l)) + [p]          # trailing repeat of the loop entry
    out = []
    for k, src in enumerate(steps):
        iv = ivals[src]
        for tr in ("0", "1"):                 # trace 1 is padding, see module docstring
            for n, v in zip(inst["inputs"], iv):
                out.append(f"{n}_{tr}@{k}={1 if v else 0}")
        out.append(f"I:remember_state@{k}={1 if k == p else 0}")
        s = states[src]
        for tr in ("0", "1"):
            for j in range(bits):
                out.append(f"state[{j}]_{tr}@{k}={(s >> j) & 1}")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n\n")
    return len(steps)


# --------------------------------------------------------------------------- #
# LTL -> MCHyper prefix syntax
# --------------------------------------------------------------------------- #
#: spot's kindstr() names -> the prefix operators Translator.mchyper_to_spot
#: understands.  `Xor` and `M` have no MCHyper spelling, which is no loss here:
#: the effect generator already excludes them because AutoHyper cannot take them
#: either.
_MCH_UN = {"Not": "Neg", "G": "G", "F": "F", "X": "X"}
_MCH_BIN = {"And": "And", "Or": "Or", "Implies": "Implies", "Equiv": "Eq",
            "U": "Until", "R": "Release", "W": "WUntil"}
_MCH_NARY = {"And", "Or"}


def ltl_to_mchyper(formula, trace_id=0):
    """Render a spot formula in the prefix syntax Translator.mchyper_to_spot
    reads.  n-ary And/Or are folded to binary: mchyper_to_spot rewrites them to
    the LBT operators `&`/`|`, which take exactly two arguments."""
    import spot
    f = spot.formula(formula) if isinstance(formula, str) else formula

    def go(g):
        k = g.kindstr()
        if k == "ap":
            return f'AP "{g.ap_name()}" {trace_id}'
        if k == "tt":
            return "Const True"
        if k == "ff":
            return "Const False"
        if k in _MCH_UN:
            return f"{_MCH_UN[k]} ({go(g[0])})"
        if k in _MCH_BIN:
            name = _MCH_BIN[k]
            if k in _MCH_NARY:
                acc = go(g[0])
                for i in range(1, g.size()):
                    acc = f"{name} ({acc}) ({go(g[i])})"
                return acc
            if g.size() != 2:
                raise ExportError(f"{k} with {g.size()} operands")
            return f"{name} ({go(g[0])}) ({go(g[1])})"
        raise ExportError(f"operator {k!r} has no MCHyper rendering")

    return go(f)


def hyper_formula(effect):
    """Phi = Exists (Exists (eff_0)): the effect itself, in the one prefix their
    front end reads without negating (see the module docstring).

    The effect is simplified first, and not for tidiness.  Translator's
    mchyper_to_spot renders `Const True` as `1`, but the LBT syntax it produces
    is then handed to spot's `parse_formula`, whose LBT branch only accepts
    `t`/`f` -- so any effect carrying a Boolean constant (`true U F g`, which is
    how randltl writes `F F g`) makes their tool die on a parse error before it
    computes anything.  Simplifying is a semantics-preserving rewrite that
    removes the constants, and it can only help them, so it is the fair thing to
    do.  \tool receives the effect as written."""
    import spot
    f = spot.simplify(spot.formula(effect))
    if f.kindstr() in ("tt", "ff"):
        raise ExportError("effect simplifies to a constant")
    # Expand equivalence, xor and strong release.  Their annotate_original_formula
    # walks the equivalence normal form and indexes an edge list that an `<->`
    # node does not have, so an effect containing one raises IndexError before a
    # single cause is computed; `xor` and `M` have no MCHyper spelling at all.
    # All three rewrites preserve the property, so again this only helps them.
    f = spot.unabbreviate(f, "e^M")
    return f"Exists (Exists ({ltl_to_mchyper(f, 0)}))"


# --------------------------------------------------------------------------- #
# self-check
# --------------------------------------------------------------------------- #
def verify(inst, states, ivals, letters, circ):
    """Simulate the exported circuit on the counterexample's inputs and confirm
    it reproduces the lasso letter for letter.

    Worth doing rather than assuming: the whole comparison rests on both tools
    being handed the same system and the same trace, and the circuit is the only
    artefact where that could silently drift (a wrong latch-bit order or an
    off-by-one in the Mealy alignment still yields a file their tool happily
    consumes)."""
    sim = circ.simulator()
    next(sim)
    for k, (iv, letter) in enumerate(zip(ivals, letters)):
        got, _ = sim.send({n: bool(v) for n, v in zip(inst["inputs"], iv)})
        emitted = {o for o in inst["outputs"] if got[o]}
        want = letter & set(inst["outputs"])
        if emitted != want:
            raise ExportError(
                f"circuit disagrees with the lasso at step {k}: emits "
                f"{sorted(emitted)}, lasso says {sorted(want)}")
    return True


# --------------------------------------------------------------------------- #
def export(txt_path, outdir, name=None, effect=None):
    os.makedirs(outdir, exist_ok=True)
    name = name or os.path.splitext(os.path.basename(txt_path))[0]
    inst = load_instance(txt_path)
    if effect:
        inst["effect"] = effect
    states, ivals, letters, p, l = walk(inst)
    states, ivals, letters, p, l = unroll(states, ivals, letters, p, l, 2)

    base = os.path.join(outdir, name)
    non_cube = write_dot(inst, base + ".dot")
    circ = build_circuit(inst)
    verify(inst, states, ivals, letters, circ)
    circ.write(base + ".aag")
    n_steps = write_cex(inst, states, ivals, p, l, base + ".cex")
    phi = hyper_formula(inst["effect"])
    with open(base + ".f", "w") as f:
        f.write(phi + "\n")

    # Which (source,target) pairs the counterexample actually walks: only those
    # edges' labels are ever read, so a non-cube elsewhere is harmless.
    walked = {(states[k], states[k + 1]) for k in range(len(ivals))}
    meta = dict(
        name=name, source=txt_path, effect=inst["effect"],
        n_states=inst["n_states"], n_inputs=len(inst["inputs"]),
        n_outputs=len(inst["outputs"]), n_latches=n_latch_bits(inst["n_states"]),
        n_prefix=p, n_loop=l, cex_steps=n_steps,
        lasso_prefix=inst["n_prefix"], lasso_loop=inst["n_loop"],
        inputs=inst["inputs"], outputs=inst["outputs"],
        formula=phi,
        cube_ok=not [e for e in non_cube if e in walked],
        moore_ok=check_moore(inst),
        # Their counterfactual automaton (Def. 4) presumes a total transition
        # function: an intervention may feed any input at any step.  A partial
        # delta would have to be totalized, and every totalization invents
        # behaviour that Traces(S) does not have, so a cause found there can be
        # spurious for us.  Every CATS/SYNTCOMP system is already total; only
        # the hand-written examples with a forced initial step are not.
        total_ok=all(v is not None for v in inst["trans"].values()),
        aag=base + ".aag", dot=base + ".dot", cex=base + ".cex")
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("instance")
    ap.add_argument("-o", "--outdir", default="cav22_instances")
    ap.add_argument("--name")
    ap.add_argument("--effect", help="override the [effect] block")
    a = ap.parse_args()
    meta = export(a.instance, a.outdir, a.name, a.effect)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
