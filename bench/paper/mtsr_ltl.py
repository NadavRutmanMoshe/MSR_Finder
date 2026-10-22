#!/usr/bin/env python3
"""
mtsr_ltl -- can an MTSR automaton be written as LTL, and how large is the formula?

    ~/micromamba/envs/spot/bin/python3 bench/paper/mtsr_ltl.py [--max-size 7]
    in : claude_scripts/results/bench_stats/mtsr_hoa.json   (bench_stats_hoa.py)
    out: claude_scripts/results/bench_stats/mtsr_ltl.csv

Three answers per automaton, each exact in the direction it claims:

1. COUNTER-FREE  (=> LTL-definable).  Determinize (spot, generic acceptance),
   complete, and build the transition monoid.  If it is aperiodic the automaton
   is counter-free, and deterministic counter-free Muller automata recognize
   only first-order = LTL-definable languages (McNaughton-Papert / Thomas).
   Sufficient, not necessary: a non-minimal automaton can carry a spurious
   counter.

2. NOT LTL  (a proof).  For every non-aperiodic monoid element u, look for
   words x, y with  x u^M y^w in L  <=/=>  x u^(M+1) y^w in L  for M >= |D|.
   On a map over |D| points, u^n is periodic from n = |D| on, so if L were
   aperiodic membership would be constant there; a flip is a witness that L is
   not LTL-definable.  spot decides both memberships.

3. SMALLEST FORMULA.  Exhaustive bottom-up enumeration of LTL over the inputs,
   operators {!, &, |, X, F, G, U} plus true/false, size = AST nodes.  Formulas
   are deduplicated by their truth value at every position of a fixed sample of
   lasso words (computed compositionally with bit-parallel fixpoints); a
   candidate is matched to an automaton by its position-0 bits and then CONFIRMED
   with spot.are_equivalent, so a reported formula is always really equivalent.
   Sampling can only err towards reporting a formula LARGER than the true
   minimum (a class merged away), never a wrong one; the dedup is audited on a
   random sample of merges and the audit count is printed.
"""
import argparse
import csv
import itertools
import json
import os
import random
import sys
import time

import buddy
import spot

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "out", "ltl_rerun")      # ltl_rerun_catsrl.py sets it
SHAPES = [(p, l) for p in range(0, 9) for l in range(1, 5)]  # p > X-depth of any size-8 formula
WORDS_PER_SHAPE = 200
MONOID_CAP = 20000
CLASS_CAP = 120000   # ~2 GB of signatures


# ------------------------------------------------------------ word sample ---
class Sample:
    """Lasso words grouped by shape (p, l); a formula's signature is, per shape,
    one big int with bit (w*n + t) = truth at position t of word w."""

    def __init__(self, k, seed=0):
        rnd = random.Random(seed)
        A = 1 << k
        self.k, self.shapes = k, []
        for p, l in SHAPES:
            n = p + l
            if A ** n <= WORDS_PER_SHAPE:
                words = list(itertools.product(range(A), repeat=n))
            else:
                words = list({tuple(rnd.randrange(A) for _ in range(n))
                              for _ in range(WORDS_PER_SHAPE * 2)})[:WORDS_PER_SHAPE]
            W = len(words)
            last = sum(1 << (w * n + n - 1) for w in range(W))
            full = (1 << (W * n)) - 1
            pos0 = sum(1 << (w * n) for w in range(W))
            atoms = []
            for j in range(k):
                v = 0
                for w, word in enumerate(words):
                    for t, letter in enumerate(word):
                        if letter >> j & 1:
                            v |= 1 << (w * n + t)
                atoms.append(v)
            self.shapes.append(dict(p=p, l=l, n=n, words=words, full=full,
                                    last=last, notlast=full & ~last, pos0=pos0,
                                    atoms=atoms))

    # bit-parallel operators, one shape at a time
    @staticmethod
    def X(s, v):
        return ((v >> 1) & s["notlast"]) | ((v << (s["l"] - 1)) & s["last"])

    def lfp(self, s, base, guard):
        z = base
        for _ in range(s["n"] + 1):
            nz = base | (guard & self.X(s, z))
            if nz == z:
                break
            z = nz
        return z

    def op(self, name, a, b=None):
        out = []
        for i, s in enumerate(self.shapes):
            x = a[i]
            y = b[i] if b is not None else None
            if name == "!":
                r = s["full"] & ~x
            elif name == "X":
                r = self.X(s, x)
            elif name == "F":
                r = self.lfp(s, x, s["full"])
            elif name == "G":
                r = s["full"] & ~self.lfp(s, s["full"] & ~x, s["full"])
            elif name == "&":
                r = x & y
            elif name == "|":
                r = x | y
            elif name == "U":
                r = self.lfp(s, y, x)
            out.append(r)
        return tuple(out)

    def pos0(self, sig):
        return tuple(v & s["pos0"] for v, s in zip(sig, self.shapes))


