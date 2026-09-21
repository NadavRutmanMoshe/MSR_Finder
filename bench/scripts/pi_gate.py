#!/usr/bin/env python3
"""
pi_gate.py -- the waterfall's PI_VAL gate, done with spot alone.

    ~/micromamba/envs/spot/bin/python3 bench/scripts/pi_gate.py <dir-or-files...> \
        [-o passing.txt] [--csv gate.csv] [--quiet]

Why a second implementation of the gate
---------------------------------------
`hyperqb_validate.pi_val` runs the gate through the SMV/HyperQB stack, which
needs the whole solver chain up.  For a tool comparison all that is wanted is
the same two questions answered cheaply and exactly:

    (1) is the [lasso] an actual word of the [system]?
    (2) does the [effect] HOLD on that word?

Both are decided by an emptiness check on a product with spot, in milliseconds,
with no external solver.  A word is a one-word automaton, so

    (1)  product(word, system)          non-empty
    (2)  product(word, !effect)         empty

This matters for the CORP and CATS comparisons because a *causal* question is
only well posed when the effect actually occurred: CORP answers "No cause
exists" and CATS's cause check fails at PC1 whenever the effect fails on the
trace, so an ungated benchmark measures the two tools' rejection paths rather
than their synthesis/checking work.  See
[msr-benchmarks-need-the-pival-gate]: 46% of a SYNTCOMP sample fails this.

Verdicts:
    OK             word is a system trace and satisfies the effect
    NOT_A_TRACE    the lasso is not a word of the system
    EFFECT_FALSE   the lasso is a system word but the effect fails on it
    ERROR          parse/translate failure (message in the CSV)
"""
import argparse
import csv
import glob
import os
import re
import sys

import spot

_HEADERS = ('info', 'inputs', 'cause', 'effect', 'lasso', 'system')


def parse_instance(path):
    data, cur, buf = {}, None, []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith('[') and s.endswith(']') and s[1:-1] in _HEADERS:
                if cur:
                    data[cur] = "".join(buf).strip()
                cur, buf = s[1:-1], []
            elif cur:
                buf.append(line)
    if cur:
        data[cur] = "".join(buf).strip()
    return data


def parse_lasso(text):
    aps, prefix, loop = [], [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("AP:"):
            aps = re.findall(r'"([^"]+)"', line)
        elif line.startswith("Prefix:"):
            prefix = re.findall(r'\{([^}]*)\}', line)
        elif line.startswith("Loop:"):
            loop = re.findall(r'\{([^}]*)\}', line)
    return aps, prefix, loop


def lasso_to_word(aps, prefix, loop):
    def step(idx):
        on = set(int(i) for i in idx.split()) if idx.strip() else set()
        return "(" + " & ".join(('"%s"' % a) if i in on else ('!"%s"' % a)
                                for i, a in enumerate(aps)) + ")"
    body = "; ".join(step(s) for s in prefix)
    if loop:
        cyc = "cycle{" + "; ".join(step(s) for s in loop) + "}"
        body = (body + "; " + cyc) if body else cyc
    return body


def check(path):
    data = parse_instance(path)
    for k in ('system', 'effect', 'lasso'):
        if k not in data:
            return "ERROR", f"missing [{k}]", None
    aps, prefix, loop = parse_lasso(data['lasso'])
    if not loop:
        return "ERROR", "lasso has no Loop", None
    word_str = lasso_to_word(aps, prefix, loop)
    try:
        word = spot.parse_word(word_str).as_automaton()
        system = spot.automaton(data['system'] + "\n")
        neg_eff = spot.translate(spot.formula("!(" + data['effect'] + ")"))
    except Exception as e:
        return "ERROR", f"{type(e).__name__}: {e}"[:160], word_str

    # The word automaton and the system may declare their APs in different
    # orders; spot's product matches by name, so this is safe.
    if spot.product(word, system).is_empty():
        return "NOT_A_TRACE", "", word_str
    if not spot.product(word, neg_eff).is_empty():
        return "EFFECT_FALSE", "", word_str
    return "OK", "", word_str


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.txt")))
        else:
            files += sorted(glob.glob(p))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('-o', '--out', help="write the passing file paths, one per line")
    ap.add_argument('--csv', help="write every verdict here")
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    files = collect(args.paths)
    rows, passing = [], []
    counts = {}
    for i, p in enumerate(files, 1):
        verdict, msg, _ = check(p)
        counts[verdict] = counts.get(verdict, 0) + 1
        rows.append({"file": os.path.basename(p), "path": p,
                     "verdict": verdict, "detail": msg})
        if verdict == "OK":
            passing.append(p)
        if not args.quiet:
            print(f"  [{i}/{len(files)}] {verdict:<13} {os.path.basename(p)}"
                  + (f"  {msg}" if msg else ""), flush=True)

    print("\n=== PI_VAL gate ===")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<13} {v:>5}  ({100.0 * v / max(1, len(files)):.1f}%)")
    print(f"  {'TOTAL':<13} {len(files):>5}")

    if args.out:
        with open(args.out, 'w') as f:
            f.write("\n".join(passing) + ("\n" if passing else ""))
        print(f"\n{len(passing)} passing paths -> {args.out}")
    if args.csv:
        with open(args.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=["file", "path", "verdict", "detail"])
            w.writeheader()
            w.writerows(rows)
        print(f"verdicts -> {args.csv}")


if __name__ == '__main__':
    main()
