#!/usr/bin/env python3
"""
HyperLTL formulas for the HyperQB (NuSMV) pipeline.

The causality core -- sufficiency plus subset-minimality -- is the same query
msr_formulas builds for AutoHyper.  Two things differ, and both come from the
systems being symbolic (smv_builder) rather than explicit:

1. NO pi FIXATION.  pi's model IS the lasso (build_pi_smv is a counter with one
   trace), so the fixation conjunct msr_formulas prepends is absent here.  That
   is not an optimisation: the fixation is a positively-asserted `G`, and under
   HyperQB's pessimistic bounded semantics such a `G` needs a loop inside the
   unrolling bound.  It never finds one, so the formula-side fixation makes
   EVERY bounded MSR query return `unsat`, at every bound, on every instance.
   Moving the lasso into the transition relation is exact, not a relaxation.

2. A `valid` GUARD.  The symbolic REAL module contains *every* input word (the
   inputs are free booleans), with a DEFINEd `valid` flag that is G-true exactly
   on the words the HOA can actually produce.  So every pi2/pi3 constraint is
   guarded by `{"valid"_pi2}` / `{"valid"_pi3}` -- the symbolic analogue of the
   explicit pipeline's sink-state trick.

PI_VAL runs on the single-trace PIVAL product module:  exists pi. (G valid) & Phi.

The output is in OUR (AutoHyper) dialect; hyperqb_backend.translate turns it
into HyperQB's syntax.  Keeping it in one dialect means the identical query text
can be handed to either solver, so a verdict difference can never be a
modelling difference.
"""
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import converter            # noqa: E402

#: The spot environment's interpreter, for normalize_effect when this module is
#: imported by a python that has no spot bindings (the usual case: tracemsr runs
#: on the system python, spot lives in a micromamba env).
SPOT_ENV_PY = os.environ.get(
    "SPOT_ENV_PY", os.path.expanduser("~/micromamba/envs/spot/bin/python3"))


def _rename_effect(effect_str, trace_name):
    return re.sub(r'"([^"]+)"', r'{"\1"_' + trace_name + '}', effect_str)


def _parse_effect(filepath):
    with open(filepath, "r") as f:
        m = re.search(r'\[effect\]\s+(.*)', f.read())
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------- #
# Effect normalization -- HyperQB's grammar is a strict subset of AutoHyper's
# --------------------------------------------------------------------------- #
def normalize_effect(effect_str):
    """Rewrite an effect into the operator set HyperQB's parser accepts.

    Verified against the binary: HyperQB accepts `U`, `R`, `X`, `F`, `G`, but
    REJECTS `W` and `M` (`positives: [unop]`) and the constants `true`/`false`
    (`positives: [hfactor]`).  AutoHyper accepts all of them, so this is purely
    a HyperQB-compatibility rewrite -- and a semantics-preserving one:

        a W b  ->  b R (a | b)        a M b  ->  b U (a & b)

    spot does the rewriting (`unabbreviate`), and `simplify` additionally folds
    away most `true`/`false` -- what survives is handled by
    hyperqb_backend._expand_bool_literals.

    Returns the effect in OUR quoted-AP dialect, so the same text can still go
    to either solver.  Returns the input unchanged if spot is not reachable:
    the effect is then only a problem if it actually uses W/M, and HyperQB says
    so loudly (`Can not parse`) rather than answering wrongly.
    """
    try:
        import spot                                    # noqa: F401
    except ImportError:
        return _normalize_via_spot_env(effect_str)
    return _normalize_here(effect_str)


def _normalize_here(effect_str):
    import spot
    f = spot.formula(effect_str)
    # ORDER MATTERS: simplify first, unabbreviate last.  spot.simplify happily
    # rewrites INTO `M` (e.g. `(((!p0) <-> p2) W p1) -> p2` becomes
    # `p2 | (... M !p1)`), so unabbreviating before simplifying puts the
    # unsupported operator straight back.
    f = spot.simplify(f)
    f = spot.unabbreviate(f, "WM")
    return _spot_to_our_dialect(f)


