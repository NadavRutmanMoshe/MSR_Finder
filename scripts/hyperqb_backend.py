#!/usr/bin/env python3
"""
hyperqb_backend -- run our HyperLTL queries on HyperQB 2.0 instead of AutoHyper.

HyperQB 2.0 (Hsu et al., https://github.com/HyperQB/HyperRUSTY) is a *bounded*
model checker: NuSMV models (one per trace quantifier) + a .hq formula + an
unrolling bound, discharged with Z3.  Same one-model-per-quantifier convention
AutoHyper uses, so the symbolic systems in smv_builder plug straight in.

WHAT A VERDICT MEANS (read this before trusting one).  NEITHER direction is a
proof on our queries:

    unsat  -> no witness inside the bound k.  Raising k can turn it into sat,
              and on our queries it demonstrably does.
    sat    -> a witness was found inside the bound -- but the bound is also as
              far as the formula was CHECKED, and ours are quantifier-alternating
              (`forall pi2`, `forall tau2`).  HyperQB unrolls `G` and `F` with
              no semantics-dependent tail at all (hltlunroller/src/lib.rs:
              `Globally` is a plain conjunction over k..=bound, `Eventually` a
              plain disjunction; only `X`, `U` and `R` read the pes/opt/hpes/
              hopt flag), so `G psi` is TRUE whenever psi survives to the bound.
              A universally quantified sufficiency claim is therefore accepted
              on the strength of a k-step prefix.  Minimal reproducer:

                  Forall pi . (G (~ acc[pi]))    on a 9-state model whose only
                                                 `acc` state is 8 steps in
                      k=6 -> sat  (WRONG)    k=8 -> unsat  (right)
                      identical under -s pes, opt, hpes and hopt.
    error/timeout -> nothing was decided.

hyperqb_analyzer therefore treats a `sat` witness as a CANDIDATE and decides it
exactly with msr_certify before reporting anything; everything else is
UNKNOWN_BOUNDED, deferring to AutoHyper.  Do not relabel a bounded `unsat` as
"no cause exists", and do not report a bounded `sat` witness unchecked.

Four things differ from AutoHyper and this module owns all four:

1. FORMULA SYNTAX.  Our .hq files are AutoHyper's dialect; HyperQB's grammar
   (parser/src/grammar.pest) wants:

       exists pi. / forall pi.   ->  Exists pi . / Forall pi .   (capitalized)
       {"r0"_pi} / "r0"_pi       ->  r0[pi]
       !                         ->  ~
       a <-> b                   ->  (a -> b) & (b -> a)   (no <-> in grammar)
       true / false              ->  (A | ~A) / (A & ~A)   (no literals either)
       G F X U R & | ->          ->  unchanged

   Identifiers must match  ALPHA (ALNUM | "_" | ".")*  -- note the LEADING
   character must be a letter, so an AP like `__MSR_LASSO__` is illegal and
   gets renamed (consistently in the models too, see sanitize_model).
   `W` and `M` are NOT in the grammar; smv_formulas.normalize_effect rewrites
   them away with spot before the formula gets here.

2. TWO NuSMV PARSER BUGS.  Its NuSMV parser cannot read `->` in a DEFINE body
   (panics with "Unknown variable or predicate ''"), and it binds `!` LOOSER
   than `&`, so `!a & b` is silently read as `!(a & b)` -- wrong verdicts, in
   both directions, with no error.  expand_smv_implications and _paren_negs fix
   both; both rewrites are semantics-preserving, so AutoHyper can be given the
   same text and the two solvers stay comparable.

3. WITNESS EXTRACTION.  HyperQB prints the satisfying model ONLY under `-c`,
   and `-c` *negates* the formula first (src/main.rs: `ast_node =
   negate_formula(&ast_node)`), strips the leading existentials of the result
   (encoder/src/lib.rs: `strip_leading_existentials`), and then INVERTS the
   printed verdict -- Z3 Sat prints "result: unsat.".

   So to get a witness for  exists pi. exists tau. forall pi2 ... . body  we
   hand HyperQB the DUAL formula

       Forall pi . Forall tau . Exists pi2 ... . ~(body)

   with `-c`: HyperQB negates it back into our query, strips the leading
   `exists pi . exists tau` (exactly the two traces our table needs), and its
   printed "unsat" means our query is SAT.  `dualize()` does this, `run()`
   flips the verdict back, and `decode_states()` reads the model dump.

   The dump contains only VARs, never DEFINEs.  For our layout that means tau
   -- the cause, the thing we actually need -- reads back fine (the clique
   module's inputs are real VARs), while pi does not (its only VAR is the lasso
   counter).  pi is recovered from the [lasso] block instead; see
   hyperqb_analyzer.

4. BOUNDED, NOT EXACT.  Verdicts hold for the unrolling bound k only, and the
   witness is a finite k-prefix -- there is no prefix/loop split, because `-l`
   (loop conditions) conflicts with `-c` in the CLI, i.e. loop-condition mode
   cannot produce a witness at all.
"""
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import proc_guard  # noqa: E402  (group kill: HyperRUSTY can shell out to quabs)

