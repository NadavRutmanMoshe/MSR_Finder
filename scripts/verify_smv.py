#!/usr/bin/env python3
"""
verify_smv -- prove that the NuSMV modules denote the systems they claim to.

    ~/micromamba/envs/spot/bin/python3 verify_smv.py ../examples/*.txt

WHY
---
`smv_builder.py` is 380 lines that turn a HOA automaton into four symbolic NuSMV
modules.  Reading it to decide whether to trust it is slow and inconclusive: the
interesting bugs there are off-by-one word conventions and mis-grouped input
cubes, which look fine on the page.  But each module is a finite transition
system, so it DENOTES an omega-language, and each one has a reference language
that does not come from `smv_builder` at all:

    REAL    { w : G valid }        ==  L(the [system] HOA, read by spot)
    PIVAL   { w : G valid }        ==  L(the [lasso])  n  L(the HOA)
    PI      L(module)              ==  L(the [lasso] projected onto the inputs)
    CAUSE   L(module)              ==  every input word  (the 2^IP clique)

So this script does not read `smv_builder`.  It INTERPRETS its output -- parses
the emitted module as NuSMV, evaluates it, enumerates its reachable states into
an automaton -- and asks spot whether that automaton is equivalent to the
reference.  A wrong transition table, a dropped output, a `valid` flag that
admits one letter too many, an off-by-one in the t=0 convention: all of them
show up here as NOT EQUIVALENT, whatever they look like in the source.

WHY THE INTERPRETER IS NOT CIRCULAR
-----------------------------------
It could be, if it were written as the inverse of the builder.  It is not: it is
a plain NuSMV evaluator (declarations, `case` expressions, standard `!` > `&` >
`|` precedence) with no knowledge of what the builder was trying to say, and the
thing it is compared against is spot's own reading of the HOA.  For a shared
misconception to hide a bug it would have to also agree with spot.

Anything the evaluator does not understand raises instead of guessing, so an
unsupported construct is reported, never silently mis-evaluated.

Exit status is 0 only when every module checked was equivalent to its reference.
"""
import argparse
import itertools
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import spot                 # noqa: E402

import smv_builder          # noqa: E402  (the code under test: its OUTPUT only)

_G = "\033[92m"; _R = "\033[91m"; _D = "\033[2m"; _0 = "\033[0m"

#: Enumerating a module means walking st x 2^|inputs| configurations. Past this
#: the check is refused rather than left to run for hours -- it is a
#: verification tool, not a benchmark.
MAX_CONFIGS = 200_000


# --------------------------------------------------------------------------- #
# A small NuSMV evaluator.  Deliberately generic: it knows the language, not
# the builder.
# --------------------------------------------------------------------------- #
class SmvError(Exception):
    pass


