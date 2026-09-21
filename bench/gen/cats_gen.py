#!/usr/bin/env python3
"""
Build TraceMSR instances directly from CATS's SYNTCOMP systems.

MUST be run under the spot environment python:
    ~/micromamba/envs/spot/bin/python3 claude_scripts/cats_gen.py \
        ~/tracemsr_bench/cats_syntcomp_instances.json -o ~/tracemsr_bench/cats_bench

Why this exists
---------------
`syntcomp_gen.py` runs our own TLSF -> ltlsynt pipeline. Comparing its runtimes
against CATS's published numbers is not sound: on the 77 specifications present
in both benchmark sets, only 41 of the synthesized systems are the same strategy
(17 language-equal, 24 ours-a-refinement-of-theirs); the other 36 are genuinely
different implementations of the same specification, and different
implementations have different causal structure.

CATS ships its already-synthesized systems in `eval/syntcomp_instances.json`
(github.com/cats-tool/cats), so we can skip synthesis entirely and load theirs.
Then the systems are identical by construction and the comparison needs no
argument.

Pipeline per entry
------------------
    JSON {inputs, outputs, strat}
      --(converter.parse_hoa)-->     inputs / aps / start / states
      --(hoa_canonicalize)-->        concrete input-deterministic Mealy machine
      --(effect_gen)-->              N non-vacuous effects
      -->                            one .txt per effect

`ltlsynt` emits a *strategy*: an edge may leave an output unconstrained. Every
such don't-care is resolved to a fixed value (see hoa_canonicalize). This is a
refinement, so the result still satisfies the specification -- but the choice is
arbitrary among winning implementations and does affect which causes exist, so
it must be stated in any write-up. Without it, 93 of the 169 systems in range
are rejected by the input-determinism firewall even though none of them is
genuinely input-nondeterministic.

Filters
-------
CATS's own filter (EvalSyntcomp/PrepareSyntcompInstance.fs) is
    |Q| <= 300  &&  1 <= |IP| <= 4  &&  |OP| >= 1
which selects exactly 204 of the 514 entries, matching their paper. We apply the
same filter and then our own `--max-inputs` wall (default 3), leaving 169.
"""
import argparse
import itertools
import json
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

import converter            # noqa: E402
import hoa_canonicalize     # noqa: E402
import effect_gen as eg     # noqa: E402  (needs spot)
from syntcomp_gen import build_step_table, random_lasso, _sets  # noqa: E402


CATS_MAX_STATES = 300
CATS_MAX_INPUTS = 4


def hoa_states(hoa):
    m = re.search(r'^States:\s*(\d+)', hoa, re.M)
    return int(m.group(1)) if m else None


def cats_filter(entries, max_inputs):
    """CATS's filter, then our |IP| wall. Returns (kept, Counter of rejections)."""
    kept, why = [], Counter()
    for e in entries:
        strat = e.get('strat')
        if not strat:
            why['no-strategy (ltlsynt failed/timed out)'] += 1
            continue
        n = hoa_states(strat)
        if n is None or n > CATS_MAX_STATES:
            why['|Q| > 300'] += 1
            continue
        if not e['inputs'] or len(e['inputs']) > CATS_MAX_INPUTS:
            why['|IP| = 0 or > 4'] += 1
            continue
        if not e['outputs']:
            why['|OP| = 0'] += 1
            continue
        if len(e['inputs']) > max_inputs:
            why[f'|IP| > {max_inputs} (our wall)'] += 1
            continue
        kept.append(e)
    return kept, why


def build_system(entry, tmpdir):
    """JSON entry -> (sysblock, inputs, aps, start, states). Raises on failure."""
    tmp = os.path.join(tmpdir, f"cats_{os.getpid()}_{abs(hash(entry['name']))}.raw")
    with open(tmp, 'w') as f:
        f.write("[inputs]\n" + " ".join(f'"{i}"' for i in entry['inputs']) + "\n\n")
        f.write("[system]\n" + entry['strat'] + "\n")
    try:
        ins, aps, start, states = converter.parse_hoa(tmp)
    finally:
        os.remove(tmp)
    if not aps:
        raise ValueError('parse produced no APs')
    states = hoa_canonicalize.canonicalize(ins, aps, states)
    return hoa_canonicalize.emit_hoa(aps, start, states), ins, aps, start, states