#: Where to look for the HyperRUSTY binary, in order.  $HYPERQB_BIN wins, so a
#: build living anywhere can be pointed at without touching the tool.  Unlike
#: AutoHyper, HyperQB is NOT shipped inside this repository -- it is a Rust
#: build against a local Z3 (see the README, "Optional: the HyperQB backend").
BIN_CANDIDATES = (
    os.path.join(_ROOT, "external_tools", "HyperQB", "target", "release",
                 "HyperRUSTY"),
    os.path.join(_ROOT, "external_tools", "HyperQB", "HyperRUSTY"),
)
# The usual build links the micromamba `hyperqb` env's libz3; if the binary was
# built without an rpath the loader needs to be told where that is.
DEFAULT_LIB = os.path.expanduser("~/micromamba/envs/hyperqb/lib")

SEMANTICS = ("pes", "opt", "hpes", "hopt")


def binary():
    """The HyperRUSTY path: $HYPERQB_BIN if set, else the first candidate that
    exists, else the first candidate (so the error message names a real path)."""
    env = os.environ.get("HYPERQB_BIN")
    if env:
        return env
    for c in BIN_CANDIDATES:
        if os.path.exists(c):
            return c
    return BIN_CANDIDATES[0]


def available():
    return os.path.exists(binary())


def _env():
    env = dict(os.environ)
    lib = os.environ.get("HYPERQB_LIB", DEFAULT_LIB)
    if os.path.isdir(lib):
        env["LD_LIBRARY_PATH"] = lib + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    return env


# --------------------------------------------------------------------------- #
# 1. Identifier legality:  ALPHA (ALNUM | "_" | ".")*
# --------------------------------------------------------------------------- #
_LEGAL = re.compile(r'^[A-Za-z][A-Za-z0-9_.]*$')


def legal_ident(name):
    return bool(_LEGAL.match(name))


def rename_map(aps):
    """Map every AP that HyperQB's grammar would reject onto a legal name.
    Leading underscores/digits are the realistic case (`__MSR_LASSO__`); we
    prefix with 'v' and strip illegal characters, keeping the result unique."""
    out, taken = {}, set(a for a in aps if legal_ident(a))
    for ap in aps:
        if legal_ident(ap):
            continue
        clean = re.sub(r'[^A-Za-z0-9_.]', '_', ap).lstrip('_.0123456789')
        cand = ("v_" + clean) if clean else "v"
        i = 0
        while cand in taken:
            i += 1
            cand = f"v_{clean}_{i}"
        taken.add(cand)
        out[ap] = cand
    return out


# --------------------------------------------------------------------------- #
# 2. Formula translation
# --------------------------------------------------------------------------- #
def _split_prefix(hq):
    """Return (list of (kind, trace_var), body) for the quantifier prefix."""
    prefix, rest = [], hq.strip()
    pat = re.compile(r'^(exists|forall)\s+([A-Za-z][A-Za-z0-9_]*)\s*\.\s*',
                     re.IGNORECASE)
    while True:
        m = pat.match(rest)
        if not m:
            return prefix, rest
        prefix.append((m.group(1).lower(), m.group(2)))
        rest = rest[m.end():]


def _operands(text, i, oplen, stops=""):
    """Operands of a binary operator at text[i:i+oplen], found by scanning out
    to the enclosing parentheses.  Our generators always parenthesize, so no
    expression parser is needed; `stops` adds hard boundaries (`:=`/`;` in an
    SMV DEFINE) for the un-parenthesized case."""
    depth, l = 0, i - 1
    while l >= 0:
        c = text[l]
        if c == ')':
            depth += 1
        elif c == '(':
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and c in stops:
            break
        l -= 1
    depth, r = 0, i + oplen
    while r < len(text):
        c = text[r]
        if c == '(':
            depth += 1
        elif c == ')':
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and c in stops:
            break
        r += 1
    if l < 0 or r > len(text):
        raise ValueError(f"unbalanced parentheses around {text[i:i + oplen]!r}")
    return l, r, text[l + 1:i].strip(), text[i + oplen:r].strip()


