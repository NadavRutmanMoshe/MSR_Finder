#!/usr/bin/env python3
"""
cause_check -- turn MTSR from a synthesizer into a CHECKER, the way CATS is one.

Runs under the spot environment python (MTSR_one_example already does), because
the whole check is a language question spot answers exactly.

WHAT IT CHECKS
--------------
CATS (Beutner, Finkbeiner, Frenkel, Metzger, "Checking and Sketching Causality
in Reactive Systems") does not synthesize a cause: the user writes a candidate
cause into the instance and CATS decides whether it is one.  Its instance format
carries that candidate in a `[cause]` block, an LTL formula over the inputs.

MTSR goes the other way -- it *builds* the cause, as an automaton over the
inputs: the (system x effect) product, projected onto the inputs and minimized,
i.e. exactly the input sequences that force the effect.  So checking a
user-supplied cause needs no second algorithm.  Synthesize as usual, then ask
whether the two denote the same omega-language:

        L( [cause] )   ==   L( MTSR )

Both are omega-languages over the same input alphabet, so `spot.are_equivalent`
settles it, and when they differ the inclusion direction says HOW.

RESTRICTED TO WHAT THE SYSTEM CAN PRODUCE
-----------------------------------------
One correction to that picture, and it is not cosmetic.  The MTSR is built as
(system x effect) projected, so by construction it only ever contains input
words the SYSTEM CAN PRODUCE.  A hand-written cause has no reason to be written
that way.  CATS's own `atva2022_arbiter_simple` is `X "r1"` on a system whose
first step is forced to `!r0 & !r1`: as raw languages, `X "r1"` is a strict
superset of the MTSR -- it "admits" words like `r0 & X r1` -- and a plain
equivalence check calls it TOO WEAK.  That verdict is false as a statement
about causality: those extra words are not traces of the system at all, so
nothing is claimed about them either way.

So the comparison is made inside the system's own input language:

        L( [cause] )  n  L_in(system)   ==   L( MTSR )

`L_in(system)` is the system automaton projected onto the inputs -- the same
projection MTSR does, minus the effect.  With it, `X "r1"` comes out
EQUIVALENT, which is the true answer.  When the restriction is what made the
difference, the result says so, so the raw fact ("this cause also mentions
input words the system cannot produce") is reported rather than hidden.

WHY THE DIRECTION MATTERS, AND WHY IT IS NAMED AND NOT SUMMARISED
-----------------------------------------------------------------
The two ways to be wrong are not symmetric:

  cause SUBSET-OF mtsr   the given cause is too STRONG -- every input word it
                         admits does force the effect, so it is sufficient, but
                         it rules out words that would have forced it too.  A
                         correct-but-not-weakest explanation.
  mtsr SUBSET-OF cause   the given cause is too WEAK -- it admits input words
                         that do NOT force the effect.  It is not sufficient,
                         i.e. not a cause at all.
  incomparable           it does both.

"subsumes"/"stronger" wording has inverted a finding in this project before, so
the verdicts are named by direction and the human-readable line spells out which
side is the offender.

THE `[cause]` BLOCK
-------------------
Same block CATS uses, so an instance can be handed to either tool:

    [cause]
    "i1" & (X X ("i1" & !"i2"))

APs are quoted, like everywhere else in our format, and must be inputs -- a
cause over outputs is not a cause.  CATS's *sketch* syntax (`?{"i0"}` holes) is
a different thing: it is a template for CATS to fill, not a formula, so it is
rejected here rather than half-parsed.

QPTL: `E _q . ...`
------------------
CATS's own examples use quantified propositional temporal logic for causes that
are not expressible in plain LTL -- "every OTHER step", say:

    E _q . !_q & G (X _q <-> !_q) & F (_q & "r1")

`_q` is a fresh proposition the formula is free to choose, here pinned by the
middle conjunct to alternate, so the last conjunct says "r1 holds at some ODD
position".  LTL cannot say that; this can.

An existential proposition quantifier is exactly an existential PROJECTION of
the language onto the remaining atoms, and omega-automata are closed under it
-- it is the same operation MTSR itself already performs to project the outputs
away.  So the body is translated with `_q` as an ordinary atom and `_q` is then
projected out (`spot.remove_ap`).  Exact, not an approximation.  A universal
`A _q .` is NOT handled and is reported rather than silently mistranslated.
"""
import os
import re
import sys

import spot

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

#: `spot.formula` would happily parse `?{"i0"}` into something unintended, so
#: sketch holes are detected before parsing rather than after.
_HOLE = re.compile(r'\?\s*\{')

