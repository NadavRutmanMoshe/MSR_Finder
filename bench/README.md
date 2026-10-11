# bench — the benchmark suite

One command, stoppable and resumable:

```sh
cd bench
tools/fetch.sh              # once: the three baselines, at pinned commits
./run.sh                    # everything
./run.sh --list             # the plan, and how many rows are already on disk
./run.sh --sample 50        # a 50-instance subset (the SAME 50 for every tool)
./run.sh --only MSR         # one group
```

**Ctrl-C is safe at any point.** The stage in flight loses at most the instance
it was on; everything already measured is on disk. Re-running the same line
continues from there — every driver reads its own CSV back and skips what it
already has. That is why nothing here ever rewrites a row: if a measurement
changes, delete the CSV rather than mixing two constructions in one file.

The default timeout is **120 s per solver call**, every stage
(`--timeout`, with `--msr-timeout` / `--mtsr-timeout` to override one group).
`--sample N` selects N instances *after* the gate, so N is the number that
actually runs — and the selection is deterministic, so every tool measures the
same N and the CSVs still join.

## Heat, and what a timed-out tool leaves behind

Two things a long sweep on a laptop gets wrong if nobody looks:

**Cooling.** The suite pauses **20 s after every 5 minutes of work**
(`--cooldown` / `--cooldown-every`, `0` disables) and **60 s between stages**
(`--stage-pause`). The instance-level pause is time-based, not
every-N-instances: instances here range from 0.2 s to the full timeout, so a
count-based rule would mean anything between seconds and an hour of solid load.
The between-stage pause matters for the numbers, not just the hardware — the
next tool's first instances would otherwise be timed on the thermal state the
last one left, and a throttled CPU makes a tool look slow for reasons that have
nothing to do with it.

**Leaked solvers.** Killing a tool on timeout can orphan its solver at 100% CPU,
which then taxes every measurement taken beside it. This was measured, not
assumed: each tool was given a 3 s timeout on an instance it cannot finish, and
`ps` was checked afterwards.

| forced to time out | left running |
|---|---|
| AutoHyper (MSR) | none |
| HyperQB (MSR) | none |
| `mtsr_min` | **`autfilt --sat-minimize -B`** ← a real leak |
| CATS `--check` / `--sketch` | none |
| CORP | none |
| ExplainHyper | none |

The cause is worth knowing, because it is not obvious. `mtsr_min` is itself a
solver *driver*: it starts `autfilt` through `proc_guard`, which puts it in a
**new process group**. Killing our child's group therefore never reaches the
grandchild. And SIGTERM does not help either — Python's default SIGTERM
disposition ends the interpreter *without unwinding*, so the `finally` that
would have killed `autfilt` never runs.

Fixed in two places: `bench_lib.run` now escalates **SIGINT → SIGTERM → kill the
group** (SIGINT raises `KeyboardInterrupt`, which *does* unwind), and
`mtsr_min` installs a SIGTERM handler that raises, so it cleans up whichever
signal a caller sends. Re-measured after the fix: **nothing left running for any
of the seven paths**.

### The reaper, for anything that still slips through

A fix that is right today is not a guarantee over a two-day unattended run, so
the suite also sweeps: **between every instance and after every stage**, and
every orphan it finds is appended to `results/leaks.log` with its pid, age,
command line and the stage that leaked it. **A line in that file is a bug** —
the suite should not produce one — so it is worth reporting rather than
ignoring.

Killing is safe here because ownership is tested, not assumed. A process is
reaped only if **both**:

1. it **started after this run began** — so a solver you already had running,
   in this terminal or any other, is never a candidate; and
2. it is **outside the suite's process tree** — so anything a stage is
   legitimately using right now is never a candidate either. A live solver is a
   descendant of `run_all`; it becomes an orphan only once its driver dies,
   which is exactly the case we want.

Anything matching (1)'s opposite is logged once as `PRE-EXISTING (untouched)`
and left alone. Both branches are tested: a genuine double-forked orphan is
killed and logged; a reparented solver started before the run survives and is
logged as untouched. `--no-reap` logs without killing.

```
results/leaks.log
2026-09-02 21:21:20  ORPHAN KILLED  ltl2tgba(179903)  age=4s  stage=MTSR vs CORP  ltl2tgba -D G F ...
2026-09-02 21:21:55  PRE-EXISTING (untouched)  ltl2tgba(180027)  age=16s  ltl2tgba -D G F ...
```