def enumerate_formulas(k, max_size, budget_s):
    """{sig: (size, formula_str)} for all formulas up to max_size, and the
    position-0 index {pos0sig: [(size, formula), ...]} sorted by size."""
    S = Sample(k)
    names = [f'"i{j}"' for j in range(k)]
    by_size = {1: []}
    seen = {}
    merges = []
    rnd = random.Random(k)
    nmerge = [0]

    def add(size, sig, f):
        if sig in seen:
            # reservoir sample of all merges, audited against spot afterwards
            nmerge[0] += 1
            if len(merges) < 1000:
                merges.append((seen[sig][1], f))
            else:
                j = rnd.randrange(nmerge[0])
                if j < 1000:
                    merges[j] = (seen[sig][1], f)
            return
        seen[sig] = (size, f)
        by_size[size].append((sig, f))

    add(1, tuple(0 for _ in S.shapes), "false")
    add(1, tuple(s["full"] for s in S.shapes), "true")
    for j in range(k):
        add(1, tuple(s["atoms"][j] for s in S.shapes), names[j])
    t0 = time.time()
    reached = 1
    for size in range(2, max_size + 1):
        by_size[size] = []
        for sig, f in by_size[size - 1]:
            for u in ("!", "X", "F", "G"):
                add(size, S.op(u, sig), f"({u} {f})" if u != "!" else f"(! {f})")
        for ls in range(1, size - 1):
            rs = size - 1 - ls
            for sa, fa in by_size[ls]:
                for sb, fb in by_size[rs]:
                    if ls <= rs:  # & and | commute
                        add(size, S.op("&", sa, sb), f"({fa} & {fb})")
                        add(size, S.op("|", sa, sb), f"({fa} | {fb})")
                    add(size, S.op("U", sa, sb), f"({fa} U {fb})")
            if time.time() - t0 > budget_s:
                break
        if time.time() - t0 > budget_s:
            print(f"  k={k}: budget hit inside size {size}; keeping sizes < {size}",
                  flush=True)
            for sig in [sg for sg, _ in by_size[size]]:
                del seen[sig]
            del by_size[size]
            break
        reached = size
        if len(seen) * 5 > CLASS_CAP:   # the next size grows ~5x
            print(f"  k={k}: {len(seen)} classes > CLASS_CAP after size {size}; "
                  f"stopping (memory)", flush=True)
            break
        print(f"  k={k} size {size}: {len(by_size[size])} new classes "
              f"({len(seen)} total, {time.time() - t0:.0f}s)", flush=True)
    index = {}
    for sig, (size, f) in seen.items():
        index.setdefault(S.pos0(sig), []).append((size, f))
    for v in index.values():
        v.sort()
    return S, index, reached, merges


def audit(merges, n=300):
    bad = 0
    sample = random.Random(1).sample(merges, min(n, len(merges)))
    for a, b in sample:
        if not spot.are_equivalent(spot.formula(a), spot.formula(b)):
            bad += 1
    return len(sample), bad