class Expr:
    """Parser + evaluator for the NuSMV expression fragment.

    NuSMV precedence, tightest first: `!`, `&`, `|`, `->` (right-associative),
    `<->`.  Getting this order right is the point -- HyperQB's own NuSMV parser
    binds `!` LOOSER than `&` and silently mis-encodes every model as a result
    (see hyperqb_backend), which is exactly the class of bug an evaluator that
    guessed the precedence would fail to catch.

        iff   := impl ('<->' impl)*
        impl  := or ('->' impl)?
        or    := and ('|' and)*
        and   := not ('&' not)*
        not   := '!' not | atom
        atom  := '(' iff ')' | 'TRUE' | 'FALSE' | int
               | ident ('=' int)? | ident '+' int
    """

    #: `<->` and `->` must be matched before an identifier, and an identifier
    #: may contain `-` (NuSMV allows it, and HOA AP names use it) -- but not a
    #: `-` that starts an arrow.
    _TOK = re.compile(r'<->|->|[A-Za-z_](?:[A-Za-z0-9_$#\[\].]|-(?!>))*'
                      r'|\d+|[()&|!=+]')

    def __init__(self, text):
        self.toks = self._TOK.findall(text)
        self.i = 0
        self.tree = self._iff()
        if self.i != len(self.toks):
            raise SmvError(f"trailing tokens in {text!r}: {self.toks[self.i:]}")

    def _iff(self):
        node = self._impl()
        while self._peek() == '<->':
            self._eat('<->')
            node = ('iff', node, self._impl())
        return node

    def _impl(self):
        node = self._or()
        if self._peek() == '->':
            self._eat('->')
            return ('impl', node, self._impl())      # right-associative
        return node

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _eat(self, tok=None):
        t = self._peek()
        if tok is not None and t != tok:
            raise SmvError(f"expected {tok!r}, got {t!r}")
        self.i += 1
        return t

    def _or(self):
        node = self._and()
        while self._peek() == '|':
            self._eat('|')
            node = ('or', node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._peek() == '&':
            self._eat('&')
            node = ('and', node, self._not())
        return node

    def _not(self):
        if self._peek() == '!':
            self._eat('!')
            return ('not', self._not())
        return self._atom()

    def _atom(self):
        t = self._peek()
        if t == '(':
            self._eat('(')
            node = self._iff()
            self._eat(')')
            return node
        if t is None:
            raise SmvError("unexpected end of expression")
        self._eat()
        if t == 'TRUE':
            return ('const', True)
        if t == 'FALSE':
            return ('const', False)
        if t.isdigit():
            return ('int', int(t))
        if self._peek() == '=':                 # ident = int
            self._eat('=')
            return ('eq', t, int(self._eat()))
        if self._peek() == '+':                 # ident + int
            self._eat('+')
            return ('add', t, int(self._eat()))
        return ('var', t)

    def eval(self, env):
        return self._eval(self.tree, env)

    def _eval(self, n, env):
        k = n[0]
        if k == 'const':
            return n[1]
        if k == 'int':
            return n[1]
        if k == 'var':
            if n[1] not in env:
                raise SmvError(f"unknown identifier {n[1]!r}")
            return env[n[1]]
        if k == 'eq':
            return env[n[1]] == n[2]
        if k == 'add':
            return env[n[1]] + n[2]
        if k == 'not':
            return not self._eval(n[1], env)
        if k == 'and':
            return bool(self._eval(n[1], env)) and bool(self._eval(n[2], env))
        if k == 'or':
            return bool(self._eval(n[1], env)) or bool(self._eval(n[2], env))
        if k == 'impl':
            return (not bool(self._eval(n[1], env))) or bool(self._eval(n[2], env))
        if k == 'iff':
            return bool(self._eval(n[1], env)) == bool(self._eval(n[2], env))
        raise SmvError(f"cannot evaluate {n!r}")


class Module:
    """A parsed NuSMV `MODULE main`: variables, init/next assignments, defines."""

    def __init__(self, text):
        self.range_vars = {}     # name -> (lo, hi)
        self.free_bools = []     # names assigned {TRUE, FALSE}
        self.init = {}           # name -> Expr or 'free'
        self.next = {}           # name -> [(guard Expr|None, value Expr)]
        self.defines = []        # [(name, Expr)] in declaration order
        self._parse(text)

    # -- parsing ---------------------------------------------------------- #
    def _parse(self, text):
        body = re.sub(r'--[^\n]*', '', text)                # strip comments
        for m in re.finditer(r'^\s*([A-Za-z_][\w$#\-\[\].]*)\s*:\s*'
                             r'(boolean|(\d+)\s*\.\.\s*(\d+))\s*;', body, re.M):
            if m.group(3) is not None:
                self.range_vars[m.group(1)] = (int(m.group(3)), int(m.group(4)))
            else:
                self.range_vars[m.group(1)] = None           # boolean
        # Scanned, not regex-matched: a `case ... esac` right-hand side contains
        # semicolons of its own, so "up to the next `;`" cuts it in half (and
        # silently drops its last branch, which is exactly the default one).
        for m in re.finditer(r'(init|next)\(\s*([\w$#\-\[\].]+)\s*\)\s*:=\s*',
                             body):
            kind, var, pos = m.group(1), m.group(2), m.end()
            rest = body[pos:]
            if rest.lstrip().startswith('case'):
                depth, i = 0, 0
                for tok in re.finditer(r'\bcase\b|\besac\b', rest):
                    depth += 1 if tok.group(0) == 'case' else -1
                    if depth == 0:
                        i = tok.end()
                        break
                if depth != 0:
                    raise SmvError(f"unterminated case in next({var})")
                rhs = rest[:i]
            else:
                end = rest.find(';')
                if end < 0:
                    raise SmvError(f"unterminated assignment to {var}")
                rhs = rest[:end]
            self._assign(kind, var, rhs.strip())
        m = re.search(r'^\s*DEFINE\s*$(.*)', body, re.M | re.S)
        if m:
            for d in re.finditer(r'([A-Za-z_][\w$#\-\[\].]*)\s*:=\s*(.*?);',
                                 m.group(1), re.S):
                self.defines.append((d.group(1), Expr(d.group(2))))

    def _assign(self, kind, var, rhs):
        if re.fullmatch(r'\{\s*TRUE\s*,\s*FALSE\s*\}|\{\s*FALSE\s*,\s*TRUE\s*\}',
                        rhs):
            if kind == 'init':
                self.init[var] = 'free'
                if var not in self.free_bools:
                    self.free_bools.append(var)
            else:
                self.next[var] = 'free'
            return
        if rhs.startswith('case'):
            inner = re.sub(r'^case\s*|\s*esac$', '', rhs.strip(), flags=re.S)
            cases = []
            for c in re.finditer(r'(.*?)\s*:\s*([^;]+);', inner, re.S):
                cases.append((Expr(c.group(1)), Expr(c.group(2))))
            (self.init if kind == 'init' else self.next)[var] = cases
            return
        (self.init if kind == 'init' else self.next)[var] = [(None, Expr(rhs))]

    # -- semantics -------------------------------------------------------- #
    def _pick(self, cases, env):
        for guard, value in cases:
            if guard is None or guard.eval(env):
                return value.eval(env)
        raise SmvError("no case branch applies (NuSMV would leave the "
                       "variable unconstrained)")

    def _det_vars(self):
        return [v for v in self.range_vars if v not in self.free_bools]

    def _free_combos(self):
        return list(itertools.product([False, True], repeat=len(self.free_bools)))

    def initial_states(self):
        out = []
        base = {}
        for v in self._det_vars():
            spec = self.init.get(v)
            if spec is None:
                raise SmvError(f"{v} has no init assignment")
            base[v] = self._pick(spec, {})
        for combo in self._free_combos():
            st = dict(base)
            st.update(dict(zip(self.free_bools, combo)))
            out.append(st)
        return out

    def successors(self, state):
        # Guards are evaluated in the FULL valuation, not just the variables:
        # a module may (and PIVAL does) branch on a DEFINE, e.g.
        # `next(st) := case (st = 0) & (i1 & !i2) : 1; ...` where i1/i2 are
        # DEFINEs over the counter rather than VARs.
        env = self.valuation(state)
        base = {}
        for v in self._det_vars():
            spec = self.next.get(v)
            base[v] = state[v] if spec is None else self._pick(spec, env)
        out = []
        for combo in self._free_combos():
            nxt = dict(base)
            nxt.update(dict(zip(self.free_bools, combo)))
            out.append(nxt)
        return out

    def valuation(self, state):
        """All observable propositions in a state: the VARs plus the DEFINEs
        (evaluated in declaration order, so a DEFINE may use an earlier one)."""
        env = dict(state)
        for name, expr in self.defines:
            env[name] = expr.eval(env)
        return env


# --------------------------------------------------------------------------- #
# Module -> spot automaton
# --------------------------------------------------------------------------- #
def module_automaton(module, aps, keep=None):
    """The module's language over `aps`, as a spot automaton.

    The word convention is the one smv_builder documents: the letter at time t
    is the valuation IN the state at time t. That is a state-labelled system, so
    each outgoing edge is labelled with its SOURCE state's valuation, and a
    synthetic initial state carries the initial letters.

    `keep(valuation) -> bool` drops states, which is how `G valid` is applied:
    a run that would pass through a rejected state simply does not exist."""
    seen, order, edges = {}, [], {}
    frontier = []

    def add(state):
        key = tuple(sorted(state.items()))
        if key in seen:
            return seen[key], False
        val = module.valuation(state)
        if keep is not None and not keep(val):
            seen[key] = None
            return None, False
        idx = len(order)
        seen[key] = idx
        order.append(val)
        frontier.append((idx, state))
        return idx, True

    inits = []
    for st in module.initial_states():
        idx, _new = add(st)
        if idx is not None:
            inits.append(idx)
    while frontier:
        if len(order) > MAX_CONFIGS:
            raise SmvError(f"more than {MAX_CONFIGS} reachable configurations")
        idx, state = frontier.pop()
        for nxt in module.successors(state):
            tgt, _new = add(nxt)
            if tgt is not None:
                edges.setdefault(idx, set()).add(tgt)

    n = len(order) + 1                      # +1 for the synthetic start state
    START = len(order)
    lines = ["HOA: v1", f"States: {n}", f"Start: {START}",
             f"AP: {len(aps)} " + " ".join(f'"{a}"' for a in aps),
             "acc-name: all", "Acceptance: 0 t", "--BODY--"]

    def label(val):
        parts = []
        for i, ap in enumerate(aps):
            parts.append(f"{i}" if val.get(ap) else f"!{i}")
        return "&".join(parts) if parts else "t"

    for i, val in enumerate(order):
        lines.append(f"State: {i}")
        for tgt in sorted(edges.get(i, ())):
            lines.append(f"[{label(val)}] {tgt}")
    lines.append(f"State: {START}")
    for i in inits:                          # the initial letter comes from the
        for tgt in sorted(edges.get(i, ())):  # init state we are leaving
            lines.append(f"[{label(order[i])}] {tgt}")
    lines.append("--END--")
    return spot.automaton("\n".join(lines))


# --------------------------------------------------------------------------- #
# The reference languages -- none of them built by smv_builder
# --------------------------------------------------------------------------- #
def _hoa(path):
    with open(path, "r", errors="replace") as f:
        m = re.search(r'(HOA:.*--END--)', f.read(), re.DOTALL)
    if not m:
        raise ValueError("no HOA [system] block")
    return spot.automaton(m.group(1))


def lasso_automaton(path, aps, only_inputs=None):
    """L([lasso]) over `aps`: one word, read straight from the file."""
    lasso_aps, prefix, loop = smv_builder.parse_lasso(path)
    if not loop:
        raise ValueError("lasso has no Loop")
    chain = list(prefix) + list(loop)
    start = len(prefix)
    L = len(chain)
    lines = ["HOA: v1", f"States: {L}", "Start: 0",
             f"AP: {len(aps)} " + " ".join(f'"{a}"' for a in aps),
             "acc-name: all", "Acceptance: 0 t", "--BODY--"]
    for t, idxs in enumerate(chain):
        true_here = {ap for ap in aps
                     if ap in lasso_aps and lasso_aps.index(ap) in idxs}
        lab = "&".join(f"{i}" if a in true_here else f"!{i}"
                       for i, a in enumerate(aps)) or "t"
        lines.append(f"State: {t}")
        lines.append(f"[{lab}] {t + 1 if t + 1 < L else start}")
    lines.append("--END--")
    return spot.automaton("\n".join(lines))


def universal_automaton():
    """Every word, over any alphabet -- what the CAUSE clique is supposed to be.
    Takes no AP list on purpose: "accepts everything" is alphabet-independent,
    and spot compares over the union alphabet."""
    return spot.translate(spot.formula("1"), "BA")


# --------------------------------------------------------------------------- #
def _check(name, built, reference):
    if spot.are_equivalent(built, reference):
        return True, f"{name}: equivalent to its reference"
    a_in_b = spot.product(built, spot.complement(reference)).is_empty()
    b_in_a = spot.product(reference, spot.complement(built)).is_empty()
    how = ("module is a STRICT SUBSET of the reference (it loses words)"
           if a_in_b else
           "module is a STRICT SUPERSET of the reference (it admits words the "
           "reference does not)" if b_in_a else "the two are INCOMPARABLE")
    return False, f"{name}: NOT EQUIVALENT -- {how}"


def verify(path):
    """[(ok, note)] for the four modules of one instance."""
    import converter
    inputs, aps, _s, _st = converter.parse_hoa(path)
    rows = []

    real, rmeta = smv_builder.build_real_smv(path)
    aut = module_automaton(Module(real), list(aps),
                           keep=lambda v: v[rmeta["valid_var"]])
    rows.append(_check("REAL   (G valid == the HOA)", aut, _hoa(path)))

    cause, _ = smv_builder.build_cause_smv(path)
    if inputs:
        aut = module_automaton(Module(cause), list(inputs))
        rows.append(_check("CAUSE  (== every input word)", aut,
                           universal_automaton()))
    else:
        rows.append((None, "CAUSE  : skipped (system has no inputs)"))

    try:
        pi, pmeta = smv_builder.build_pi_smv(path)
    except ValueError as e:
        rows.append((None, f"PI     : skipped ({e})"))
        return rows
    aut = module_automaton(Module(pi), list(inputs))
    rows.append(_check("PI     (== the lasso, over inputs)", aut,
                       lasso_automaton(path, list(inputs))))

    pival, vmeta = smv_builder.build_pival_smv(path)
    aut = module_automaton(Module(pival), list(aps),
                           keep=lambda v: v[vmeta["valid_var"]])
    rows.append(_check("PIVAL  (G valid == lasso n HOA)", aut,
                       spot.product(lasso_automaton(path, list(aps)),
                                    _hoa(path))))
    return rows


# --------------------------------------------------------------------------- #
# Checking the checker
# --------------------------------------------------------------------------- #
#: Each mutant breaks the module in one specific way. `label, which module,
#: rewrite`. A rewrite that does not apply to a given file (its `valid` is
#: already TRUE, say) is reported as a no-op rather than counted as a pass.
_MUTANTS = (
    ("an output DEFINE negated", "real",
     lambda s, op: re.sub(rf'^(  {re.escape(op)} := )(.*);$', r'\1!(\2);',
                          s, count=1, flags=re.M)),
    ("valid forced TRUE", "real",
     lambda s, op: re.sub(r'^(  valid := )(?!TRUE;).*;$', r'\1TRUE;',
                          s, count=1, flags=re.M)),
    ("one delta target redirected", "real",
     lambda s, op: re.sub(r'^(    \(st = 1\)[^:]*: )(\d+);$', r'\g<1>0;',
                          s, count=1, flags=re.M)),
    ("the init state moved", "real",
     lambda s, op: re.sub(r'init\(st\) := \d+;', 'init(st) := 1;',
                          s, count=1)),
    ("the lasso loop target shifted", "pi",
     lambda s, op: re.sub(r'(\(c = \d+\) : )(\d+);',
                          lambda m: f"{m.group(1)}{int(m.group(2)) + 1};",
                          s, count=1)),
    ("an input DEFINE weakened to TRUE", "pi",
     lambda s, op: re.sub(r'^(  [A-Za-z_][\w]* := )(?!TRUE;).*;$', r'\1TRUE;',
                          s, count=1, flags=re.M)),
)


def self_test(path):
    """Mutate the modules and confirm every mutant is caught.

    A checker that cannot fail proves nothing. Each mutant below is a bug of
    the exact kind this script exists to find -- a flipped output, a `valid`
    that admits too much, a redirected edge, a moved initial state, an
    off-by-one in the lasso -- and each must come back NOT EQUIVALENT."""
    import converter
    inputs, aps, _s, _st = converter.parse_hoa(path)
    outputs = [a for a in aps if a not in inputs]
    real, rmeta = smv_builder.build_real_smv(path)
    pi, _pmeta = smv_builder.build_pi_smv(path)
    ref_real, ref_pi = _hoa(path), lasso_automaton(path, list(inputs))

    def judge(text, kind):
        if kind == "real":
            aut = module_automaton(Module(text), list(aps),
                                   keep=lambda v: v[rmeta["valid_var"]])
            return _check("REAL", aut, ref_real)
        return _check("PI", module_automaton(Module(text), list(inputs)),
                      ref_pi)

    print(f"self-test on {os.path.basename(path)}\n")
    bad = 0
    for kind, src, ref in (("REAL", real, ref_real), ("PI", pi, ref_pi)):
        ok, note = judge(src, kind.lower())
        print(f"  {'control: unmutated ' + kind:<40} "
              f"{_G + 'ok' + _0 if ok else _R + 'CONTROL FAILS' + _0}  {note}")
        bad += 0 if ok else 1

    for label, kind, rewrite in _MUTANTS:
        src = real if kind == "real" else pi
        try:
            mutated = rewrite(src, outputs[0] if outputs else "")
        except Exception as e:
            mutated = src
            label += f" [{type(e).__name__}]"
        if mutated == src:
            print(f"  {'mutant: ' + label:<40} {_D}no-op on this file{_0}")
            continue
        try:
            ok, note = judge(mutated, kind)
        except Exception as e:                  # a mutant the evaluator rejects
            ok, note = False, f"{type(e).__name__}: {e}"
        mark = f"{_R}MISSED{_0}" if ok else f"{_G}caught{_0}"
        bad += 1 if ok else 0
        print(f"  {'mutant: ' + label:<40} {mark}  {note[:70]}")

    print(f"\n{'self-test PASSED' if not bad else _R + 'self-test FAILED' + _0}")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        description="Check smv_builder's modules against references it did "
                    "not build.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--self-test", action="store_true",
                    help="check the checker: mutate the modules and confirm "
                         "every mutant is reported NOT EQUIVALENT")
    args = ap.parse_args()

    if args.self_test:
        return self_test(args.files[0])

    failures = checked = 0
    for path in args.files:
        name = os.path.basename(path)
        try:
            rows = verify(path)
        except Exception as e:
            rows = [(False, f"{type(e).__name__}: {e}")]
        for ok, note in rows:
            if ok is None:
                mark = f"{_D}skip{_0}"
            elif ok:
                mark = f"{_G}OK  {_0}"
                checked += 1
            else:
                mark = f"{_R}FAIL{_0}"
                checked += 1
                failures += 1
            print(f"{name:<32} {mark} {note}", flush=True)

    print(f"\n{checked} modules checked, {failures} not equivalent")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