## Layout

```
bench/
  run.sh              the one line
  scripts/            the drivers (below)
  tools/              ExplainHyper, CATS, CORP -- fetched, not vendored:
    fetch.sh            pinned commits + the ExplainHyper patches (tools/README.md)
  gen/                how the two corpora were built, and rebuilding them (gen/README.md)
  SYNTCOMP/           1610 instances, built from CATS's own SYNTCOMP systems
  nexts/              100 instances, CATS Table-2 rules (cats/ + tracemsr/)
  results/
    gate.csv          the PI_VAL verdict for every SYNTCOMP instance
    MSR/              msr_autohyper.csv  msr_hyperqb.csv  explainhyper.csv
                      verify_hyperqb.csv        (untimed, see below)
    MTSR_CORP/        mtsr.csv  corp.csv
    MTSR_CATS_check/  mtsr.csv  cats_check.csv
    MTSR_CATS_sketch/ mtsr.csv  cats_sketch.csv
```

Each comparison writes **two** CSVs from **one** pass — ours and theirs —
joined on `instance` (`name` for the sketch benchmark). They are written
together rather than split afterwards, so an interrupted or resumed run cannot
leave them misaligned: an instance counts as done only once both files have it.

## The gate

`results/gate.csv` is stage 0 and every later stage filters on it. A causal
question is only well posed when the effect actually **occurred** on the trace:
CORP answers "No cause exists" and CATS's check fails at PC1 whenever it did
not, so an ungated benchmark measures the tools' rejection paths rather than
their work.

On this corpus that matters a lot: **818 of 1610 pass (50.8%)**, the rest are
`EFFECT_FALSE`. Nothing is deleted — the verdict is recorded for all 1610 and
`--no-gate` runs everything.

## MSR

Compared against **ExplainHyper** only. Both sides answer a question about the
same triple ⟨S, π, eff⟩, but not the same question:

| | answer |
|---|---|
| MSR | one **minimal sufficient reason** R: a minimal set of (input, time) pairs of π such that *every* system trace agreeing with π on R satisfies eff |
| ExplainHyper | the **actual causes** C: minimal sets of input events whose flip, under some contingency, makes π stop satisfying eff — a statement about that one trace, and there can be many |

So the comparable columns are `size` against `cause_min`/`cause_max` (subsets of
the same ℕ × IP on the same trace) and the wall clocks — not a single "who
wins".

**Both models are run and both are kept.** `msr_autohyper.csv` and
`msr_hyperqb.csv` have identical columns including `model`, so they concatenate
into one table and "the best time, and which model got it" is a group-by
afterwards. Neither run has to be repeated to build that column, which is the
whole point: these runs are long.

Every row carries the reason as **data**, not prose: `reason` is
`<input>@<t>` items, a `*` suffix marking a pin inside the lasso's loop (it
recurs at t, t+L, t+2L, … and is not one position). `size` counts the finite
pins, `loop_size` the per-loop ones, and they are never added together.
`reason_complete` is False when a bounded HyperQB run could only report a
*prefix* of R — such a `size` is a lower bound and must not be compared with an
exact one. It is now rare: a flat witness is read as a *finite* R and certified
like any other, so it is either proved (and marked complete, on an unrolled π)
or rejected.

`certify_s` is the HyperQB rows' share of `wall_s` spent checking the witness
rather than finding it — see the next section. It is inside `wall_s` because the
check is part of the answer, and broken out so a solver-only comparison is still
available.

### HyperQB's `sat` is not a proof, and the pipeline no longer treats it as one

Both the SR and the MSR query carry a **universal** trace quantifier, and
HyperQB unrolls `G` and `F` with no semantics-dependent tail at all
(`hltlunroller/src/lib.rs`: `Globally` is a plain conjunction over `k..=bound`,
`Eventually` a plain disjunction; only `X`, `U` and `R` read the
`pes`/`opt`/`hpes`/`hopt` flag). So `G ψ` is **true** whenever `ψ` survives to
the bound, and a universally quantified sufficiency claim is accepted on the
strength of a `k`-step prefix:

```
Forall pi . (G (~ acc[pi]))   on ltl2dba_alpha7__e06's REAL module (the only
                              `acc` state is 8 steps in)
    k=6 -> sat    (WRONG)     identical under -s pes, opt, hpes and hopt
    k=8 -> unsat  (right)
```

