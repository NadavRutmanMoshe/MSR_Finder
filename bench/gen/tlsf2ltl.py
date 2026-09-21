#!/usr/bin/env python3
"""
Minimal TLSF -> LTL converter for the *basic* TLSF subset that the SYNTCOMP
specs relevant to TraceMSR actually use.

Why not syfco: syfco is the reference converter but ships no prebuilt Linux
binary and needs a Haskell toolchain. The specs we care about (IP<=3, non
parametric) use only INPUTS/OUTPUTS plus plain-LTL blocks -- no macros, no
quantifiers, no bounded operators -- so a direct translation is safe.

Correctness is not assumed: `--validate` re-synthesises every spec with ltlsynt
and compares the verdict against the `//STATUS` annotation SYNTCOMP ships, which
is an independent oracle.

TLSF semantics (Sec. "Specification Semantics" of the TLSF report):

    (INITIALLY & G REQUIRE & ASSUME & ASSUMPTIONS)
        -> (PRESET & G(ASSERT & INVARIANTS) & GUARANTEE)

where ASSUME/ASSUMPTIONS/GUARANTEE hold plain LTL, and REQUIRE/ASSERT/INVARIANTS
are implicitly G-wrapped.

Usage:
    tlsf2ltl.py <file.tlsf>                 # print ins / outs / formula
    tlsf2ltl.py --validate <tlsf_root> [-j N] [--timeout S] [--max-inputs K]
"""
import argparse
import concurrent.futures as cf
import csv
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from syntcomp_scan import _strip_comments, _find_block, _parse_parameters  # noqa: E402

# Blocks that are implicitly G-wrapped, and the plain-LTL ones.
_ENV_G = ['REQUIRE']
_ENV_PLAIN = ['ASSUME', 'ASSUMPTIONS']
_ENV_INIT = ['INITIALLY']
# GUARANTEE/GUARANTEES are plain LTL. (G-wrapping the plural form reproduces
# lilydemo15/16's //STATUS but breaks the whole ltl2dba family, 94% -> 75%
# agreement, so the lily cases are annotation/semantics artefacts, not a dialect
# rule -- they are reported by --validate and excluded from the usable pool.)
_SYS_G = ['ASSERT', 'INVARIANTS']
_SYS_PLAIN = ['GUARANTEE', 'GUARANTEES']
_SYS_INIT = ['PRESET']


def _signals(body, params):
    """Expand an INPUTS/OUTPUTS body into concrete signal names."""
    names = []
    if body is None:
        return names
    for decl in body.split(';'):
        decl = decl.strip()
        if not decl:
            continue
        m = re.match(r'^(\w+)\s*\[([^\]]+)\]$', decl)
        if m:
            width = int(eval(m.group(2).strip(), {"__builtins__": {}}, dict(params)))
            names.extend(f"{m.group(1)}_{i}" for i in range(width))
        elif re.match(r'^\w+$', decl):
            names.append(decl)
        else:
            raise ValueError(f"unsupported signal declaration: {decl!r}")
    return names


def _entries(main, keyword):
    """Split a block body into its `;`-separated LTL entries."""
    body = _find_block(main, keyword)
    if body is None:
        return []
    return [e.strip() for e in body.split(';') if e.strip()]


def _to_spot(expr):
    """TLSF expression syntax -> spot LTL syntax."""
    # Bus references `HBURST[0]` must match the expanded signal name `HBURST_0`.
    expr = re.sub(r'\b(\w+)\s*\[\s*(\d+)\s*\]', r'\1_\2', expr)
    expr = expr.replace('&&', ' & ').replace('||', ' | ')
    # TLSF writes strong until as `U!`; spot's `U` is already strong.
    expr = re.sub(r'\bU\s*!\s*(?=[\w(!])', ' U ', expr)
    return f"({expr})"


def build_renaming(names):
    """Map TLSF signal names to names spot/ltlsynt can actually round-trip.

    Two spot 2.15.1 quirks force this:
      * the LTL lexer glues an operator letter onto a following identifier, so
        `X HMASTLOCK` parses as the single AP `XHMASTLOCK` -- fixed by quoting;
      * `--ins`/`--outs` fail to match ANY name containing an uppercase letter
        ("one of --ins or --outs should match 'HMASTLOCK'"), even quoted --
        fixed by lowercasing.

    Returns {original: sanitized}; collisions get a numeric suffix.
    """
    mapping, used = {}, set()
    for n in names:
        base = n.lower()
        if not re.match(r'^[a-z_]', base):
            base = 'v' + base
        cand, k = base, 2
        while cand in used:
            cand, k = f"{base}_{k}", k + 1
        used.add(cand)
        mapping[n] = cand
    return mapping


def _quote_aps(formula, mapping):
    """Rewrite signal occurrences to their sanitized names, double-quoted."""
    if not mapping:
        return formula
    # Longest first so a name that is a prefix of another cannot shadow it;
    # one pass so renamed text is never re-matched.
    alt = "|".join(re.escape(n) for n in sorted(mapping, key=len, reverse=True))
    return re.sub(r'\b(?:' + alt + r')\b',
                  lambda m: f'"{mapping[m.group(0)]}"', formula)


