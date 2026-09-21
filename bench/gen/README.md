# gen — where the corpora come from

The instances in `../SYNTCOMP` (1610) and `../nexts` (100) are committed, so
nothing here has to be run to reproduce a measurement. It is here so that the
corpora can be *audited* and rebuilt: generation is seeded and deterministic,
and both commands below reproduce the shipped files **byte for byte**.

```sh
# SYNTCOMP: 162 systems, up to 10 effects each = 1610
"$SPOT_ENV_PY" cats_gen.py ../tools/CATS/eval/syntcomp_instances.json -o ../SYNTCOMP

# nexts: 5 sizes x 20 random systems = 100
"$SPOT_ENV_PY" cats_next_gen.py -o ../nexts -n 20
```

`$SPOT_ENV_PY` is the spot environment's interpreter
(`~/micromamba/envs/spot/bin/python3` by default) — these scripts need spot's
Python bindings. `syntcomp_instances.json` is CATS's own file; `../tools/fetch.sh
cats` puts it there.

## SYNTCOMP — CATS's systems, not ours (`cats_gen.py`)

The systems are **not synthesized here**. CATS ships its already-synthesized
SYNTCOMP strategies in `eval/syntcomp_instances.json`, and those are what we
load, so the systems on both sides of the comparison are identical by
construction and the comparison needs no argument.

That was worth doing rather than assuming: on the 77 specifications present in
both benchmark sets, running our own TLSF → `ltlsynt` pipeline gives the *same*
strategy for only 41 (17 language-equal, 24 a refinement of theirs). The other
36 are genuinely different implementations of the same specification — and
different implementations have different causal structure. Comparing runtimes
across those would not have been sound.

```
514   entries in CATS's syntcomp_instances.json
-310   CATS's own filter: |Q| <= 300, 1 <= |IP| <= 4, |OP| >= 1
          (EvalSyntcomp/PrepareSyntcompInstance.fs -- this is the step that
           reproduces the "204 systems" of their paper)
=204   systems
 -35   our own --max-inputs 3 wall (IP=4 is measured at a timeout, not a
          slow run: see ../README.md)
=169   systems in range
  -7   no non-vacuous effect exists for the system (degenerate: nearly every
          effect is universal or impossible on it)
=162   systems, up to 10 non-vacuous effects each  =  1610 instances
          (five systems yield fewer than 10, which is where 1620 - 10 goes)
```

Per system: `converter.parse_hoa` → `hoa_canonicalize` → 10 effects from
`../scripts/effect_gen.py`, one `.txt` each.

**Don't-care resolution** (`hoa_canonicalize.py`) is the step to know about.
`ltlsynt` emits a *strategy*, and a strategy's edge may leave an output
unconstrained; the input-determinism firewall then rejects the system — 93 of
the 169 in range, none of them genuinely input-nondeterministic. Each don't-care
is resolved to a fixed value, which is what any real implementation does and
yields a refinement that still satisfies the specification. **The choice is
arbitrary among winning implementations and it does affect which causes exist**,
so it belongs in any write-up. The order is fixed (edges as listed, outputs
false-before-true), which is why regeneration is reproducible.

**Effects are drawn over the outputs only** (as in CATS, where causes range over
inputs and effects over outputs), interleaved from two sources: templates —
`F`/`G`/`GF`/`FG` over one output literal, the four shapes of the effect-class
taxonomy — and spot's `randltl`, the unbiased one. Every candidate passes a
**non-vacuity filter** before it is kept:

```
system ^  effect == empty  ->  no trace shows the effect: the question is empty
system ^ !effect == empty  ->  every trace shows it: the cause is empty and the
                               MTSR collapses to `States: 1 / AP: 0 / [t] 0`
```

Only effects that are both possible *and* avoidable pose a causal question.
Both are `spot.product(...).is_empty()` checks, and both are applied to every
candidate before selection rather than to the ones that happen to be picked, so
the yield is unbiased. `cats_gen.py` prints it per source at the end of a run.

A `[lasso]` is emitted for each instance even though analyses 2 and 4 ignore it,
so that analysis 1 can run on the same files. This is why the multiplier here is
**effects, not traces**: CATS pairs ten lasso traces per system because it
*checks* a cause on a trace; we synthesize from the system.

## nexts — the sketch benchmark (`cats_next_gen.py`)

100 random systems built to the rules of Table 2 of the CATS paper: |Q| ∈
{10,20,30,40,50} (20 each), one input, effect `X^k "error"`, and a sketch hole
at every step before the effect. This is the corpus where CATS is a
*synthesizer* (by enumeration), so the answers — not just the runtimes — are
comparable. `index.csv` records each instance's size, `k` and candidate count.

## Also here, and not used for the shipped corpora

`tlsf2ltl.py`, `syntcomp_scan.py`, `syntcomp_probe.py` and `syntcomp_gen.py` are
the **other** route: SYNTCOMP's raw `tlsf/` specs → LTL → `ltlsynt` → instances
(959 specs → 125 usable systems → 978 instances). It is a working pipeline and
it is what showed that our synthesis and CATS's disagree on 36 of 77 shared
specs — the finding that made `cats_gen.py` the right generator. **No number in
the paper comes from it.** Keep that straight when reading the two funnels: the
125 there and the 162 here count different things.

```sh
git clone --depth 1 --filter=blob:none --sparse https://github.com/SYNTCOMP/benchmarks.git sc
git -C sc sparse-checkout set tlsf
"$SPOT_ENV_PY" syntcomp_gen.py sc/tlsf -o /tmp/tlsf_instances --effects 10 --max-inputs 3 --max-aps 12
```

`tlsf2ltl.py --validate` re-synthesizes every spec SYNTCOMP annotates with a
`//STATUS` trailer and compares the verdict against it: 132/138 agree, and each
of the 6 disagreements is a degenerate spec (zero outputs), a Moore/Mealy
artefact in the `lily` family, or out of scope on size — none is a conversion
bug.