The first run of this benchmark recorded the consequence: 37 of 818 rows whose
R is not sufficient or not minimal, plus 182 more whose truncated R was not
sufficient either (stage 6 had skipped those). The pipeline now certifies every
`sat` witness exactly before reporting it (`../scripts/msr_certify.py`),
so a rerun records only checked answers, and the rejected witnesses fall
through to the next query instead of becoming results.

### What we hand ExplainHyper

Φ = `Exists (Exists (eff_0))`, and π as the counterexample.

Their front end does not read the quantifier prefix: `mchyper_to_spot`
(translator.py:124) deletes every `Forall`/`Exists` and keeps one bit — "did it
start with `Forall`" — and if it did, `explain_hyper.py:233` negates the body
before explaining it. So:

```
Forall (Forall (Neg eff))  ->  body !eff, negated  ->  explains  eff
Exists (Exists (eff))      ->  body  eff, as-is    ->  explains  eff
Forall (Forall (eff))      ->  body  eff, negated  ->  explains !eff   WRONG
```

The first two are the same question and return bit-identical causes (measured
on TorcsGearing e00/e07). We write the second, so the formula handed to their
tool literally *is* the effect. The third is the naive reading of "give it Φ"
and is a different question — on those two instances it lost the real cause on
one and invented five spurious ones on the other.

`expressible`/`skip_reason` record their front end's two limits (`cube_ok`,
`moore_ok`), which are theirs and not the theory's, so the coverage number is in
the data rather than in a dropped row.

## MTSR

Three comparisons, all synthesis-side:

