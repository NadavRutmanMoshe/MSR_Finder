#!/usr/bin/env python3
"""
Generate the CATS "next-only sketch" benchmark, in both tools' input formats.

MUST run under the spot environment python:
    ~/micromamba/envs/spot/bin/python3 claude_scripts/cats_next_gen.py \
        -o ~/tracemsr_bench/next_bench --sizes 10,20,30 -n 5

What this reproduces
--------------------
CATS's Table 2 experiment (`eval/EvalSketchRandom/GenerateSketchInstances.fs`).
Their generator is not shipped as data -- it builds the instances in memory and
runs them -- so the only way to put both tools on *identical* instances is to
re-implement it and emit files.  Every step below mirrors that F# source:

  1. `randaut -D -Q<size> -e<density> -A0 -S -n<k> i0`
     Deterministic, complete, `Acceptance: 0 t`, inputs only.
  2. Pick a random NON-INITIAL state as the error state.
  3. Add an output AP `error` and a fresh sink state with a `[t]` self loop.
     Conjoin `!error` onto every pre-existing edge, then REPLACE the error
     state's outgoing edges with the single edge `[error] -> sink`.
  4. BFS the shortest path init -> errorState; resolve each guard to a concrete
     valuation (unconstrained APs flipped at random).
  5. lasso = Prefix: <those valuations> {error} / Loop: {}
  6. effect = X^k "error"   where k = len(Prefix) - 1
     sketch = ?{"i0"} & X ?{"i0"} & ... & X^(k-1) ?{"i0"}

So the effect is a pure chain of nexts, and the cause sketch offers a hole at
every time step strictly before the effect.  CATS fills each hole with a
conjunction of positive/negative literals (`SketchMode.PositiveAndNegativeConjunction`,
3^|IP| fillings per hole) and returns the first candidate that is an actual
cause; we synthesize the minimal sufficient reason over the same time window.
Both answers are therefore sets of (input, time) pairs on the same lasso --
which is why this is the one experiment where the *results* are comparable, not
just the runtimes.

Why the emitted system is canonicalized
---------------------------------------
TraceMSR's determinism firewall (`converter.check_determinism`) needs every
input valuation to have exactly one outgoing edge with a fixed output
valuation.  `randaut` emits `[t]` edges and the sink's self loop leaves `error`
unconstrained, so `hoa_canonicalize` resolves those don't-cares.  The SAME
canonicalized HOA is written into the CATS instance, so both tools see the same
object.  The resolution is a refinement: on the sink the only don't-care is
`error`, resolved to false, which is exactly what the lasso's `Loop: {}` says.

Outputs (per instance <name>):
    <outdir>/tracemsr/<name>.txt    TraceMSR instance ([inputs]/[effect]/[lasso]/[system])
    <outdir>/cats/<name>.txt        CATS --sketch instance ([cause] holds the sketch)
    <outdir>/index.csv              name,size,inputs,k,states
"""
import argparse
import csv
import itertools
import os
import random
import re
import subprocess
import sys
from collections import deque

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH_SCRIPTS = os.path.abspath(os.path.join(_HERE, '..', 'scripts'))
_TOOL_SCRIPTS = os.path.abspath(os.path.join(_HERE, '..', '..', 'scripts'))
for p in (_HERE, _BENCH_SCRIPTS, _TOOL_SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import converter            # noqa: E402
import hoa_canonicalize     # noqa: E402

RANDAUT = os.path.expanduser("~/micromamba/envs/spot/bin/randaut")


# --------------------------------------------------------------------------- #
# step 1 -- random systems
# --------------------------------------------------------------------------- #
def gen_random_systems(size, density, n, seed, inputs):
    """CATS's `generateRandomSystems`, verbatim flags."""
    cmd = [RANDAUT, "-D", f"-Q{size}", f"-e{density}", "-A0", "-S",
           f"-n{n}", f"--seed={seed}"] + list(inputs)
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    blocks = [b.strip() for b in out.split("--END--") if b.strip()]
    return [b + "\n--END--" for b in blocks]


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #
_LIT = re.compile(r'(!?)\s*(\d+)')


def _lits(cond):
    """`!0&1` -> {0: False, 1: True}. `t` -> {}."""
    c = cond.strip()
    if c in ('t', '1', ''):
        return {}
    return {int(i): (neg == '') for neg, i in _LIT.findall(c)}


def _cond_str(assign, n_aps):
    return "&".join(("" if assign[i] else "!") + str(i) for i in range(n_aps))


def _and_not(cond, idx):
    """Conjoin `!idx` onto a guard string."""
    c = cond.strip()
    return f"!{idx}" if c in ('t', '1', '') else f"({c})&!{idx}"


# --------------------------------------------------------------------------- #
# step 2-4 -- inject the error AP, the sink, and read off the lasso
# --------------------------------------------------------------------------- #
class NoErrorPath(Exception):
    pass


def modify_and_lasso(aps, start, states, rng, error_ap="error"):
    """CATS's `modifyAndComputeLasso`.

    Returns (new_aps, new_states, prefix_vals, loop_vals) where the *_vals are
    tuples of booleans over new_aps.
    """
    err_idx = len(aps)
    new_aps = list(aps) + [error_ap]

    candidates = sorted(s for s in states if s != start)
    if not candidates:
        raise NoErrorPath("no non-initial state")
    err_state = candidates[rng.randrange(len(candidates))]
    sink = max(states) + 1

    new_states = {}
    for sid, edges in states.items():
        if sid == err_state:
            # replaced wholesale, exactly as CATS does
            new_states[sid] = [(str(err_idx), sink)]
        else:
            new_states[sid] = [(_and_not(c, err_idx), t) for c, t in edges]
    new_states[sink] = [("t", sink)]

    # shortest path start -> err_state over the modified graph
    prev = {start: None}
    q = deque([start])
    while q:
        s = q.popleft()
        if s == err_state:
            break
        for cond, t in new_states[s]:
            if t not in prev:
                prev[t] = (s, cond)
                q.append(t)
    if err_state not in prev:
        raise NoErrorPath("error state unreachable")

    path = []
    node = err_state
    while prev[node] is not None:
        s, cond = prev[node]
        path.append(cond)
        node = s
    path.reverse()

    def resolve(cond):
        fixed = _lits(cond)
        return tuple(fixed.get(i, rng.random() <= 0.5) for i in range(len(new_aps)))

    prefix = [resolve(c) for c in path]
    prefix.append(tuple(i == err_idx for i in range(len(new_aps))))  # {error}
    loop = [tuple(False for _ in new_aps)]                            # {}
    return new_aps, new_states, prefix, loop


# --------------------------------------------------------------------------- #
# step 6 -- the next-only effect and the next-only sketch
# --------------------------------------------------------------------------- #
def next_effect(k, atom):
    return ("X " * k) + atom


def next_sketch(k, inputs):
    """`?{"i0"} & (X ?{"i0"}) & ... & (X^(k-1) ?{"i0"})`, CATS hole syntax."""
    hole = "?{" + ", ".join(f'"{i}"' for i in inputs) + "}"
    if k <= 0:
        return "1"
    return " & ".join("(" + "X " * i + hole + ")" for i in range(k))


# --------------------------------------------------------------------------- #
# emission
# --------------------------------------------------------------------------- #
def _sets(vals):
    return ' '.join('{' + ' '.join(str(i) for i, b in enumerate(v) if b) + '}'
                    for v in vals)


def write_tracemsr(path, name, inputs, aps, effect, prefix, loop, sysblock, k, size):
    with open(path, 'w') as f:
        f.write(f"[info]\nCATS next-only sketch benchmark: {name} "
                f"(random system, |Q|={size}, |IP|={len(inputs)}, k={k})\n\n")
        f.write("[inputs]\n" + " ".join(f'"{i}"' for i in inputs) + "\n\n")
        f.write(f"[effect]\n{effect}\n\n")
        f.write("[lasso]\n")
        f.write("AP: " + " ".join(f'"{a}"' for a in aps) + "\n")
        f.write("Prefix: " + _sets(prefix) + "\n")
        f.write("Loop: " + _sets(loop) + "\n\n")
        f.write("[system]\n" + sysblock + "\n")


def write_cats(path, name, inputs, aps, sketch, effect, prefix, loop, sysblock):
    with open(path, 'w') as f:
        f.write(f"[info]\n{name}\n\n")
        f.write("[inputs]\n" + " ".join(f'"{i}"' for i in inputs) + "\n\n")
        f.write(f"[cause]\n{sketch}\n\n")
        f.write(f"[effect]\n{effect}\n\n")
        f.write("[lasso]\n")
        f.write("AP: " + " ".join(f'"{a}"' for a in aps) + "\n")
        f.write("Prefix: " + _sets(prefix) + "\n")
        f.write("Loop: " + _sets(loop) + "\n\n")
        f.write("[system]\n" + sysblock + "\n")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', '--outdir', required=True)
    ap.add_argument('--sizes', default="10,20,30,40,50",
                    help="comma-separated |Q| values (CATS uses 10..200 step 10)")
    ap.add_argument('-n', '--per-size', type=int, default=5,
                    help="instances per size (CATS uses 50)")
    ap.add_argument('--inputs', type=int, default=1,
                    help="number of input APs (CATS uses 1)")
    ap.add_argument('--density', type=float, default=0.1)
    ap.add_argument('--seed', type=int, default=0)
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(',') if s.strip()]
    inputs = [f"i{i}" for i in range(args.inputs)]

    d_tm = os.path.join(args.outdir, "tracemsr")
    d_ct = os.path.join(args.outdir, "cats")
    os.makedirs(d_tm, exist_ok=True)
    os.makedirs(d_ct, exist_ok=True)

    rng = random.Random(args.seed)
    tmpdir = os.environ.get('TMPDIR', '/tmp')
    rows = []

    for size in sizes:
        raw = gen_random_systems(size, args.density, args.per_size,
                                 args.seed + size, inputs)
        for j, hoa in enumerate(raw):
            name = f"rand_q{size:03d}_{j:02d}"
            tmp = os.path.join(tmpdir, f"catsnext_{os.getpid()}_{name}.txt")
            with open(tmp, 'w') as f:
                f.write("[inputs]\n" + " ".join(f'"{i}"' for i in inputs) + "\n\n")
                f.write("[system]\n" + hoa + "\n")
            try:
                ins, aps, start, states = converter.parse_hoa(tmp)
            finally:
                os.remove(tmp)

            try:
                new_aps, new_states, prefix, loop = modify_and_lasso(aps, start,
                                                                    states, rng)
            except NoErrorPath as e:
                print(f"  skip {name}: {e}")
                continue

            canon = hoa_canonicalize.canonicalize(ins, new_aps, new_states)
            sysblock = hoa_canonicalize.emit_hoa(new_aps, start, canon)

            k = len(prefix) - 1
            effect = next_effect(k, '"error"')
            sketch = next_sketch(k, inputs)

            write_tracemsr(os.path.join(d_tm, name + ".txt"), name, ins, new_aps,
                           effect, prefix, loop, sysblock, k, size)
            write_cats(os.path.join(d_ct, name + ".txt"), name, ins, new_aps,
                       sketch, effect, prefix, loop, sysblock)
            rows.append({"name": name, "size": size, "inputs": len(ins),
                         "k": k, "states": len(canon),
                         "candidates": 3 ** (k * len(ins))})
            print(f"  [{name}] |Q|={len(canon)} k={k} "
                  f"candidates={3 ** (k * len(ins))}")

    with open(os.path.join(args.outdir, "index.csv"), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=["name", "size", "inputs", "k",
                                          "states", "candidates"])
        w.writeheader()
        w.writerows(rows)

    print(f"\n{len(rows)} instances written to {args.outdir}")


if __name__ == '__main__':
    main()