def gen_one(entry, outdir, n_effects, seed, tmpdir):
    name = re.sub(r'[^A-Za-z0-9]+', '_', entry['name']).strip('_')
    try:
        sysblock, ins, aps, start, states = build_system(entry, tmpdir)
    except hoa_canonicalize.IncompleteStrategy:
        return name, [], 'incomplete-strategy', Counter()
    except Exception as e:
        return name, [], f'error: {type(e).__name__}'[:40], Counter()

    outputs = [a for a in aps if a not in ins]
    table = build_step_table(ins, aps, states)
    rng = random.Random(f"{name}:{seed}")

    try:
        aut = eg.load_system(sysblock)
    except Exception:
        return name, [], 'spot-parse-failed', Counter()

    chosen, stats = eg.pick_effects(aut, outputs, n_effects, seed, rng)
    if not chosen:
        return name, [], 'no-nonvacuous-effect', stats

    made = []
    for k, (source, eff) in enumerate(chosen):
        lasso = random_lasso(table, start, len(ins), rng)
        fn = f"{name}__e{k:02d}.txt"
        with open(os.path.join(outdir, fn), 'w') as f:
            f.write(f"[info]\nCATS SYNTCOMP: {entry['name']} "
                    f"(spec={entry.get('path', '?')}, states={len(states)}, "
                    f"inputs={len(ins)}, aps={len(aps)}) "
                    f"effect #{k} source={source}\n\n")
            f.write("[inputs]\n" + " ".join(f'"{i}"' for i in ins) + "\n\n")
            f.write(f"[effect]\n{eff}\n\n")
            if lasso and lasso[1]:
                f.write("[lasso]\n")
                f.write("AP: " + " ".join(f'"{a}"' for a in aps) + "\n")
                f.write("Prefix: " + _sets(lasso[0]) + "\n")
                f.write("Loop: " + _sets(lasso[1]) + "\n\n")
            f.write("[system]\n" + sysblock + "\n")
        made.append(fn)
    return name, made, 'ok', stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('json', help="CATS eval/syntcomp_instances.json")
    ap.add_argument('-o', '--outdir', required=True)
    ap.add_argument('--effects', type=int, default=10)
    ap.add_argument('--max-inputs', type=int, default=3)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    tmpdir = os.environ.get('TMPDIR', '/tmp')

    entries = json.load(open(args.json))
    kept, why = cats_filter(entries, args.max_inputs)

    print(f"CATS entries: {len(entries)}")
    for k, v in why.most_common():
        print(f"   -{v:>4}  {k}")
    print(f"=  {len(kept)} systems to generate from\n")

    if args.limit:
        kept = kept[:args.limit]

    total, stats, per_spec = 0, Counter(), Counter()
    # Sequential: spot objects are not thread-safe.
    for i, e in enumerate(kept, 1):
        name, made, status, st = gen_one(e, args.outdir, args.effects,
                                         args.seed, tmpdir)
        per_spec[status] += 1
        stats += st
        total += len(made)
        print(f"  [{i}/{len(kept)}] {status:<24} {len(made):>2} files  {name}",
              flush=True)

    print("\n=== per-system status ===")
    for k, v in per_spec.most_common():
        print(f"  {k:<26} {v}")

    print("\n=== effect filter yield ===")
    for src in ('template', 'randltl'):
        tot = sum(v for k, v in stats.items() if k.startswith(src + ':'))
        if not tot:
            continue
        ok = stats.get(f'{src}:ok', 0)
        print(f"  {src:<9} {ok}/{tot} kept ({100.0*ok/tot:.1f}%)   "
              f"universal={stats.get(f'{src}:universal', 0)} "
              f"impossible={stats.get(f'{src}:impossible', 0)} "
              f"error={stats.get(f'{src}:error', 0)}")

    print(f"\n{total} instances written to {args.outdir}")


if __name__ == '__main__':
    main()