| results dir | baseline | corpus | what is comparable |
|---|---|---|---|
| `MTSR_CORP` | CORP (Synthesis of Temporal Causality, CAV'24) | SYNTCOMP | both emit an NBA over the inputs, so `states` compares directly — read it alongside `det`, since a nondeterministic 3-state witness is not the same object as a deterministic one. `equiv` decides language equality |
| `MTSR_CATS_check` | CATS `--check` | SYNTCOMP | CATS *verifies* a candidate; we *synthesize* one. No shared answer, so this compares **runtime**: what synthesis costs against what checking one candidate costs |
| `MTSR_CATS_sketch` | CATS `--sketch` | nexts (100) | here CATS becomes a synthesizer by enumeration, so the **answers** are comparable: both denote an ω-language over the inputs and `agree` is `spot.are_equivalent` |

MSR appears in none of them on purpose. MSR is a *non-temporal* notion — a
finite set of (input, position) pairs on one trace — and scoring it against a
temporal cause compares two different kinds of object.

The nexts corpus is 100 random systems built to the CATS paper's Table 2 rules
(|Q| 10–50, one input, effect `X^k "error"`, a sketch hole at every step before
the effect). `cats_sketch.csv` also times CATS **verifying the cause it just
searched for** — the one column anywhere that times a full *positive* check,
since everywhere else CATS's check is a rejection and short-circuits at the
first failing obligation.

## Verification — never counted as time

```sh
~/micromamba/envs/spot/bin/python3 scripts/verify_msr.py
~/micromamba/envs/spot/bin/python3 scripts/verify_msr.py --self-test
```

Stage 6 re-checks the reasons the benchmark recorded, reading them back out of
`msr_hyperqb.csv` rather than re-running anything. It writes its own CSV and its
wall clock is never merged into a tool's row.

It stays worth running even though the pipeline now checks itself: this is an
**independent** check — a different engine (AutoHyper, on explicit LTSs)
answering a differently-written query. A shared bug would have to live in the
instance parser they both use, and nowhere else. Cross-checked on the first
run's 404 decidable rows: same verdict on every one.

* **Sufficiency**, with spot: `L(system) ∩ L(pins(R,π)) ∩ L(¬Φ) = ∅`.
  `pins(R,π)` is an *automaton*, not a chain of `X`s, so a pin inside the loop —
  an infinite modulo-periodic set of positions — is expressed exactly.
* **Minimality**, with AutoHyper: the 4-quantifier query with π and τ *pinned to
  the row under test*,
  `∃π ∃τ ∀τ2 ∃π2. fix(π) ∧ fix(τ) ∧ (τ2 not sufficient | τ2 = τ | τ2 ⊄ τ)`.
  SAT means no proper subset of R is sufficient. Its formula is written in
  `verify_msr.py`, not imported from `msr_formulas` — a query may not certify
  itself.

HyperQB rows only: HyperQB's π *is* the file's lasso, so the recorded positions
reconstruct exactly. AutoHyper's witness is an unrolling the solver chose and
the CSV does not carry it, so that would silently misalign; it is refused rather
than approximated. `--self-test` mutates a verified row (drop a pin, add a pin)
and requires both halves to reject the mutant.

## The baselines, and the container

`tools/` is empty in a fresh clone. `tools/fetch.sh` fills it, pinning each tool
to the commit its numbers in `results/` were measured with (CATS is built from
source, ~30 s; CORP and ExplainHyper are Python). ExplainHyper is patched, with
the three diffs and the reason for each in `tools/README.md`; CATS and CORP are
used exactly as published.

Everything — both corpora, the drivers, the recorded CSVs, the baselines and the
environments they need — is in the benchmark image:

```sh
docker build -t msr-finder .                             # from the repo root
docker build -t msr-finder-bench -f Dockerfile.bench .

docker run --rm -it msr-finder-bench ./run.sh --list
docker run --rm -it -v "$PWD/bench/results:/opt/msr_finder/bench/results" \
    msr-finder-bench ./run.sh --sample 20
```

Mount `bench/results` for anything you want to keep: the drivers are resumable
and read that directory back, so a mount is also what makes Ctrl-C and re-run
continue rather than start over. Without one, the shipped CSVs are used inside
the container and the new rows go away with it.

### One instance per tool, in about a minute

The full sweep is days of solver time. To see that every tool works, run each
driver on a single instance and write the rows somewhere disposable:

```sh
docker run --rm -it msr-finder-bench bash -c '
  "$SPOT_ENV_PY"       scripts/mtsr_corp_bench.py        --sample 1 -o /tmp/o/corp   --timeout 60
  "$SPOT_ENV_PY"       scripts/mtsr_cats_check_bench.py  --sample 1 -o /tmp/o/cats   --timeout 60
  "$SPOT_ENV_PY"       scripts/mtsr_cats_sketch_bench.py --limit  1 -o /tmp/o/sketch --timeout 60
  python3              scripts/msr_bench.py --model autohyper --sample 1 --out /tmp/o/msr.csv --timeout 60
  "$EXPLAINHYPER_ENV_PY" scripts/explainhyper_bench.py   --sample 1 --out /tmp/o/eh.csv  --timeout 60'
```

`--sample`/`--limit` select deterministically, so all five land on the same
instance (`lilydemo23__e01`, plus `rand_q010_00` for the sketch corpus) and the
answers can be read straight against `results/`: `corp=OK 6st`, `mtsr=OK 4st`,
CATS `NO_CAUSE (failed PC4)`, `SAT_MSR |R|=2`, ExplainHyper `UNSUPPORTED`
(their front end cannot take a Mealy system), sketch `agree=yes`. Every one of
those matches the recorded row.

## Environments

| tool | interpreter |
|---|---|
| MSR (AutoHyper, HyperQB) | system `python3` |
| MTSR, CORP, CATS drivers, gate, verify | `~/micromamba/envs/spot/bin/python3` (`$SPOT_ENV_PY`) |
| ExplainHyper | `~/micromamba/envs/explainhyper/bin/python3` (`$EXPLAINHYPER_ENV_PY`) |

`run.sh` dispatches each stage under the right one. AutoHyper and CATS are .NET
apphosts, so `DOTNET_ROOT` is set automatically when `~/.dotnet` exists.

The tool's own `scripts/` are found relative to this directory (`../scripts`,
and `../../MSR_Finder/scripts` for the layout the measurements were taken in);
`$MSR_FINDER_SCRIPTS` overrides both.

`tools/CATS/app/paths.json` points at `scripts/solver_wrappers/`, not at spot
directly. CATS builds on FsOmegaLib, which launches spot and then calls
`WaitForExit()` with no timeout and no memory cap (`src/FsOmegaLib/Util.fs:62`);
one unguarded `autfilt` child once reached 8.4 GB and drove a 9 GB box to a
load of 42. The wrappers cap the leaf, which is the only thing that can be
capped — the .NET process itself fails at startup under any `ulimit -v`.