# ------------------------------------------------------------- automata -----
def letter_table(D, inputs):
    """trans[state][letter] = (dst, mark_int) on a complete deterministic D."""
    k = len(inputs)
    d = D.get_dict()
    cubes = []
    for L in range(1 << k):
        b = buddy.bddtrue
        for j, ap in enumerate(inputs):
            v = buddy.bdd_ithvar(D.register_ap(ap))
            b = b & (v if L >> j & 1 else buddy.bdd_not(v))
        cubes.append(b)
    trans = []
    for s in range(D.num_states()):
        row = [None] * (1 << k)
        for e in D.out(s):
            for L in range(1 << k):
                if row[L] is None and (e.cond & cubes[L]) != buddy.bddfalse:
                    row[L] = (e.dst, sum(1 << b for b in e.acc.sets()))
        trans.append(row)
    return trans


def accepts(D, trans, acc_cache, prefix, loop, q0):
    q = q0
    for L in prefix:
        q = trans[q][L][0]
    seen = {}
    marks_seq = []
    i = 0
    while (q, i % len(loop)) not in seen:
        seen[(q, i % len(loop))] = len(marks_seq)
        dst, m = trans[q][loop[i % len(loop)]]
        marks_seq.append(m)
        q = dst
        i += 1
    start = seen[(q, i % len(loop))]
    m = 0
    for x in marks_seq[start:]:
        m |= x
    if m not in acc_cache:
        acc_cache[m] = D.acc().accepting(
            spot.mark_t([b for b in range(32) if m >> b & 1]))
    return acc_cache[m]


def monoid(trans, n, nletters):
    gens = [tuple(trans[q][L][0] for q in range(n)) for L in range(nletters)]
    ident = tuple(range(n))
    words = {ident: ()}
    frontier = [ident]
    while frontier:
        nf = []
        for f in frontier:
            for L, g in enumerate(gens):
                h = tuple(g[f[q]] for q in range(n))
                if h not in words:
                    words[h] = words[f] + (L,)
                    nf.append(h)
                    if len(words) > MONOID_CAP:
                        return words, False
        frontier = nf
    return words, True


def is_aperiodic_elem(f):
    """f^i = f^(i+1) for some i, i.e. the power sequence ends in a 1-cycle."""
    n = len(f)
    seen, cur, i = {}, f, 1
    while cur not in seen:
        seen[cur] = i
        cur = tuple(f[cur[q]] for q in range(n))
        i += 1
    return i - seen[cur] == 1


def word_str(inputs, letters):
    out = []
    for L in letters:
        out.append(" & ".join(f'"{ap}"' if L >> j & 1 else f'!"{ap}"'
                              for j, ap in enumerate(inputs)) or "1")
    return out


def member(A, inputs, prefix, loop):
    pre = word_str(inputs, prefix)
    lp = word_str(inputs, loop)
    s = "; ".join(pre + [f"cycle{{{'; '.join(lp)}}}"])
    w = spot.parse_word(s, A.get_dict())
    return A.intersects(w.as_automaton())


def non_ltl_witness(A, D, trans, inputs, words):
    n = D.num_states()
    nl = 1 << len(inputs)
    q0 = D.get_init_state_number()
    # shortest access word per reachable state
    access = {q0: ()}
    fr = [q0]
    while fr:
        nf = []
        for q in fr:
            for L in range(nl):
                d = trans[q][L][0]
                if d not in access:
                    access[d] = access[q] + (L,)
                    nf.append(d)
        fr = nf
    ys = [y for ln in (1, 2) for y in itertools.product(range(nl), repeat=ln)]
    tried = 0
    for f, u in sorted(words.items(), key=lambda kv: len(kv[1])):
        if not u or is_aperiodic_elem(f):
            continue
        for x in access.values():
            for y in ys:
                tried += 1
                if tried > 400:
                    return None
                M = n + 1
                a = member(A, inputs, x + u * M, y)
                b = member(A, inputs, x + u * (M + 1), y)
                if a != b:
                    return (x, u, y)
        break  # only the shortest counter word; enough in practice
    return None


