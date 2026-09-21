#!/usr/bin/env python3
"""
Turn SYNTCOMP TLSF specs into TraceMSR example files for the MSR/MTSR sweep.

MUST be run under the spot environment python:
    ~/micromamba/envs/spot/bin/python3 claude_scripts/syntcomp_gen.py ...

Pipeline per spec:

    .tlsf --(tlsf2ltl)--> LTL+ins/outs --(ltlsynt)--> strategy HOA
          --(hoa_canonicalize)--> concrete input-deterministic Mealy machine
          --> N non-vacuous EFFECTS --> one .txt per effect

Why effects, not traces, are the multiplier: CATS pairs each system with 10
random lasso traces because it *checks* a cause on a trace. Our target flow --
`tracemsr.py --run 2,4` (MSR for Phi / find a PI, and MTSR) -- reads only
[inputs]/[effect]/[system] and ignores the [lasso] entirely, so the instance is
determined by the effect. A [lasso] block is still emitted so option 1
(MSR with the given PI) can be run on the same files.

Every effect passes effect_gen's non-vacuity filter: it must be both reachable
and avoidable in the system, else the MSR cause is empty / MTSR is `true`.
"""
import argparse
import itertools
import os
import random
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH_SCRIPTS = os.path.abspath(os.path.join(_HERE, '..', 'scripts'))
_TOOL_SCRIPTS = os.path.abspath(os.path.join(_HERE, '..', '..', 'scripts'))
for p in (_HERE, _BENCH_SCRIPTS, _TOOL_SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import converter                # noqa: E402
import tlsf2ltl                 # noqa: E402
import syntcomp_scan as scan    # noqa: E402
import syntcomp_probe as probe  # noqa: E402
import hoa_canonicalize         # noqa: E402
import effect_gen as eg         # noqa: E402  (needs spot)


def _parse_full_cond(cond, n_aps):
    """`!0&1&!2` -> (False, True, False). Only full conjunctions (canonical)."""
    vals = [None] * n_aps
    for lit in cond.split('&'):
        lit = lit.strip()
        neg = lit.startswith('!')
        idx = int(lit[1:] if neg else lit)
        vals[idx] = not neg
    if any(v is None for v in vals):
        raise ValueError(f"not a full conjunction: {cond}")
    return tuple(vals)


def build_step_table(inputs, aps, states):
    """{state: {input_valuation: (full_valuation, target)}} for a canonical HOA."""
    ap_idx = {a: i for i, a in enumerate(aps)}
    in_idx = [ap_idx[i] for i in inputs]
    table = {}
    for sid, edges in states.items():
        m = {}
        for cond, target in edges:
            val = _parse_full_cond(cond, len(aps))
            m[tuple(val[i] for i in in_idx)] = (val, target)
        table[sid] = m
    return table


def random_lasso(table, start, n_inputs, rng, max_prefix=3, max_loop=3):
    """Drive the machine with an eventually-periodic input word and read off the
    lasso it induces. Returns (prefix_vals, loop_vals) as full AP valuations."""
    combos = list(itertools.product([False, True], repeat=n_inputs))
    pre_in = [rng.choice(combos) for _ in range(rng.randint(0, max_prefix))]
    loop_in = [rng.choice(combos) for _ in range(rng.randint(1, max_loop))]

    trace, state = [], start
    for iv in pre_in:
        if iv not in table[state]:
            return None
        val, state = table[state][iv]
        trace.append(val)

    # Cycle the periodic part until (state, phase) repeats -- guaranteed, since
    # both are finite, and that repeat is exactly the lasso's loop start.
    seen, phase = {}, 0
    while True:
        key = (state, phase)
        if key in seen:
            return trace[:seen[key]], trace[seen[key]:]
        seen[key] = len(trace)
        iv = loop_in[phase]
        if iv not in table[state]:
            return None
        val, state = table[state][iv]
        trace.append(val)
        phase = (phase + 1) % len(loop_in)


def _sets(vals):
    """Valuations -> `{0 2} {1}` index-set notation."""
    return ' '.join('{' + ' '.join(str(i) for i, b in enumerate(v) if b) + '}'
                    for v in vals)


def build_system(path, timeout):
    """.tlsf -> (sysblock, inputs, aps, start, states) or raises/returns status."""
    ins, outs, formula, _meta = tlsf2ltl.convert(path)
    if not outs:
        return None, 'no-outputs'
    hoa, _dt = probe.synthesize(ins, outs, formula, timeout)
    if hoa is None:
        return None, 'unrealizable-or-timeout'

    tmp = f"/tmp/gen_{os.getpid()}_{abs(hash(path))}.raw"
    with open(tmp, 'w') as f:
        f.write("[inputs]\n" + " ".join(f'"{i}"' for i in ins) + "\n\n")
        f.write("[system]\n" + probe.to_tracemsr_hoa(hoa, ins) + "\n")
    try:
        p_ins, aps, start, states = converter.parse_hoa(tmp)
    finally:
        os.remove(tmp)
    if not aps:
        return None, 'parse-no-aps'

    states = hoa_canonicalize.canonicalize(p_ins, aps, states)
    sysblock = hoa_canonicalize.emit_hoa(aps, start, states)
    return (sysblock, p_ins, aps, start, states), 'ok'


def gen_one(path, root, outdir, n_effects, seed, timeout):
    rel = os.path.relpath(path, root)
    stem = re.sub(r'[^A-Za-z0-9]+', '_', os.path.splitext(rel)[0]).strip('_')
    try:
        built, status = build_system(path, timeout)
    except hoa_canonicalize.IncompleteStrategy:
        return rel, [], 'incomplete-strategy', Counter()
    except Exception as e:
        return rel, [], f'error: {type(e).__name__}'[:40], Counter()
    if built is None:
        return rel, [], status, Counter()

    sysblock, p_ins, aps, start, states = built
    outputs = [a for a in aps if a not in p_ins]
    table = build_step_table(p_ins, aps, states)
    rng = random.Random(f"{stem}:{seed}")

    try:
        aut = eg.load_system(sysblock)
    except Exception:
        return rel, [], 'spot-parse-failed', Counter()

    chosen, stats = eg.pick_effects(aut, outputs, n_effects, seed, rng)
    if not chosen:
        return rel, [], 'no-nonvacuous-effect', stats

    made = []
    for k, (source, eff) in enumerate(chosen):
        lasso = random_lasso(table, start, len(p_ins), rng)
        name = f"{stem}__e{k:02d}.txt"
        with open(os.path.join(outdir, name), 'w') as f:
            f.write(f"[info]\nSYNTCOMP: {rel} (states={len(states)}, "
                    f"inputs={len(p_ins)}, aps={len(aps)}) "
                    f"effect #{k} source={source}\n\n")
            f.write("[inputs]\n" + " ".join(f'"{i}"' for i in p_ins) + "\n\n")
            f.write(f"[effect]\n{eff}\n\n")
            if lasso and lasso[1]:
                f.write("[lasso]\n")
                f.write("AP: " + " ".join(f'"{a}"' for a in aps) + "\n")
                f.write("Prefix: " + _sets(lasso[0]) + "\n")
                f.write("Loop: " + _sets(lasso[1]) + "\n\n")
            f.write("[system]\n" + sysblock + "\n")
        made.append(name)
    return rel, made, 'ok', stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('-o', '--outdir', required=True)
    ap.add_argument('--effects', type=int, default=10)
    ap.add_argument('--max-inputs', type=int, default=3)
    ap.add_argument('--max-aps', type=int, default=12)
    ap.add_argument('--timeout', type=int, default=300)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
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
                targets.append(p)
    if args.limit:
        targets = targets[:args.limit]

    print(f"generating from {len(targets)} specs -> {args.outdir}\n")
    total, stats, per_spec = 0, Counter(), Counter()
    # Sequential: spot objects are not thread-safe.
    for i, p in enumerate(targets, 1):
        rel, made, status, st = gen_one(p, args.root, args.outdir,
                                        args.effects, args.seed, args.timeout)
        per_spec[status] += 1
        stats += st
        total += len(made)
        print(f"  [{i}/{len(targets)}] {status:<22} {len(made):>2} files  {rel}")

    print("\n=== per-spec status ===")
    for k, v in per_spec.most_common():
        print(f"  {k:<26} {v}")

    print("\n=== effect filter yield (candidate classifications) ===")
    for src in ('template', 'randltl'):
        tot = sum(v for k, v in stats.items() if k.startswith(src + ':'))
        if not tot:
            continue
        ok = stats.get(f'{src}:ok', 0)
        print(f"  {src:<9} {ok}/{tot} kept ({100.0*ok/tot:.1f}%)   "
              f"universal={stats.get(f'{src}:universal',0)} "
              f"impossible={stats.get(f'{src}:impossible',0)} "
              f"error={stats.get(f'{src}:error',0)}")

    print(f"\n{total} instances written to {args.outdir}")


if __name__ == '__main__':
    main()