## Scripts

```
run_all.py                 the orchestrator: stages, resume, Ctrl-C
bench_lib.py               resumable CSVs, paired CSVs, the gate, timing
pi_gate.py                 stage 0: is the lasso a system trace, and does Phi hold
msr_bench.py               MSR, one model per run
explainhyper_bench.py      ExplainHyper (their env)
explainhyper_export.py     instance -> .aag/.dot/.cex/.f, with the Exists encoding
mtsr_corp_bench.py         MTSR vs CORP
mtsr_cats_check_bench.py   MTSR vs CATS --check
mtsr_cats_sketch_bench.py  MTSR vs CATS --sketch
mtsr_min.py                the MTSR witness (determinize-then-SAT-minimize)
cats_util.py               CATS instance format + invocation
effect_gen.py              random effects / causes, CATS's own flags
verify_msr.py              stage 6, untimed
solver_wrappers/           resource-capped autfilt and ltl2tgba, for CATS
```

## Changes since the recorded runs

`results/` was measured with this suite sitting *beside* the tool
(`demo_tool/bench`) and with the baselines copied into `tools/`. Moving it
inside the tool repository took two changes, both to how the code finds things
rather than to what it measures:

* the tool's `scripts/` directory is now probed for (`../scripts`, then
  `../../MSR_Finder/scripts`, with `$MSR_FINDER_SCRIPTS` overriding), so either
  layout works;
* the four drivers that time a subprocess now use `time.monotonic()` instead of
  `time.time()`. A wall clock can step backwards — under WSL it does, after a
  suspend — and a re-run here produced a **negative** `corp_s`. No recorded row
  is affected (none is negative), but a reviewer re-running could have hit it.

One further fix, which does affect what a re-run produces. The CATS `--check`
driver drew its candidate cause with

```python
random_cause(ins, args.seed + (hash(name) & 0xffff))      # was
random_cause(ins, args.seed + stable_hash(name))          # is
```

and `hash(str)` is salted by `PYTHONHASHSEED`, so it returns a different value
in every interpreter. The candidate therefore changed on **every run**, on any
machine — which is how it was found: the same instance drew one formula on the
host and another in the container. The rows in `results/MTSR_CATS_check/` were
measured with the salted version, so re-running now gives the *same* candidate
every time but **not the one they got**: `cats_cause`, `cats_failed`,
`cats_stages` and `cats_s` will differ per instance. What the comparison
measures — how long CATS takes to decide a random candidate, against how long
synthesis takes — is unchanged, since the candidates come from the same
distribution (their Figure-2 protocol, their generator's flags). The
`cats_cause` column records the formula each recorded row actually used.

`hash()` appears three more times in `gen/`, only ever in a temp *filename*;
that both corpora regenerate byte-for-byte is the check that it does not reach
the content.

The baselines are byte-identical to the copies that were measured: `fetch.sh`
was checked against them after it was written.

Three changes to the tool itself (2026-09-29) are inside what `msr_bench.py`
times, none of them in what it answers on this corpus:

* both MSR backends first check, exactly and in plain Python, that the lasso
  is a word of the system (`scripts/trace_check.py`) — milliseconds, and all
  818 gated instances pass it (it agrees with `pi_gate.py` on all 1610). It
  stops a vacuous "MSR" on a lasso the system cannot produce, which the gate
  already excluded here;
* every solver call runs in a private temporary directory that is also its
  working directory — AutoHyper writes `aut1.hoa`/`autRes.hoa` into its CWD,
  so two concurrent runs could read each other's automata. The drivers here are
  serial, so the recorded rows could not have been affected;
* SIGTERM/SIGHUP unwind like Ctrl-C, so a stopped run no longer leaves its
  solver running.

The 12 shipped examples give byte-identical output before and after.

Two more (2026-10-11):

* `scripts/mtsr_min.py` is no longer a copy of the MTSR construction: it calls
  the tool's own `MTSR_one_example.synthesize`, so the benchmark measures the
  code `tracemsr.py` runs. On 60 of the 818 gated instances the old copy and the
  tool give the same automata (same state counts and determinism,
  spot-equivalent languages). `--cause` adds the tool's `--check` in the same
  process.
* `--check` answers `MTSR` or `NOT_MTSR` (is the given cause the MTSR); it no
  longer names a direction when they differ.