def analyse(entry, index_by_k, sample_by_k):
    inputs = entry["inputs"]
    k = len(inputs)
    A = spot.automaton(entry["hoa"])
    for ap in inputs:
        A.register_ap(ap)
    # Counter-freeness is a property of the AUTOMATON, and a non-minimal one can
    # carry a spurious counter; try a few deterministic forms, any one suffices.
    forms = [spot.complete(spot.postprocess(A, "generic", "deterministic", lvl))
             for lvl in ("low", "high")]
    forms.append(spot.complete(spot.postprocess(A, "parity", "deterministic", "high")))
    mo = spot.minimize_obligation(A)       # minimal WDBA when A is weak
    if mo is not None and mo.is_deterministic():
        forms.append(spot.complete(mo))
    counter_free, complete = False, True
    for D0 in sorted(forms, key=lambda a: a.num_states()):
        t0 = letter_table(D0, inputs)
        w0, c0 = monoid(t0, D0.num_states(), 1 << k)
        cf = c0 and all(is_aperiodic_elem(f) for f in w0)
        if cf:
            counter_free = True
            break
    D = min(forms, key=lambda a: a.num_states())
    n = D.num_states()
    trans = letter_table(D, inputs)
    words, complete = monoid(trans, n, 1 << k)
    res = {"k": k, "det_states": n, "monoid": len(words) if complete else f">{MONOID_CAP}",
           "counter_free": counter_free, "non_ltl_witness": "", "ltl": "",
           "ltl_size": "", "universal": False, "empty": False}
    res["empty"] = A.is_empty()
    res["universal"] = spot.complement(A).is_empty()
    if not counter_free and complete:
        w = non_ltl_witness(A, D, trans, inputs, words)
        if w:
            res["non_ltl_witness"] = repr(w)
    # smallest formula
    if k in index_by_k:
        S = sample_by_k[k]
        q0 = D.get_init_state_number()
        cache = {}
        key = []
        for s in S.shapes:
            v = 0
            p = s["p"]
            for wi, word in enumerate(s["words"]):
                if accepts(D, trans, cache, word[:p], word[p:], q0):
                    v |= 1 << (wi * s["n"])
            key.append(v)
        cands = index_by_k[k].get(tuple(key), [])
        ren = {f"i{j}": ap for j, ap in enumerate(inputs)}
        for size, f in cands:
            fs = f
            for j in range(k - 1, -1, -1):
                fs = fs.replace(f'"i{j}"', f'"@@{j}"')
            for j in range(k):
                fs = fs.replace(f'"@@{j}"', f'"{inputs[j]}"')
            if spot.are_equivalent(spot.translate(spot.formula(fs)), A):
                res["ltl"], res["ltl_size"] = f, size
                break
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-size", type=int, default=7)
    ap.add_argument("--budget", type=float, default=240,
                    help="seconds of enumeration per input count")
    args = ap.parse_args()
    data = json.load(open(os.path.join(DIR, "mtsr_hoa.json")))
    ks = sorted({len(v["inputs"]) for v in data.values() if "hoa" in v})
    index_by_k, sample_by_k, reached = {}, {}, {}
    audits = {}
    for k in ks:
        S, idx, r, merges = enumerate_formulas(k, args.max_size, args.budget)
        index_by_k[k], sample_by_k[k], reached[k] = idx, S, r
        audits[k] = audit(merges)
        print(f"k={k}: complete up to size {r}; dedup audit "
              f"{audits[k][1]} wrong of {audits[k][0]} merges", flush=True)
    json.dump({"reached": reached, "audit": audits},
              open(os.path.join(DIR, "mtsr_ltl_meta.json"), "w"))
    out = os.path.join(DIR, "mtsr_ltl.csv")
    fields = ["key", "k", "states", "det_states", "monoid", "counter_free",
              "non_ltl_witness", "empty", "universal", "ltl", "ltl_size",
              "search_bound"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for i, (key, e) in enumerate(sorted(data.items()), 1):
            if "hoa" not in e:
                continue
            r = analyse(e, index_by_k, sample_by_k)
            r.update(key=key, states=e["states"], search_bound=reached.get(r["k"], ""))
            w.writerow(r)
            if i % 100 == 0:
                print(f"  {i}/{len(data)}", flush=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
