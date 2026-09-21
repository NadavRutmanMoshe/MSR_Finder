#!/usr/bin/env python3
"""
Scan a SYNTCOMP TLSF benchmark tree and report, per spec, the numbers that
decide whether TraceMSR can consume it:

  * #INPUTS  -> drives the 2^IP cause space (the hard wall: idea3 dies at IP>=4)
  * #OUTPUTS -> together with inputs gives |AP|, which drives build_real_system's
                2^|AP| python-eval unroll
  * STATUS   -> realizable / unrealizable (unrealizable = no system to synthesize)

Bus declarations (`r[n];`) are expanded using the PARAMETERS block when the
value is a plain integer; specs whose width stays symbolic are reported as
parametric so they can be instantiated explicitly later.

Usage:  syntcomp_scan.py <tlsf_root> [-o out.csv]
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter

# --------------------------------------------------------------------------- #
# TLSF lexing helpers
# --------------------------------------------------------------------------- #
_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)


def _strip_comments(text):
    """Drop /* */ blocks and // line comments, but keep the //STATUS markers
    (SYNTCOMP stores realizability in a // comment trailer)."""
    text = _BLOCK_COMMENT.sub(' ', text)
    out = []
    for line in text.split('\n'):
        if line.lstrip().startswith('//'):
            continue                      # whole-line comment: drop
        out.append(line.split('//')[0])   # trailing comment: cut
    return '\n'.join(out)


def _find_block(text, keyword):
    """Return the body of `KEYWORD { ... }`, brace-matched. None if absent."""
    m = re.search(r'\b' + keyword + r'\s*\{', text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[m.end():i - 1]


def _parse_parameters(text):
    """PARAMETERS { n = 3; m = 2; } -> {'n': 3, 'm': 2} (integer values only)."""
    body = _find_block(text, 'PARAMETERS')
    params = {}
    if not body:
        return params
    for name, val in re.findall(r'(\w+)\s*=\s*([^;]+);', body):
        try:
            params[name] = int(eval(val.strip(), {"__builtins__": {}}, dict(params)))
        except Exception:
            pass                          # symbolic / non-integer: leave unbound
    return params


def _count_signals(body, params):
    """Count declared signals in an INPUTS/OUTPUTS body.

    Returns (count, parametric) where parametric is True if some bus width could
    not be resolved to an integer."""
    if body is None:
        return 0, False
    count, parametric = 0, False
    for decl in body.split(';'):
        decl = decl.strip()
        if not decl:
            continue
        m = re.match(r'^(\w+)\s*\[([^\]]+)\]$', decl)
        if m:
            try:
                width = int(eval(m.group(2).strip(), {"__builtins__": {}}, dict(params)))
                count += max(width, 0)
            except Exception:
                parametric = True
        elif re.match(r'^\w+$', decl):
            count += 1
        else:
            parametric = True             # unrecognised declaration form
    return count, parametric


def scan_file(path, root):
    with open(path, 'r', errors='replace') as f:
        raw = f.read()

    m_status = re.search(r'//\s*STATUS\s*:\s*(\w+)', raw)
    status = m_status.group(1).lower() if m_status else 'unknown'
    m_ref = re.search(r'//\s*REF_SIZE\s*:\s*(-?\d+)', raw)
    ref_size = int(m_ref.group(1)) if m_ref else None

    text = _strip_comments(raw)
    params = _parse_parameters(text)
    main = _find_block(text, 'MAIN') or text

    n_in, p_in = _count_signals(_find_block(main, 'INPUTS'), params)
    n_out, p_out = _count_signals(_find_block(main, 'OUTPUTS'), params)

    m_sem = re.search(r'SEMANTICS\s*:\s*(\w+)', text)
    rel = os.path.relpath(path, root)
    return {
        'family': rel.split(os.sep)[0] if os.sep in rel else '.',
        'file': rel,
        'status': status,
        'ref_size': ref_size,
        'inputs': n_in,
        'outputs': n_out,
        'aps': n_in + n_out,
        'parametric': p_in or p_out,
        'semantics': (m_sem.group(1) if m_sem else ''),
    }


# --------------------------------------------------------------------------- #
# Verdict against TraceMSR's measured limits
# --------------------------------------------------------------------------- #
# From benchmark/benchmark_models.csv (gate_n family, the input-count axis):
#   IP=2 -> 2.2s, IP=3 -> 8.5s, IP=4 -> TIMEOUT even at 200s.
# build_real_system additionally does |edges| * 2^|AP| python evals.
def verdict(row):
    if row['parametric']:
        return 'parametric'
    if row['status'] == 'unrealizable':
        return 'unrealizable'
    ip, ap = row['inputs'], row['aps']
    if ip <= 3 and ap <= 10:
        return 'MSR-feasible'
    if ip <= 6 and ap <= 16:
        return 'check-only'      # too wide for 2^IP minimisation; TSR-style checking may fit
    return 'out-of-scope'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('-o', '--out', default=None)
    args = ap.parse_args()

    rows = []
    for dirpath, _dirs, files in os.walk(args.root):
        for fn in sorted(files):
            if fn.endswith('.tlsf'):
                try:
                    rows.append(scan_file(os.path.join(dirpath, fn), args.root))
                except Exception as e:
                    print(f"[warn] {fn}: {e}", file=sys.stderr)

    for r in rows:
        r['verdict'] = verdict(r)

    if args.out:
        with open(args.out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {args.out}\n")

    print(f"total specs: {len(rows)}")
    print("\n--- STATUS ---")
    for k, v in Counter(r['status'] for r in rows).most_common():
        print(f"  {k:<14} {v}")

    print("\n--- VERDICT ---")
    for k, v in Counter(r['verdict'] for r in rows).most_common():
        print(f"  {k:<14} {v}")

    real = [r for r in rows if r['status'] == 'realizable' and not r['parametric']]
    print(f"\n--- INPUT COUNT, realizable & non-parametric (n={len(real)}) ---")
    hist = Counter(r['inputs'] for r in real)
    cum = 0
    for k in sorted(hist):
        cum += hist[k]
        bar = '#' * min(hist[k], 60)
        print(f"  IP={k:<3} {hist[k]:>4}  (cum {cum:>4})  {bar}")

    print(f"\n--- AP COUNT, realizable & non-parametric ---")
    hist = Counter(r['aps'] for r in real)
    cum = 0
    for k in sorted(hist):
        cum += hist[k]
        print(f"  AP={k:<3} {hist[k]:>4}  (cum {cum:>4})")

    print("\n--- realizable specs with IP<=3 (the MSR-feasible pool) ---")
    pool = sorted([r for r in real if r['inputs'] <= 3],
                  key=lambda r: (r['inputs'], r['aps']))
    for r in pool:
        print(f"  IP={r['inputs']} OP={r['outputs']:<3} AP={r['aps']:<3} {r['file']}")
    print(f"  => {len(pool)} specs")


if __name__ == '__main__':
    main()