def _expand_iff(body):
    """Rewrite every `A <-> B` into `((A) -> (B)) & ((B) -> (A))` -- HyperQB's
    formula grammar has no `<->` (it has `=`, but at a different precedence)."""
    while True:
        i = body.find("<->")
        if i < 0:
            return body
        l, r, a, b = _operands(body, i, 3)
        body = f"{body[:l + 1]}(({a}) -> ({b})) & (({b}) -> ({a})){body[r:]}"


def expand_smv_implications(smv):
    """Rewrite `A -> B` as `(!(A) | (B))` inside a NuSMV model.

    HyperQB's NuSMV parser (external_tools/HyperQB/parser_nusmv) does not
    handle `->` in a DEFINE body: it panics with "Unknown variable or predicate
    ''".  Our REAL/PIVAL modules use it in the `valid` flag, so every model has
    to be rewritten before HyperQB sees it.  `|`/`!` are handled fine, and the
    rewrite is semantics-preserving, so AutoHyper is given the same text and
    the two solvers stay comparable."""
    while True:
        i = smv.find("->")
        if i < 0:
            return smv
        l, r, a, b = _operands(smv, i, 2, stops=";\n")
        smv = f"{smv[:l + 1]}(!({a}) | ({b})){smv[r:]}"


def translate_body(body, rename=None):
    """AutoHyper atom/operator syntax -> HyperQB's."""
    rename = rename or {}
    body = _expand_iff(body)
    # {"ap"_trace} and "ap"_trace  ->  ap[trace]
    def atom(m):
        ap, trace = m.group(1), m.group(2)
        return f"{rename.get(ap, ap)}[{trace}]"
    body = re.sub(r'\{\s*"([^"]+)"_([A-Za-z][A-Za-z0-9_]*)\s*\}', atom, body)
    body = re.sub(r'"([^"]+)"_([A-Za-z][A-Za-z0-9_]*)', atom, body)
    body = body.replace("!", "~")
    body = _expand_bool_literals(body)
    return body


def _expand_bool_literals(body):
    """HyperQB's HyperLTL grammar has NO `true` / `false` constants -- the
    parser rejects them with `positives: [hfactor]` (verified against the
    binary: `U`, `R`, `X`, `F`, `G` all parse fine; only the literals fail).

    Our effects carry them: SYNTCOMP's generator emits `(X false) U "a1"` and
    `!(true U (F "a"))` (195 of the 978 bench files use `U`), and
    smv_formulas emits a bare `true` for the input-agreement conjunct when a
    system has no inputs.  So rewrite each literal into a tautology /
    contradiction over an atom that is already in the formula:

        true  -> (A[t] | ~A[t])        false -> (A[t] & ~A[t])

    Semantics-preserving, and AutoHyper is never given this text (it accepts
    the literals), so the comparison stays apples-to-apples."""
    m = re.search(r'[A-Za-z_][A-Za-z0-9_]*\[[A-Za-z][A-Za-z0-9_]*\]', body)
    if not m:
        return body            # nothing to build a tautology from; leave as-is
    a = m.group(0)
    body = re.sub(r'(?<![A-Za-z0-9_])true(?![A-Za-z0-9_])', f"({a} | ~{a})", body)
    body = re.sub(r'(?<![A-Za-z0-9_])false(?![A-Za-z0-9_])', f"({a} & ~{a})", body)
    return body


def translate(hq, rename=None):
    """Full AutoHyper .hq -> HyperQB .hq."""
    prefix, body = _split_prefix(hq)
    if not prefix:
        raise ValueError("no quantifier prefix found in the .hq file")
    head = " ".join(f"{'Exists' if k == 'exists' else 'Forall'} {v} ."
                    for k, v in prefix)
    return f"{head}\n{translate_body(body, rename)}\n"


def dualize(hq, rename=None):
    """The witness form: swap every quantifier and negate the body, so that
    HyperQB's `-c` (which negates internally) recovers our original query and
    exposes its leading existentials in the model."""
    prefix, body = _split_prefix(hq)
    if not prefix:
        raise ValueError("no quantifier prefix found in the .hq file")
    head = " ".join(f"{'Forall' if k == 'exists' else 'Exists'} {v} ."
                    for k, v in prefix)
    return f"{head}\n~(\n{translate_body(body, rename)}\n)\n"