def _conj(parts):
    parts = [p for p in parts if p]
    if not parts:
        return None
    return " & ".join(parts)


def convert(path):
    """Return (inputs, outputs, ltl_formula, meta)."""
    with open(path, 'r', errors='replace') as f:
        raw = f.read()

    m_status = re.search(r'//\s*STATUS\s*:\s*(\w+)', raw)
    status = m_status.group(1).lower() if m_status else 'unknown'

    text = _strip_comments(raw)
    params = _parse_parameters(text)
    main = _find_block(text, 'MAIN')
    if main is None:
        raise ValueError("no MAIN section")

    inputs = _signals(_find_block(main, 'INPUTS'), params)
    outputs = _signals(_find_block(main, 'OUTPUTS'), params)

    m_sem = re.search(r'SEMANTICS\s*:\s*(\w+)', text)
    semantics = m_sem.group(1) if m_sem else 'Mealy'

    def gather(keys, wrap_g):
        out = []
        for k in keys:
            for e in _entries(main, k):
                s = _to_spot(e)
                out.append(f"G{s}" if wrap_g else s)
        return out

    env = gather(_ENV_INIT, False) + gather(_ENV_G, True) + gather(_ENV_PLAIN, False)
    sys_ = gather(_SYS_INIT, False) + gather(_SYS_G, True) + gather(_SYS_PLAIN, False)

    assume = _conj(env)
    guarantee = _conj(sys_)
    if guarantee is None:
        guarantee = "1"
    formula = f"({assume}) -> ({guarantee})" if assume else guarantee

    renaming = build_renaming(inputs + outputs)
    formula = _quote_aps(formula, renaming)
    inputs = [renaming[n] for n in inputs]
    outputs = [renaming[n] for n in outputs]

    return inputs, outputs, formula, {
        'status': status,
        'semantics': semantics,
        'renaming': {k: v for k, v in renaming.items() if k != v},
    }


# --------------------------------------------------------------------------- #
# Validation against the //STATUS oracle
# --------------------------------------------------------------------------- #
def _check_one(args):
    path, root, timeout = args
    rel = os.path.relpath(path, root)
    try:
        ins, outs, f, meta = convert(path)
    except Exception as e:
        return {'file': rel, 'expected': '?', 'got': 'CONVERT-ERROR',
                'agree': False, 'note': str(e)[:80], 'inputs': 0, 'outputs': 0}

    if not outs:
        return {'file': rel, 'expected': meta['status'], 'got': 'NO-OUTPUTS',
                'agree': False, 'note': '', 'inputs': len(ins), 'outputs': 0}

    cmd = ['ltlsynt', '--realizability', f'--ins={",".join(ins)}' if ins else '--ins=',
           f'--outs={",".join(outs)}', '-f', f]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        got = p.stdout.strip().split('\n')[0].strip().lower()
        if got not in ('realizable', 'unrealizable'):
            got = f'ERR:{(p.stderr.strip().splitlines() or [""])[0][:60]}'
    except subprocess.TimeoutExpired:
        got = 'timeout'

    return {'file': rel, 'expected': meta['status'], 'got': got,
            'agree': got == meta['status'], 'note': '',
            'inputs': len(ins), 'outputs': len(outs)}


def validate(root, jobs, timeout, max_inputs):
    import syntcomp_scan as scan
    targets = []
    for dirpath, _d, files in os.walk(root):
        for fn in sorted(files):
            if not fn.endswith('.tlsf'):
                continue
            p = os.path.join(dirpath, fn)
            try:
                row = scan.scan_file(p, root)
            except Exception:
                continue
            if row['status'] in ('realizable', 'unrealizable') and not row['parametric'] \
                    and row['inputs'] <= max_inputs:
                targets.append((p, root, timeout))

    print(f"validating {len(targets)} annotated specs (IP<={max_inputs}) with {jobs} jobs...\n")
    results = []
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for i, r in enumerate(ex.map(_check_one, targets), 1):
            results.append(r)
            if not r['agree']:
                print(f"  [{i}/{len(targets)}] MISMATCH {r['file']}: "
                      f"expected {r['expected']}, got {r['got']} {r['note']}")

    agree = sum(1 for r in results if r['agree'])
    to = sum(1 for r in results if r['got'] == 'timeout')
    print(f"\n=== {agree}/{len(results)} agree with //STATUS "
          f"({100.0*agree/max(len(results),1):.1f}%), {to} ltlsynt timeouts ===")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('-j', '--jobs', type=int, default=4)
    ap.add_argument('--timeout', type=int, default=60)
    ap.add_argument('--max-inputs', type=int, default=3)
    ap.add_argument('-o', '--out', default=None)
    args = ap.parse_args()

    if args.validate:
        res = validate(args.target, args.jobs, args.timeout, args.max_inputs)
        if args.out:
            with open(args.out, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(res[0].keys()))
                w.writeheader()
                w.writerows(res)
            print(f"wrote {args.out}")
        return

    ins, outs, f, meta = convert(args.target)
    print(f"# status={meta['status']} semantics={meta['semantics']}")
    print(f"ins  = {','.join(ins)}")
    print(f"outs = {','.join(outs)}")
    print(f"ltl  = {f}")


if __name__ == '__main__':
    main()