#: `E _q . <body>` / `E _q _r . <body>` -- the QPTL prefix CATS uses. The `.` is
#: what makes this unambiguous against a formula that merely starts with an
#: atom named `E`.
_QPTL = re.compile(r'\A\s*(?P<quant>[EA])\s+'
                   r'(?P<vars>[A-Za-z_][A-Za-z0-9_]*(?:\s+[A-Za-z_][A-Za-z0-9_]*)*)'
                   r'\s*\.\s*(?P<body>.+)\Z', re.DOTALL)


def split_qptl(cause_text):
    """(quantifier, [prop names], body) for a QPTL cause; (None, [], text)
    for a plain LTL one."""
    m = _QPTL.match(cause_text)
    if not m:
        return None, [], cause_text
    return m.group("quant"), m.group("vars").split(), m.group("body")


#: Section headers sit alone on their own line. Anchoring to that matters: the
#: [info] block is free prose and may well mention "[cause]" or "[effect]" in a
#: sentence, and an unanchored search happily returns that sentence.
_SECTION = re.compile(r'^[^\S\n]*\[([a-z]+)\][^\S\n]*$', re.MULTILINE)


def parse_cause(path):
    """The `[cause]` block's text, or None when the file has none."""
    with open(path, "r", errors="replace") as f:
        content = f.read()
    for m in _SECTION.finditer(content):
        if m.group(1) != "cause":
            continue
        nxt = _SECTION.search(content, m.end())
        text = content[m.end():nxt.start() if nxt else len(content)].strip()
        return text or None
    return None


def parse_inputs(path):
    with open(path, "r", errors="replace") as f:
        content = f.read()
    for m in _SECTION.finditer(content):
        if m.group(1) != "inputs":
            continue
        nxt = _SECTION.search(content, m.end())
        block = content[m.end():nxt.start() if nxt else len(content)]
        return set(re.findall(r'"([^"]+)"', block))
    return set()


def system_input_language(path):
    """L_in(system): the input words the system can actually produce.

    The system automaton with every non-input AP existentially projected away --
    the same operation MTSR performs on (system x effect), minus the effect.
    Returns None when the file has no HOA block or none of its APs are outputs
    (nothing to project, so no restriction to apply)."""
    with open(path, "r", errors="replace") as f:
        m = re.search(r'(HOA:.*--END--)', f.read(), re.DOTALL)
    if not m:
        return None
    aut = spot.automaton(m.group(1))
    inputs = parse_inputs(path)
    outputs = [str(ap) for ap in aut.ap() if str(ap) not in inputs]
    if not outputs:
        return aut
    strip = spot.remove_ap()
    for op in outputs:
        strip.add_ap(op)
    return strip.strip(aut)


def _compare(a, b):
    """(verdict, ok) for two automata, naming the inclusion direction.
    `product(x, complement(y)).is_empty()` is "L(x) subset-of L(y)"."""
    if spot.are_equivalent(a, b):
        return "EQUIVALENT", True
    if spot.product(a, spot.complement(b)).is_empty():
        return "CAUSE_SUBSET_OF_MTSR", False
    if spot.product(b, spot.complement(a)).is_empty():
        return "MTSR_SUBSET_OF_CAUSE", False
    return "INCOMPARABLE", False