def _normalize_via_spot_env(effect_str):
    """Same rewrite, in a subprocess, when this interpreter has no spot.

    tracemsr runs on the system python; spot lives in a separate environment.
    Rather than make the whole HyperQB path depend on that environment, we shell
    out only for this one string rewrite and fall back to the original text if
    the environment is not there."""
    if not os.path.exists(SPOT_ENV_PY):
        return effect_str
    code = ("import sys; sys.path.insert(0, %r); import smv_formulas as m; "
            "print(m._normalize_here(sys.argv[1]))" % _HERE)
    try:
        r = subprocess.run([SPOT_ENV_PY, "-c", code, effect_str],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return effect_str
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else effect_str


def _spot_to_our_dialect(f):
    r"""Print a spot formula in OUR quoted-AP dialect, from the AST.

    Do NOT do this with a regex over `str(f)`: spot prints unary operators
    flush against their operand (`Ghmastlock`, `Xa`), so a
    `(?<![\w"])name(?![\w"])` substitution silently fails to quote exactly
    those occurrences and emits an undeclared atom."""
    import spot
    k = f.kind()
    if k == spot.op_ap:
        return f'"{f.ap_name()}"'
    if k == spot.op_tt:
        return "true"
    if k == spot.op_ff:
        return "false"
    unary = {spot.op_Not: "!", spot.op_X: "X", spot.op_F: "F", spot.op_G: "G"}
    if k in unary:
        return f"({unary[k]} {_spot_to_our_dialect(f[0])})"
    binary = {spot.op_U: "U", spot.op_R: "R", spot.op_W: "W", spot.op_M: "M",
              spot.op_Implies: "->", spot.op_Equiv: "<->", spot.op_Xor: "^"}
    if k in binary:
        return (f"({_spot_to_our_dialect(f[0])} {binary[k]} "
                f"{_spot_to_our_dialect(f[1])})")
    nary = {spot.op_And: "&", spot.op_Or: "|"}
    if k in nary:
        inner = f" {nary[k]} ".join(_spot_to_our_dialect(f[i])
                                    for i in range(f.size()))
        return f"({inner})"
    raise ValueError(f"normalize_effect: unsupported spot operator {k}")


# --------------------------------------------------------------------------- #
# The queries
# --------------------------------------------------------------------------- #
def generate_hq_smv(filepath, mode="MSR", valid_var="valid", normalize=True):
    """mode in {PI_VAL, SR, MSR}.

    `valid_var` is the module's validity DEFINE (PI_VAL uses the PIVAL module's,
    which also checks the lasso's outputs).  Pass valid_var=None when the
    module's validity is constantly TRUE (an input-complete HOA): the guard
    atoms are then omitted entirely, which matters because each guard atom
    doubles the alphabet of the automata the solver has to handle.
    """
    inputs, _aps, _start, _states = converter.parse_hoa(filepath)
    effect = _parse_effect(filepath)
    if normalize and effect:
        try:
            effect = normalize_effect(effect)
        except Exception:
            pass          # unparseable by spot: leave the original text alone

    # guard prefix inside a `G ( ... )` block: '{"valid"_pi2}\n      &\n      '
    def VG(tr, indent):
        if valid_var is None:
            return ""
        return f'{{"{valid_var}"_{tr}}}\n{indent}&\n{indent}'

    if mode == "PI_VAL":
        if valid_var is None:
            return f"""exists pi.
(
  {_rename_effect(effect, 'pi')}
)"""
        return f"""exists pi.
(
  ( G {{"{valid_var}"_pi}} )
  &
  ( {_rename_effect(effect, 'pi')} )
)"""

    if inputs:
        ip_pi2 = "\n      & \n      ".join(
            f'({{"{ip}"_tau}} -> ({{"{ip}"_pi}} <-> {{"{ip}"_pi2}}))' for ip in inputs)
        ip_pi3 = "\n          & \n          ".join(
            f'({{"{ip}"_tau2}} -> ({{"{ip}"_pi}} <-> {{"{ip}"_pi3}}))' for ip in inputs)
        ip_eq = "\n      & \n      ".join(
            f'({{"{ip}"_tau}} <-> {{"{ip}"_tau2}})' for ip in inputs)
        ip_imp = "\n        & \n        ".join(
            f'({{"{ip}"_tau2}} -> {{"{ip}"_tau}})' for ip in inputs)
    else:
        ip_pi2 = ip_pi3 = ip_eq = ip_imp = "true"

    if mode == "SR":
        return f"""exists pi. exists tau. forall pi2.
(
  ( G (
      {VG('pi2', '      ')}{ip_pi2}
  ) )
  ->
  ( {_rename_effect(effect, 'pi2')} )
)"""

    if mode == "MSR":
        return f"""exists pi. exists tau. forall pi2. forall tau2. exists pi3.
(
  ( G (
      {VG('pi2', '      ')}{ip_pi2}
  ) )
  ->
  ( {_rename_effect(effect, 'pi2')} )
)
&
(
  !
  (
    ( G (
        {VG('pi3', '        ')}{ip_pi3}
    ) )
    ->
    ( {_rename_effect(effect, 'pi3')} )
  )
  |
  G ( {ip_eq} )
  |
  !
  ( G ( {ip_imp} ) )
)"""

    raise ValueError(f"unsupported mode {mode!r} (use PI_VAL, SR or MSR)")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../examples/example_1.txt"
    mode = sys.argv[2].upper() if len(sys.argv) > 2 else "MSR"
    print(generate_hq_smv(path, mode))
