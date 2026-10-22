#!/usr/bin/env python3
r"""
ip4_gen -- the |IP| = 4 systems of CATS's benchmark, as TraceMSR instances.

    ~/micromamba/envs/spot/bin/python3 claude_scripts/ip4_gen.py -o <outdir>

Hadar (2026-10-05): a separate table for the systems with more than three
inputs, with a longer timeout.  CATS's own filter (|Q| <= 300, 1 <= |IP| <= 4,
|OP| >= 1) keeps 204 SYNTCOMP systems; the paper uses the 169 with |IP| <= 3, so
the 35 with |IP| = 4 are the ones left out.  This generates their instances
with the SAME code and seed as the paper's benchmark (bench/gen/cats_gen.py,
seed 0, 10 effects per system), so an |IP| = 4 instance here is exactly the one
`cats_gen.py --max-inputs 4` would have produced.  Only the |IP| = 4 systems
are generated; the 169 others are already in bench/SYNTCOMP.
"""
import argparse
import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import cats_gen        # noqa: E402

#: CATS's eval/syntcomp_instances.json (bench/tools/fetch.sh puts it into
#: MSR_Finder/bench/tools/CATS/eval/; this checkout has the artifact copy).
JSON_CANDIDATES = [
    os.path.join(_HERE, "..", "tools", "CATS", "eval", "syntcomp_instances.json"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--json", default=next((p for p in JSON_CANDIDATES
                                            if os.path.exists(p)), None))
    ap.add_argument("--effects", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    if not a.json:
        sys.exit("syntcomp_instances.json not found; pass --json")
    os.makedirs(a.outdir, exist_ok=True)
    entries = json.load(open(a.json))
    kept, _why = cats_gen.cats_filter(entries, 4)
    ip4 = [e for e in kept if len(e["inputs"]) == 4]
    print(f"{len(entries)} CATS entries -> {len(kept)} pass CATS's filter -> "
          f"{len(ip4)} with |IP| = 4")
    per, total = Counter(), 0
    tmp = os.environ.get("TMPDIR", "/tmp")
    for i, e in enumerate(ip4, 1):
        name, made, status, _st = cats_gen.gen_one(e, a.outdir, a.effects, a.seed, tmp)
        per[status] += 1
        total += len(made)
        print(f"  [{i}/{len(ip4)}] {status:<22} {len(made):>2} files  {name}", flush=True)
    print(dict(per))
    print(f"{total} instances written to {a.outdir}")


if __name__ == "__main__":
    main()