def n_leading_exists(hq):
    """How many leading existentials our query has -- i.e. how many traces the
    witness model will contain (HyperQB strips exactly these)."""
    prefix, _ = _split_prefix(hq)
    n = 0
    for kind, _v in prefix:
        if kind != "exists":
            break
        n += 1
    return n


# --------------------------------------------------------------------------- #
# 3. Model-side: identifier renaming inside the .smv files
# --------------------------------------------------------------------------- #
def _paren_negs_doc():
    """Wrap every `!x` in a NuSMV model as `(!x)`  (implementation: _paren_negs).

    WHY THIS EXISTS -- HyperQB 2.0 gives `!` LOWER precedence than `&`, i.e. it
    reads `!a & b` as `!(a & b)`.  Minimal reproducer (verified against the
    binary):

        VAR a, b : boolean;  init/next both FALSE
        DEFINE t := !a & b;          -- correct value: FALSE
        Exists pi . t[pi]            -- HyperQB: sat   (i.e. it computed TRUE)
        DEFINE t := (!a) & b;        -- same formula
        Exists pi . t[pi]            -- HyperQB: unsat (correct)

    Nothing errors out: the model is silently mis-encoded and the verdicts are
    wrong, in both directions (legal paths rejected, illegal paths accepted).
    Our HOA-derived transition relations are built from input cubes like
    `(st = 1) & (!r0 & r1)`, so essentially every model we generate is affected.

    Parenthesizing is semantics-preserving, so the same text goes to AutoHyper
    and the two solvers stay comparable."""


def _paren_negs(smv):
    out, i = [], 0
    while i < len(smv):
        c = smv[i]
        if c != "!":
            out.append(c)
            i += 1
            continue
        j = i + 1
        while j < len(smv) and smv[j].isspace():
            j += 1
        if j < len(smv) and smv[j] == "(":          # !(...)  -> ((...) negated)
            depth, k = 0, j
            while k < len(smv):
                if smv[k] == "(":
                    depth += 1
                elif smv[k] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            out.append("(!" + _paren_negs(smv[j:k + 1]) + ")")
            i = k + 1
        elif j < len(smv) and (smv[j].isalpha() or smv[j] == "_"):
            k = j
            while k < len(smv) and (smv[k].isalnum() or smv[k] in "_."):
                k += 1
            out.append("(!" + smv[j:k] + ")")
            i = k
        else:                                        # not a negation we know
            out.append(c)
            i += 1
    return "".join(out)


def sanitize_model(smv, rename, expand_implications=True, fix_negation=True):
    """Make a NuSMV model digestible for HyperQB: rename illegal identifiers,
    eliminate `->` (its parser cannot read it), and parenthesize every `!`
    (its parser binds `!` looser than `&`)."""
    for old, new in rename.items():
        smv = re.sub(r'(?<![A-Za-z0-9_.])' + re.escape(old) +
                     r'(?![A-Za-z0-9_.])', new, smv)
    if expand_implications:
        smv = expand_smv_implications(smv)
    return _paren_negs(smv) if fix_negation else smv


# --------------------------------------------------------------------------- #
# 4. Running + output decoding
# --------------------------------------------------------------------------- #
_VERDICT = re.compile(r'result:\s*(sat|unsat|unknown)\.', re.I)
_STATE = re.compile(r'^State (\d+):\s*$')
_ENTRY = re.compile(r'^\s+(\S+)\s*=\s*(\S+)\s*$')


def decode_states(out):
    """The `-c` model dump -> {state_index: {var_name: value}}.

    Variable names carry the trace they belong to as a suffix (the tool's own
    regex is `.*_(\\d+)_(\\w+)$`, i.e. <var>_<state>_<trace>)."""
    states, cur = {}, None
    for line in out.splitlines():
        m = _STATE.match(line)
        if m:
            cur = int(m.group(1))
            states.setdefault(cur, {})
            continue
        m = _ENTRY.match(line)
        if m and cur is not None:
            states[cur][m.group(1)] = m.group(2)
    return states


def steps_for_trace(states, trace, aps, rename=None):
    """Project the model dump onto one trace variable: a list (indexed by time)
    of the sets of APs true at that step."""
    rename = rename or {}
    back = {v: k for k, v in rename.items()}
    out = []
    for t in sorted(states):
        true_here = set()
        for var, val in states[t].items():
            m = re.match(r'^(.*)_(\d+)_([A-Za-z][A-Za-z0-9_]*)$', var)
            if not m or m.group(3) != trace or int(m.group(2)) != t:
                continue
            name = back.get(m.group(1), m.group(1))
            if name in aps and val.lower() == "true":
                true_here.add(name)
        out.append(true_here)
    return out


