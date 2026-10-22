#!/usr/bin/env python3
r"""
cats_randltl_gen -- the benchmark with CATS's effects instead of ours.

    ~/micromamba/envs/spot/bin/python3 gen/cats_randltl_gen.py -o CATS_RL   (from bench/)

Hadar (2026-10-08, via Nadav): the benchmark should be CATS's.  The paper's 818
use CATS's systems but OUR effects (F/G/GF/FG templates + randltl with
tree-size 8).  CATS (eval/EvalSyntcomp/PrepareSyntcompInstance.fs,
getRandomFormula / genInstances) draws its effects as

    randltl --seed <rnd> --tree-size=22..30 --ltl-priorities "xor=0, M=0" <outputs>

and nothing else.  This generator is bench/gen/cats_gen.py (same systems, same
canonicalization, same random lasso, 10 effects per system) with exactly two
changes:
  * no templates -- only randltl, tree-size 22..30, CATS's priorities;
  * randltl's seed is drawn per system (CATS: rnd.Next() per system; cats_gen
    passed the same --seed to every system, so systems with the same number of
    outputs got the same formulas up to renaming).
The non-vacuity filter (effect neither impossible nor universal in the system)
is kept, and the PI_VAL gate is applied afterwards by bench/scripts/pi_gate.py,
as for the 818.

All 204 systems of CATS's filter are generated (|IP| <= 4); the run script
splits |IP| <= 3 (120 s, the main table) from |IP| = 4 (300 s).
"""
import argparse
import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import cats_gen                  # noqa: E402
import effect_gen as eg          # noqa: E402  (the module cats_gen uses)
from ip4_gen import JSON_CANDIDATES  # noqa: E402

TREE_SIZE = "22..30"
#: candidates drawn per system before the non-vacuity filter (cats_gen: 40)
BUDGET = 60

_orig_randltl = eg.randltl_effects

#: seconds per non-vacuity check.  At tree-size 22..30 spot's LTL translation
#: can blow up (test2_f2774e0b, 16 outputs, ran >10 min on one formula), and a
#: spot call cannot be interrupted in-process, so the check runs in a fork.
CLASSIFY_TIMEOUT = 30


def _classify(aut, eff):
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    rd, wr = ctx.Pipe(duplex=False)

    def child():
        wr.send(eg.classify(aut, eff))

    p = ctx.Process(target=child)
    p.start()
    wr.close()
    verdict = rd.recv() if rd.poll(CLASSIFY_TIMEOUT) else "timeout"
    if p.is_alive():
        p.kill()
    p.join()
    return verdict


def _pick_effects(aut, outputs, n_wanted, seed, rng, randltl_budget=BUDGET):
    """effect_gen.pick_effects, randltl only, CATS's tree size, per-system seed."""
    stats = Counter()
    sys_seed = rng.randrange(2 ** 31)       # rng is seeded by "<system>:<seed>"
    pool, seen = [], set()
    for source, eff in _orig_randltl(outputs, BUDGET, sys_seed, tree_size=TREE_SIZE):
        if eff in seen:
            continue
        seen.add(eff)
        verdict = _classify(aut, eff)
        stats[f"{source}:{verdict}"] += 1
        if verdict == "ok":
            pool.append((source, eff))
    rng.shuffle(pool)
    return pool[:n_wanted], stats


eg.pick_effects = _pick_effects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--json", default=next((p for p in JSON_CANDIDATES
                                            if os.path.exists(p)), None))
    ap.add_argument("--effects", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if not a.json:
        sys.exit("syntcomp_instances.json not found; pass --json")
    os.makedirs(a.outdir, exist_ok=True)
    entries = json.load(open(a.json))
    kept, _why = cats_gen.cats_filter(entries, 4)
    print(f"{len(entries)} CATS entries -> {len(kept)} pass CATS's filter "
          f"(|IP|<=3: {sum(len(e['inputs']) <= 3 for e in kept)}, "
          f"|IP|=4: {sum(len(e['inputs']) == 4 for e in kept)})")
    if a.limit:
        kept = kept[:a.limit]
    per, stats, total = Counter(), Counter(), 0
    tmp = os.environ.get("TMPDIR", "/tmp")
    for i, e in enumerate(kept, 1):
        name, made, status, st = cats_gen.gen_one(e, a.outdir, a.effects, a.seed, tmp)
        per[status] += 1
        stats += st
        total += len(made)
        print(f"  [{i}/{len(kept)}] {status:<22} {len(made):>2} files  {name}", flush=True)
    print("per system:", dict(per))
    tot = sum(stats.values())
    print(f"randltl candidates: {tot}, kept {stats.get('randltl:ok', 0)}, "
          f"universal {stats.get('randltl:universal', 0)}, "
          f"impossible {stats.get('randltl:impossible', 0)}, "
          f"error {stats.get('randltl:error', 0)}, "
          f"check timed out ({CLASSIFY_TIMEOUT} s) {stats.get('randltl:timeout', 0)}")
    print(f"{total} instances written to {a.outdir}")


if __name__ == "__main__":
    main()