def check(cause_text, mtsr_hoa, inputs=None, sys_inputs=None):
    """Compare the given cause with the synthesized MTSR automaton.

    `mtsr_hoa` is the minimized HOA string MTSR produced.  `sys_inputs` is
    L_in(system) from `system_input_language`; when given, the cause is
    restricted to it before comparing (see the module docstring -- without that
    restriction, a cause that simply does not bother to exclude impossible input
    words is misreported as too weak).  Returns a dict:
        verdict   EQUIVALENT / CAUSE_SUBSET_OF_MTSR / MTSR_SUBSET_OF_CAUSE /
                  INCOMPARABLE / ERROR
        ok        True only for EQUIVALENT
        detail    one human-readable sentence
        raw_verdict  the same comparison WITHOUT the restriction (present only
                     when the restriction changed the answer)
    """
    if _HOLE.search(cause_text):
        return {"verdict": "ERROR", "ok": False,
                "detail": "the [cause] block is a CATS *sketch* (it contains "
                          "?{...} holes), not a concrete cause. --check "
                          "verifies a formula; it does not fill templates."}
    quant, qvars, body = split_qptl(cause_text)
    if quant == "A":
        return {"verdict": "ERROR", "ok": False,
                "detail": f"the [cause] block universally quantifies {qvars}. "
                          f"Only the existential form (E ... .) is supported: "
                          f"it is a projection, which omega-automata are closed "
                          f"under. A universal quantifier is not."}
    try:
        formula = spot.formula(body)
    except Exception as e:
        return {"verdict": "ERROR", "ok": False,
                "detail": f"could not parse the [cause] formula: {e}"}

    if inputs:
        # The quantified propositions are bound by the formula, not supplied by
        # the environment, so they are not required to be inputs.
        stray = sorted({str(ap) for ap in spot.atomic_prop_collect(formula)}
                       - set(inputs) - set(qvars))
        if stray:
            return {"verdict": "ERROR", "ok": False,
                    "detail": f"the [cause] formula mentions {stray}, which are "
                              f"not inputs. A cause constrains the environment, "
                              f"so it may only speak about [inputs]."}
    try:
        a = spot.translate(formula)          # the given cause
        if qvars:
            # `E _q . phi` == the language of phi projected onto the atoms other
            # than _q. Exact: nondeterministic omega-automata are closed under
            # projection, which is the same step MTSR uses for the outputs.
            strip = spot.remove_ap()
            for v in qvars:
                strip.add_ap(v)
            a = strip.strip(a)
        b = spot.automaton(mtsr_hoa)         # the synthesized MTSR
    except Exception as e:
        return {"verdict": "ERROR", "ok": False,
                "detail": f"{type(e).__name__}: {e}"}

    raw_verdict, _ok = _compare(a, b)
    if sys_inputs is not None:
        verdict, ok = _compare(spot.product(a, sys_inputs), b)
    else:
        verdict, ok = raw_verdict, _ok

    DETAIL = {
        "EQUIVALENT":
            "the given cause and the synthesized MTSR accept exactly the same "
            "input sequences.",
        "CAUSE_SUBSET_OF_MTSR":
            "the given cause is STRICTLY STRONGER than needed: every input "
            "sequence it admits does force the effect, but it excludes "
            "sequences that also force it. Sufficient, but not the weakest "
            "explanation.",
        "MTSR_SUBSET_OF_CAUSE":
            "the given cause is TOO WEAK: it admits input sequences the system "
            "CAN produce and that do NOT force the effect, so it is not a "
            "sufficient reason.",
        "INCOMPARABLE":
            "neither language contains the other: the given cause both admits "
            "sequences that do not force the effect and excludes sequences "
            "that do.",
    }
    res = {"verdict": verdict, "ok": ok, "detail": DETAIL[verdict]}
    if verdict != raw_verdict:
        res["raw_verdict"] = raw_verdict
        res["detail"] += (
            f" (Compared inside the system's own input language. As RAW "
            f"languages the answer would be {raw_verdict}: the cause also "
            f"mentions input words this system cannot produce, which are "
            f"outside the question.)")
    return res


def print_result(cause_text, res):
    import textwrap
    print()
    print(f"{'=' * 78}")
    print(f" CAUSE CHECK  {DIM}(is [cause] the same language as the MTSR?){RESET}")
    print(f"{'=' * 78}")
    for line in textwrap.wrap(f"[cause]: {cause_text}", width=76,
                             initial_indent=" ", subsequent_indent="          "):
        print(line)
    color = GREEN if res["ok"] else (YELLOW if res["verdict"] != "ERROR" else RED)
    print(f" verdict: {color}{res['verdict']}{RESET}")
    for line in textwrap.wrap(res["detail"], width=76,
                              initial_indent=" ", subsequent_indent=" "):
        print(line)
    print(f"{'=' * 78}")


def run(path, mtsr_hoa):
    """The whole flow for one instance; returns the result dict (or None when
    the file carries no [cause] block, having said so)."""
    cause_text = parse_cause(path)
    if cause_text is None:
        print(f"\n{YELLOW}[!] --check needs a [cause] block in "
              f"{os.path.basename(path)}, and there is none.{RESET}")
        print(f"{DIM}    Add one -- an LTL formula over the [inputs], the same "
              f"block CATS uses:{RESET}")
        print(f"{DIM}        [cause]{RESET}")
        print(f'{DIM}        "i1" & (X X ("i1" & !"i2")){RESET}')
        return None
    try:
        sys_inputs = system_input_language(path)
    except Exception:
        sys_inputs = None       # no HOA / unparseable: fall back to raw
    res = check(cause_text, mtsr_hoa, parse_inputs(path), sys_inputs)
    print_result(cause_text, res)
    return res


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: cause_check.py <instance.txt> <mtsr.hoa>")
        sys.exit(1)
    with open(sys.argv[2]) as f:
        hoa = f.read()
    r = run(sys.argv[1], hoa)
    sys.exit(0 if r and r["ok"] else 1)