def fold_lasso(steps):
    """Split a bounded witness into (prefix, loop) by finding the first repeated
    step valuation.  BMC hands back a finite unrolling with no lasso marker, so
    this is a presentation aid: it is exact when the repeated valuation really
    does determine the successor (our REAL module is input-deterministic and
    `st` is its whole state), and otherwise the caller still gets the full
    trace as the prefix with an empty loop."""
    seen = {}
    for i, s in enumerate(steps):
        key = frozenset(s)
        if key in seen:
            return steps[:seen[key]], steps[seen[key]:i]
        seen[key] = i
    return steps, []


def run(model_paths, hq_text, k=10, semantics="pes", witness=True,
        timeout=600, work_dir=None, qbf=False):
    """Invoke HyperQB once.

    `hq_text` is OUR (AutoHyper-dialect) formula; it is translated here, and
    dualized when `witness` is set so that `-c` yields the model.  Returns
    {verdict, states, seconds, cmd, raw} with `verdict` already corrected for
    the `-c` inversion, i.e. it always refers to OUR query.

    HyperRUSTY runs with `work_dir` as its current directory: it drops a
    NuSMV script (`commands.txt`) and `flattened.txt` there, so a shared CWD
    is shared state between concurrent runs.  Pass a private directory."""
    if semantics not in SEMANTICS:
        raise ValueError(f"semantics must be one of {SEMANTICS}")
    if not available():
        raise RuntimeError(
            f"HyperQB binary not found at {binary()} -- build it with "
            f"`cargo build --release` in external_tools/HyperQB, or set "
            f"HYPERQB_BIN.")
    work_dir = work_dir or os.path.dirname(os.path.abspath(model_paths[0]))
    p_hq = os.path.join(work_dir, "temp_hyperqb.hq")
    with open(p_hq, "w") as f:
        f.write(hq_text)
    cmd = [binary(), "-n", *model_paths, "-f", p_hq,
           "-k", str(k), "-s", semantics]
    if witness:
        cmd.append("-c")
    if qbf:
        cmd.append("-q")
    t0 = time.perf_counter()
    try:
        # proc_guard, not subprocess: HyperRUSTY shells out to quabs and blocks
        # on it, so a plain `subprocess.run` timeout kills the parent and leaves
        # the QBF solve running. Same TimeoutExpired, so callers are unchanged.
        res = proc_guard.run(cmd, timeout=timeout, env=_env(), cwd=work_dir)
    finally:
        if os.path.exists(p_hq):
            try:
                os.remove(p_hq)
            except OSError:
                pass
    secs = time.perf_counter() - t0
    out = res.stdout + "\n" + res.stderr
    m = _VERDICT.search(out)
    verdict = m.group(1).lower() if m else "error"
    if witness and verdict in ("sat", "unsat"):     # `-c` inverts the label
        verdict = "unsat" if verdict == "sat" else "sat"
    return {"verdict": verdict, "states": decode_states(out) if witness else {},
            "seconds": secs, "cmd": cmd, "raw": out, "returncode": res.returncode}


# --------------------------------------------------------------------------- #
# Self-test: translation is checkable without the binary
# --------------------------------------------------------------------------- #
def _selftest():
    """Checkable without the binary: the translation is pure text."""
    import smv_formulas

    ex = os.path.join(_ROOT, "examples", "example_1.txt")
    ours = smv_formulas.generate_hq_smv(ex, "MSR", normalize=False)
    ren = rename_map(["i1", "i2", "__MSR_LASSO__"])
    print("rename map:", ren)
    print("\n--- leading existentials:", n_leading_exists(ours))
    print("\n=== translated (verdict form) ===")
    print(translate(ours, ren)[:600])
    print("\n=== dualized (witness form, for -c) ===")
    print(dualize(ours, ren)[:400])

    assert "<->" not in translate(ours, ren)
    assert "!" not in translate(ours, ren)
    assert "__MSR_LASSO__" not in translate(ours, ren)
    for tok in ("Exists pi .", "Forall pi2 .", "i1[pi]", "~"):
        assert tok in translate(ours, ren), tok
    assert translate(ours, ren).count("Exists") == 3
    assert dualize(ours, ren).count("Forall") == 3      # duals of the 3 exists
    print("\nself-test OK   (binary present: %s)" % available())


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
