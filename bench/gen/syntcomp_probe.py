#!/usr/bin/env python3
"""
Synthesize SYNTCOMP specs with ltlsynt and probe each resulting strategy against
the things TraceMSR actually requires:

  1. the HOA shape scripts/converter.parse_hoa can read (Start:, AP:, --BODY--,
     `[cond] target` edges, Acceptance: 0 t);
  2. converter.check_determinism -- the "math firewall": every input valuation
     must have exactly one outgoing (edge, output-valuation) pair. ltlsynt is
     free to leave outputs unconstrained on an edge ("don't care"), which the
     firewall counts as multiple paths and rejects. This probe measures how often
     that happens, which decides whether the SYNTCOMP families are usable at all.

Emits one CSV row per spec.

Usage: syntcomp_probe.py <tlsf_root> -o probe.csv [--max-inputs 3] [-j 4]
"""
import argparse
import concurrent.futures as cf
import csv
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, '..', 'scripts'))
for p in (_HERE, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import converter                     # noqa: E402  (parse_hoa, check_determinism)
import tlsf2ltl                      # noqa: E402
import syntcomp_scan as scan         # noqa: E402
import hoa_canonicalize              # noqa: E402

CANONICALIZE = True   # set by --raw


def synthesize(ins, outs, formula, timeout):
    """Run ltlsynt; return (hoa_text, seconds) or (None, seconds)."""
    cmd = ['ltlsynt', f'--ins={",".join(ins)}', f'--outs={",".join(outs)}', '-f', formula]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, time.time() - t0
    dt = time.time() - t0
    if p.returncode != 0 or 'UNREALIZABLE' in p.stdout:
        return None, dt
    body = p.stdout.split('\n', 1)[1] if p.stdout.startswith('REALIZABLE') else p.stdout
    return body.strip(), dt


def to_tracemsr_hoa(hoa_text, ins):
    """Rewrite an ltlsynt strategy into the [system] block TraceMSR parses.

    ltlsynt already emits `State: n` + `[cond] target` with `Acceptance: 0 t`,
    so the only edit needed is dropping headers the regex parser would trip on
    and recording which AP indices are inputs.
    """
    keep = []
    for line in hoa_text.split('\n'):
        s = line.strip()
        if s.startswith(('acc-name:', 'properties:', 'controllable-AP:', 'States:', 'tool:', 'name:')):
            continue
        keep.append(line)
    return '\n'.join(keep)


def probe_one(args):
    path, root, timeout = args
    rel = os.path.relpath(path, root)
    row = {'file': rel, 'family': rel.split(os.sep)[0], 'inputs': 0, 'outputs': 0,
           'states': 0, 'edges': 0, 'synth_s': 0.0, 'outcome': '', 'detail': ''}
    try:
        ins, outs, formula, meta = tlsf2ltl.convert(path)
    except Exception as e:
        row['outcome'] = 'convert-error'
        row['detail'] = str(e)[:100]
        return row
    row['inputs'], row['outputs'] = len(ins), len(outs)
    if not outs:
        row['outcome'] = 'no-outputs'
        return row

    hoa, dt = synthesize(ins, outs, formula, timeout)
    row['synth_s'] = round(dt, 2)
    if hoa is None:
        row['outcome'] = 'unrealizable-or-timeout'
        return row

    m = re.search(r'States:\s*(\d+)', hoa)
    row['states'] = int(m.group(1)) if m else 0
    row['edges'] = len(re.findall(r'^\s*\[', hoa, re.M))

    # Write the TraceMSR [system] block and parse it exactly as the tool does.
    sysblock = to_tracemsr_hoa(hoa, ins)
    tmp = f"/tmp/probe_{os.getpid()}_{abs(hash(rel))}.txt"
    try:
        with open(tmp, 'w') as f:
            f.write("[inputs]\n" + " ".join(f'"{i}"' for i in ins) + "\n\n")
            f.write("[system]\n" + sysblock + "\n")
        p_ins, aps, start, states = converter.parse_hoa(tmp)
        if not aps:
            row['outcome'] = 'parse-no-aps'
            return row
        if CANONICALIZE:
            states = hoa_canonicalize.canonicalize(p_ins, aps, states)
        outs_parsed = [a for a in aps if a not in p_ins]
        converter.check_determinism(p_ins, outs_parsed, aps, states)
        row['outcome'] = 'ok'
    except hoa_canonicalize.IncompleteStrategy as e:
        row['outcome'] = 'incomplete-strategy'
        row['detail'] = str(e)[:120]
    except ValueError as e:
        row['outcome'] = 'nondeterministic'
        row['detail'] = str(e).replace('\n', ' ')[:120]
    except Exception as e:
        row['outcome'] = 'probe-error'
        row['detail'] = f"{type(e).__name__}: {e}"[:120]
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--max-inputs', type=int, default=3)
    ap.add_argument('--max-aps', type=int, default=12)
    ap.add_argument('--timeout', type=int, default=300)
    ap.add_argument('-j', '--jobs', type=int, default=4)
    ap.add_argument('--raw', action='store_true',
                    help='skip don\'t-care resolution (measures the firewall as-is)')
    args = ap.parse_args()

    global CANONICALIZE
    CANONICALIZE = not args.raw

    targets = []
    for dirpath, _d, files in os.walk(args.root):
        for fn in sorted(files):
            if not fn.endswith('.tlsf'):
                continue
            p = os.path.join(dirpath, fn)
            try:
                r = scan.scan_file(p, args.root)
            except Exception:
                continue
            if r['parametric'] or r['status'] == 'unrealizable':
                continue
            if r['inputs'] <= args.max_inputs and r['aps'] <= args.max_aps:
                targets.append((p, args.root, args.timeout))

    print(f"probing {len(targets)} specs (IP<={args.max_inputs}, AP<={args.max_aps})"
          f" with {args.jobs} jobs\n")
    rows = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, r in enumerate(ex.map(probe_one, targets), 1):
            rows.append(r)
            print(f"  [{i}/{len(targets)}] {r['outcome']:<24} "
                  f"IP={r['inputs']} AP={r['inputs']+r['outputs']} "
                  f"Q={r['states']:<4} {r['file']}")

    with open(args.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    print("\n=== outcomes ===")
    for k, v in Counter(r['outcome'] for r in rows).most_common():
        print(f"  {k:<26} {v}")
    ok = [r for r in rows if r['outcome'] == 'ok']
    if ok:
        print(f"\nusable: {len(ok)}/{len(rows)}   "
              f"states min/med/max = {min(r['states'] for r in ok)}/"
              f"{sorted(r['states'] for r in ok)[len(ok)//2]}/"
              f"{max(r['states'] for r in ok)}")
    print(f"\nwrote {args.out}")


if __name__ == '__main__':
    main()
