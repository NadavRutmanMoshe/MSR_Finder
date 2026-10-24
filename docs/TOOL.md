# TraceMSR

TraceMSR synthesizes sufficient reasons for LTL effects on traces of reactive
systems. Given a reactive system, an LTL formula φ (the effect), and a trace π
of the system that satisfies φ, a sufficient reason explains why π satisfies
φ. TraceMSR produces two kinds of reasons:

- **non-temporal reasons**: sets of events on π (an input and a time step), the
  *minimal sufficient reason* (MSR). TraceMSR computes it by a reduction to
  HyperLTL model checking, with AutoHyper or HyperQB;
- **temporal reasons**: a property of the inputs, given as a Büchi automaton,
  the *minimal temporal sufficient reason* (MTSR). TraceMSR computes it by an
  automata construction in spot.

The two give a trace-local and a system-global explanation of the same effect.
The paper is in [`paper/`](../paper/); this file is the tool's manual.

## What TraceMSR computes

A reactive system has atomic propositions split into **inputs** IP and
**outputs** OP, and is *input deterministic*: every sequence of inputs induces
exactly one trace. Fix a system T, an LTL effect φ, and a trace π of T with
π ⊨ φ.

- A set R of events (pairs of a time step t and an input a) is a **sufficient
  reason** (SR) if every trace of T that agrees with π on every event of R
  satisfies φ. It is a **minimal sufficient reason** (MSR) if no strictly
  smaller set is a sufficient reason. Since T is input deterministic, the set of
  all events is always a sufficient reason; an MSR, however, does not always
  exist.
- A property P of input sequences is a **temporal sufficient reason** (TSR) if π
  satisfies P and every trace of T that satisfies P also satisfies φ. It is the
  **minimal temporal sufficient reason** (MTSR) if no strictly larger property
  is a TSR, i.e. it is the weakest constraint on the inputs that still
  guarantees φ. The MTSR always exists, is unique, and is ω-regular: it is the
  product of T with an automaton for φ, projected onto the inputs.

## Quick start

From the artifact folder (with the environment of `install.sh` loaded), run
TraceMSR on the running example of the paper, the biased arbiter:

```
python3 scripts/tracemsr.py examples/biased_arbiter.txt
```

The system is biased towards `r1`: if `r1` and `r2` are requested at the same
time, `g1` is granted and not `g2`, and once `r1` is requested, `g2` is never
granted again. The effect is `F g2` on the trace `{r2,g2} ({})^ω`.

TraceMSR opens a menu of its four operations:

```
Select operations for biased_arbiter.txt
 > [x] 1. MSR with the given PI
   [ ] 2. MSR for Phi — find a PI automatically
   [ ] 3. Model checking of Phi (counterexample + why)
   [ ] 4. MTSR — Minimal Temporal Sufficient Reason
       ▶ Start
ENTER/SPACE select · ↑/↓ move · a all · ENTER on Start runs · q quit
```

Operation 1 is already selected (`[x]`). Move down to operation 4 with the
arrow keys and press ENTER or SPACE to select it, then move to **▶ Start** and
press ENTER. Operation 1 returns the MSR
R = {(r1,0), (r2,0)}:

```
Reason R:
    { (r1,0), (r2,0) }

=============================================
TIME  | ACTUAL TRACE (Pi)   | REASON (R)
=============================================
0     | r1=F, r2=T          | <-- R pins r1, r2
----- + ------------------- + ---------------
CYCLE | (Repeating Pattern) |
----- + ------------------- + ---------------
1     | r1=F, r2=F          |
=============================================
```

Every trace in which `r1` is false and `r2` is true at time 0 satisfies the
effect, and removing either event makes the reason not sufficient: if `r1`
were not forced to be false at time 0, it could be requested then, and `g2`
would never be granted. Operation 4 returns the MTSR, an automaton over the
inputs for `!r1 U (r2 & !r1)`: `r1` cannot be requested until `r2` is requested
without `r1`. It is printed in the HOA format (accepting states in green) and
saved as a picture next to the input, `examples/biased_arbiter_mtsr.png`.

To skip the menu, give the operations on the command line with `--run`. Each
of the following is a separate example.

The paper's session, operations 1 and 4:

```
python3 scripts/tracemsr.py examples/biased_arbiter.txt --run 1,4
```

All four operations:

```
python3 scripts/tracemsr.py examples/example_1.txt --run all
```

The MSR (operation 1) with HyperQB instead of AutoHyper:

```
python3 scripts/tracemsr.py examples/example_1.txt --run 1 --hyperQB
```

Checking the candidate cause in the file's `[cause]` block against the MTSR
(operation 4):

```
python3 scripts/tracemsr.py examples/example_1_cause.txt --run 4 --check
```

## Input file format

The input is a single plain-text file with five labeled blocks, plus an
optional sixth. For example, `examples/biased_arbiter.txt` (shortened):

```
[info]     Biased arbiter of ATVA'26 (Fig. 1) ...
[inputs]   "r1" "r2"
[effect]   F "g2"
[lasso]    AP: "r1" "r2" "g1" "g2"
           Prefix: {1 3}
           Loop:   {}
[cause]    !"r1" U ("r2" & !"r1")    (optional; only --check reads it)
[system]   HOA: v1
           Start: 0
           AP: 4 "r1" "r2" "g1" "g2"
           ...
           --END--
```

The blocks are the following:

- `[info]`: a description of the instance; TraceMSR ignores it.
- `[inputs]`: declares the set IP of input propositions.
- `[effect]`: the effect φ, the LTL formula for which we want to find a reason
  (in spot's syntax).
- `[lasso]`: declares the atomic propositions and the trace π, given as a
  prefix and a loop that repeats forever. Each `{…}` lists the indices (into
  the `AP:` line) of the propositions that are true at that step, so `{}` means
  all are false. This block is optional, since three of the four operations do
  not need a trace.
- `[cause]`: optional; a candidate cause, used only with the option `--check`
  to verify it.
- `[system]`: the system, given as an automaton in the HOA format or as a
  labeled transition system.

Each block header must be alone on its line. Our definitions assume that the
system is input deterministic, which guarantees that a sufficient reason always
exists. Therefore, TraceMSR checks that the system is input deterministic, and
rejects it otherwise (`NON_DETERMINISTIC`).

## The four operations

TraceMSR has four main operations. Operations 1–3 synthesize an MSR; if no MSR
exists, TraceMSR reports this and displays a sufficient reason instead.
Operation 4 synthesizes the MTSR, displays it as a Büchi automaton over the
inputs, and outputs an image of it.

1. Synthesizing an MSR for the trace given in `[lasso]`.
2. Finding a trace π that satisfies φ (if one exists), and then synthesizing an
   MSR for this trace.
3. Model checking φ: finding a trace that does not satisfy φ (if one exists),
   and then synthesizing an MSR for this trace and the effect ¬φ. If there is
   none, every trace satisfies φ and the check passes.
4. Synthesizing the MTSR using spot.
   - (4a) With the flag `--check`: checking whether the candidate cause in
     `[cause]` is equal to the MTSR (see [`--check`](#--check--mtsr-as-a-cause-checker)).

Operations 2–4 do not require a trace. In operations 1–3, the default model
checker is AutoHyper; `--hyperQB` uses HyperQB instead (see
[`--hyperQB`](#--hyperqb--the-bounded-solver)), and `--with-tau` also shows
the trace representation τ of the reason. Every operation reports its running
time when it finishes; `--timeout N` limits each solver call to N seconds.

## Reading the output

Operations 1–3 print the reason R as a set of (input, time step) pairs, and
the trace π with the events of R marked on it (`<-- R pins …`, in green). The
trace is a lasso: the steps after `CYCLE` repeat forever, and an event in the
loop is pinned at every repetition of that step. For example:

```
Reason R:
    { (i1,0), (i2,2), (i2,3), (i1,4), (i2,4) }

=============================================
TIME  | ACTUAL TRACE (Pi)   | REASON (R)
=============================================
0     | i1=T, i2=T          | <-- R pins i1
1     | i1=T, i2=T          |
2     | i1=T, i2=F          | <-- R pins i2
3     | i1=T, i2=F          | <-- R pins i2
4     | i1=T, i2=F          | <-- R pins i1, i2
----- + ------------------- + ---------------
CYCLE | (Repeating Pattern) |
----- + ------------------- + ---------------
5     | i1=T, i2=F          |
=============================================
```

With `--with-tau`, a second column shows τ, the trace that represents R in the
HyperLTL encoding: an input is true in τ at time t exactly when (t, input) is
in R. So τ's `T` cells are the pinned cells; they say *which* inputs are fixed,
not their values (the values are π's).

Operation 4 prints the MTSR in the HOA format, with the accepting states in
green (all green when every state is accepting), and saves it as
`<input>_mtsr.png` next to the input file.

## Result statuses

| status | meaning |
|--------|---------|
| `SAT_MSR` | a minimal sufficient reason was found |
| `SAT_SR` | a sufficient reason was found; with AutoHyper, no minimal one exists |
| `INVALID_PI` | π is not a trace of the system, or π does not satisfy φ |
| `NON_DETERMINISTIC` | the system is not input deterministic |
| `UNKNOWN_BOUNDED` | `--hyperQB` only: nothing was found within the bound `k`; this does **not** prove that no reason exists |

## `--hyperQB` — the bounded solver

The MSR of operations 1–3 can be computed with [HyperQB 2.0](https://github.com/HyperQB/HyperRUSTY)
instead of AutoHyper (in operations 2 and 3, the trace itself is still found by
AutoHyper):

```sh
python3 scripts/tracemsr.py examples/example_1.txt --run 1 --hyperQB
python3 scripts/tracemsr.py examples/example_1.txt --run 1 --hyperQB --k 8
```

HyperQB is a **bounded** model checker: it unrolls the systems `k` steps and
hands the result to Z3. That changes what a verdict is worth, and the tool says
so rather than hiding it:

| HyperQB says | the tool reports | is it a proof? |
|---|---|---|
| `sat`, witness certified sufficient **and** minimal | `SAT_MSR`, with the reason | **yes** — but the *certificate* is the proof, not the verdict |
| `sat`, witness certified sufficient only | `SAT_SR`, with the reason | **yes**, for sufficiency |
| `sat`, witness fails certification | falls through, ends `UNKNOWN_BOUNDED` | — |
| `unsat`, timeout, error | `UNKNOWN_BOUNDED` | **no** — only "nothing within `k`" |

So `--hyperQB` never claims that no reason exists, and never takes `INVALID_PI` from
the solver. It reports `INVALID_PI` only when the lasso fails the exact check
both backends run first (it is not a word of the system — see
[Result statuses](#result-statuses)). When any other *negative* answer has to
mean something, drop the flag: the AutoHyper backend is exact.

### Why a HyperQB `sat` is checked and not believed

A bounded `sat` used to be reported as a result on the strength of the verdict
alone. That is wrong here, and measurably so. Both the SR and the MSR query
carry a **universal** trace quantifier (`∀π₂`, `∀τ₂`), and HyperQB unrolls `G`
and `F` with no semantics-dependent tail at all —
`hltlunroller/src/lib.rs` builds `Globally` as a plain conjunction over
`k..=bound` and `Eventually` as a plain disjunction; only `X`, `U` and `R` read
the `pes`/`opt`/`hpes`/`hopt` flag. So `G ψ` reads as **true** whenever `ψ`
survives to the bound, and a universally quantified sufficiency claim is
accepted on the strength of a `k`-step prefix. One line reproduces it:

```
Forall pi . (G (~ acc[pi]))        on a 9-state model whose only `acc`
                                   state is 8 steps in
    k=6 -> sat    (WRONG)          identical under -s pes, opt, hpes, hopt
    k=8 -> unsat  (right)
```

Left unchecked that yields an R that is not sufficient, or is sufficient but
not minimal — 37 wrong rows in 818 on the SYNTCOMP benchmark, plus a further
182 whose truncated R was not sufficient either.

So HyperQB is the **search** engine and `msr_certify.py` is the **checker**:
every `sat` witness is decided exactly — plain automaton emptiness with spot,
no bound — before anything is reported.

```
sufficiency   L(system) ∩ L(pins(R, π)) ∩ L(¬Φ)  =  ∅
minimality    Good ∩ { τ₂ : τ₂ ⊆ R, τ₂ ≠ R }     =  ∅
              where Good = complement( proj_τ₂ ( L(system) ∩ L(agree(τ₂, π)) ∩ L(¬Φ) ) )
```

Checking an R costs nothing like finding it: on the benchmark the check is
~10 ms per instance against solve times in seconds to minutes, and it is broken
out as `certify_s` in the benchmark CSV so a solver-only comparison is still
available. `python3 msr_certify.py <file> --self-test` checks the checker on any
instance, with no solver in the loop.

A witness that fails certification is not a result: the pipeline carries on to
the next query, and where nothing survives the answer is `UNKNOWN_BOUNDED` with
a note saying the witness was rejected — raise `--k`, or drop `--hyperQB`.

Two more things worth knowing:

* **The bound is small on purpose.** It defaults to `|prefix| + |loop| + 2` —
  enough to walk the lasso and close its loop once. Solve time grows steeply in
  `k` (measured on one arbiter: 0.17 s at k=3, 1.18 s at k=6, 20 s at k=8,
  timeout at k=12), so a generous default turns quick instances into timeouts.
  Raise it with `--k` when a run comes back `UNKNOWN_BOUNDED`.
* **Raising `--k` is not a free win.** A larger bound gives the witness more
  room, but the MSR query is the expensive one and degrades first: at `k=11`,
  three of the shipped examples drop from `SAT_MSR` to `SAT_SR` and one to
  `UNKNOWN_BOUNDED`, because the MSR query stops answering while the cheaper SR
  query still does. Raise `k` to complete a truncated reason, then re-read the
  status.
* **`SAT_SR` means something weaker here.** On AutoHyper it is reached by
  *proving* the MSR query unsat, so it says "no minimal reason exists" — the
  liveness case. Under `--hyperQB` the reason **is** certified sufficient, but
  minimality is open: either the MSR witness was certified non-minimal (and
  could not be shrunk into a minimal one — usually the liveness case, where
  dropping a single *occurrence* of a periodic pin still suffices, so no
  minimal reason exists at all), or no MSR witness turned up inside the bound.
* **A truncated witness is checked, not discarded.** τ decodes out of the
  bounded model dump, which only covers `k` steps. When those steps repeat with
  the lasso's own period, R is periodic and is shown in full. When they do not,
  the decoded pins are read as a **finite** R — nothing pinned after the bound —
  and that is certified like any other candidate. It often holds: the reason is
  then complete (not truncated), printed on an unrolled π, and proved. When it
  does not hold, the witness is rejected rather than shown with a warning.
* **Operations 2 and 3 are split, not skipped.** Each is two steps — find a trace
  (or a counterexample), then explain it — and `--hyperQB` moves the second.
  The first stays on AutoHyper because it has to *read a trace out of the
  witness*, and HyperQB's model dump reports NuSMV `VAR`s only, so the system's
  outputs (which are `DEFINE`s) never appear in it; operation 3 additionally
  reports PASSED from an UNSAT, which a bounded UNSAT cannot support. Once the
  trace exists, explaining it is the ordinary MSR query and runs on whichever
  backend was chosen. The tool prints which half went where. Operation 4 uses no
  solver at all.

Under the hood the HyperQB path runs the *same* query on *symbolic* NuSMV
systems (`smv_builder`) rather than explicit ones, and — this is the whole
reason it works — it puts the trace π into a **module** instead of pinning it in
the formula. The formula-side pin is a positively-asserted `G`, which under
pessimistic bounded semantics is satisfied only if a loop turns up inside the
bound; it never does, so that encoding returns `unsat` at every bound on every
instance. See `hyperqb_analyzer.py` and `smv_builder.py`.

The binary is looked for at
`external_tools/HyperQB/target/release/HyperRUSTY`, then at
`external_tools/HyperQB/HyperRUSTY`; `$HYPERQB_BIN` overrides both. Unlike
AutoHyper it is not distributed with this tool — it is a Rust build linked
against a local Z3, so it has to be built on the machine that runs it (see
Setup). Without it, `--hyperQB` says exactly that and stops.

## `--check` — MTSR as a cause *checker*

CATS ("Checking and Sketching Causality in Reactive Systems") does not
synthesize causes: you write a candidate cause into the instance and it decides
whether that is one. `--check` gives TraceMSR the same mode, using the
synthesizer it already has:

```sh
python3 scripts/tracemsr.py examples/example_1_cause.txt --run 4 --check
```

The candidate goes in a `[cause]` block — the same block CATS uses, an LTL
formula over the inputs (or a QPTL one; see below). Operation 4 synthesizes the
MTSR as usual, and then asks spot whether the two accept the same input
sequences:

```
==============================================================================
 CAUSE CHECK  (is [cause] the MTSR?)
==============================================================================
 [cause]: "i1" & (X X (((!"i1") & (!"i2")) U ("i1" & (!"i2"))))
 verdict: MTSR
 the given cause IS the MTSR: it accepts exactly the input sequences that
 force the effect.
==============================================================================
```

Both sides denote an ω-language over the inputs, so this is a language question
and spot answers it exactly. The question is whether the given cause *is* the
MTSR, so the answer is yes or no:

| verdict | what it means |
|---|---|
| `MTSR` | the given cause *is* the MTSR |
| `NOT_MTSR` | it is not: the two languages differ |
| `ERROR` | unparseable, mentions non-input APs, or is a CATS *sketch* (`?{…}` holes) rather than a concrete formula |

`--check` implies operation 4 — asking it while running only operations 1–3 adds
the MTSR to the run rather than refusing.

### Compared inside what the system can produce

The MTSR is `(system × effect)` projected, so it only ever contains input words
the **system can actually produce**. A hand-written cause has no reason to be
written that way, so the comparison is made inside the system's own input
language:

```
L( [cause] )  ∩  L_in(system)   ==   L( MTSR )
```

`L_in(system)` is the system automaton projected onto the inputs — the same
projection MTSR does, minus the effect. Without it, CATS's own
`atva2022_arbiter_simple` cause `X "r1"` is reported as *not the MTSR* on a system
whose first step is forced to `!r0 & !r1`: as raw languages `X "r1"` also
"admits" `r0 & X r1`, which is not a trace of that system at all, so nothing is
claimed about it either way. Restricted, it comes out `MTSR` — the true
answer. When the restriction is what changed the verdict, the result says so,
and names what the raw answer would have been.

### QPTL causes: `E _q . …`

CATS's examples use quantified propositional temporal logic for causes LTL
cannot express — "at every *other* step", say:

```
[cause]
E _q . !_q & G (X _q <-> !_q) & F (_q & "r1")
```

`_q` is a fresh proposition the formula may choose; the middle conjunct pins it
to alternate, so the last one says *`r1` holds at some odd position*. An
existential proposition quantifier is exactly an existential **projection** of
the language onto the remaining atoms, and ω-automata are closed under it — the
same operation MTSR already performs on the outputs. So the body is translated
with `_q` as an ordinary atom and `_q` is projected away. Exact, not an
approximation. A universal `A _q .` is reported as unsupported rather than
silently mistranslated.

### On CATS's own examples

Run against the eleven instances in the CATS artifact
(`research-artifact/TSR/CATS_examples`), seven of their certified causes are
exactly our MTSR and four are not, which is expected: CATS returns *an* actual
cause for the trace it was given, MTSR is the weakest sufficient temporal
reason for the system.

| instance | `[cause]` | verdict |
|---|---|---|
| `atva2022_arbiter` | `E _q . !_q & G (X _q <-> !_q) & F (_q & "r1")` | `NOT_MTSR` |
| `atva2022_arbiter_simple` | `X "r1"` | `MTSR` |
| `atva2022_arbiter_spurious` | `F "r1"` | `NOT_MTSR` |
| `atva2022_tp_left` | `! "a"` | `MTSR` |
| `atva2022_tp_right` | `! "a"` | `MTSR` |
| `example_1` | `"i1" & X X ((!"i2") U ("i1" & !"i2"))` | `MTSR` |
| `example_1_mod` | `"i1" & X X ((!"i2") U ("i1" & !"i2"))` | `MTSR` |
| `example_6_globally` | `G "r0"` | `NOT_MTSR` |
| `example_6_odd` | `E _q . _q & G (X _q <-> !_q) & G (_q -> "r0")` | `NOT_MTSR` |
| `example_8` | `"a" \| X "a"` | `MTSR` |
| `example_8_mod` | `"a" \| X "a"` | `MTSR` |

## Verifying the results

Reading a pipeline to decide whether to believe it is slow and not very
conclusive. `smv_builder.py` — the 380 lines the `--hyperQB` path depends on —
therefore has a checker that does not read it at all, and instead asks whether
its output *denotes* the right thing.

```sh
"$SPOT_ENV_PY" scripts/verify_smv.py examples/*.txt
"$SPOT_ENV_PY" scripts/verify_smv.py --self-test examples/atva2022_arbiter.txt
```

Each module denotes an ω-language, and each has a reference language that
`smv_builder` did not produce:

| module | reference |
|---|---|
| `REAL` | `{w : G valid}` == L(the `[system]` HOA, read by spot) |
| `PIVAL` | `{w : G valid}` == L(`[lasso]`) ∩ L(HOA) |
| `PI` | == L(`[lasso]` projected onto the inputs) |
| `CAUSE` | == every input word (the 2^IP clique) |

So the script does not read `smv_builder`: it *interprets* its output with a
plain NuSMV evaluator (declarations, `case`, correct `!` > `&` > `|` > `->`
precedence), enumerates the reachable states into an automaton, and asks spot
whether that is the reference. A wrong transition table, a dropped output, a
`valid` flag one letter too wide, an off-by-one in the t=0 convention — all show
up as `NOT EQUIVALENT` with the inclusion direction named, whatever they look
like on the page. Anything the evaluator does not understand raises rather than
guessing. `--self-test` mutates the modules (negate an output, force `valid`
true, redirect a δ edge, move the init state, shift the lasso loop, weaken an
input) and confirms each mutant is caught.

Operation 4 also checks itself now: the minimized automaton is compared with the
pre-minimization projected product on every run, and a mismatch is reported
loudly instead of producing a quietly wrong MTSR (`--check` refuses to give a
verdict in that case).

## How it works

The MSR query is the 5-quantifier HyperLTL formula

```
∃π. ∃τ. ∀π₂. ∀τ₂. ∃π₃.   fixation(π) ∧ sufficiency(τ,π,π₂) ∧ minimality(τ,τ₂,π,π₃)
```

where τ ranges over candidate causes (which (input, step) pairs are pinned to
π's values), π₂ over all system traces (sufficiency: every π₂ that agrees
with π on τ's pins satisfies Φ), and the τ₂/π₃ block asserts no strictly
smaller cause is sufficient. AutoHyper resolves the *i*-th quantifier on the
*i*-th input system, so each quantifier gets a small purpose-built system:

* **CliqueQ** (π): a clique over the `2^IP` input valuations, duplicated by a
  loop-marker variable `q` — π is fixated to the user's lasso in the formula,
  over inputs only.
* **Clique** (τ, τ₂): the plain `2^IP` input clique — the candidate-cause
  space, one boolean per input per step.
* **Real** (π₂, π₃): the HOA unrolled to an explicit transition system over
  all APs — the only place the effect is evaluated.

Before any query, `trace_check.py` decides exactly — plain Python, no
solver — whether the lasso is a word of the system at all. It has to come
first: the MSR/SR queries only see π's *inputs*, so on a lasso the system
cannot produce, a cause may pin an input to a value no system trace takes there
and be "sufficient" vacuously — an MSR would be reported for a trace that does
not exist. When the lasso is a system trace, π agrees with itself on every pin,
so sufficiency is never vacuous.

The waterfall then runs MSR → SR → PI_VAL: if no MSR exists it still reports a
sufficient reason when there is one, and otherwise distinguishes "valid trace,
no cause" from "invalid trace".

**Finding π automatically** (operation 2) needs no scaffolding at all: `∃π.Φ`
on the Real system, with AutoHyper's `--witness` returning a lasso that is by
construction a valid system trace satisfying Φ. **Model checking** (operation
3) is the dual `∃π.¬Φ` — UNSAT proves `∀π.Φ`, and a SAT witness is the
counterexample fed back into the causality analysis.

## Installation and Docker

See the [README](../README.md): `install.sh` sets everything up on a clean
Ubuntu (amd64 or arm64), and the `Dockerfile` builds the same installation in a
container. Both end with `smoke_test.sh`.

Operation 4 writes its `<input>_mtsr.png` next to the input file; in Docker,
copy it to the shared folder to see it on your machine
(`cp examples/*_mtsr.png /shared/`).

## Repository layout

```
scripts/
  tracemsr.py         the entry point (interactive checklist / --run)
  msr_analyzer.py     MSR → SR → PI_VAL waterfall, per-quantifier systems
  trace_check.py      is the [lasso] a word of the [system]? (exact, run first)
  msr_systems.py      CliqueQ builder (π carrier)
  msr_formulas.py     the MSR / SR HyperLTL formulas
  system_builder.py   Real (unrolled HOA) + Clique (cause space) builders
  pi_finder.py        find-π-automatically + model-checking flows
  converter.py        HOA parsing, determinism firewall, combined LTS
  hyperLTL.py         legacy combined-system formulas (PI_VAL)
  trace_analyzer.py   witness decoding helpers
  cli_viewer.py       terminal tables; pinned inputs green, Tau via --with-tau
  MTSR_one_example.py the MTSR pipeline: colored-HOA view + image (spot env)
  cause_check.py      --check: [cause] vs the MTSR, as a language question
  verify_smv.py       re-checks smv_builder's modules against their languages
  mtsr_view.py        thin compatibility wrapper around MTSR_one_example
  hyperqb_analyzer.py --hyperQB: the same waterfall on the bounded solver
  hyperqb_backend.py  AutoHyper dialect -> HyperQB syntax, and the runner
  msr_certify.py      the exact check every bounded witness has to pass
  smv_builder.py      the symbolic NuSMV modules HyperQB runs on
  smv_formulas.py     the MSR / SR / PI_VAL queries for that layout
examples/             ready-to-run inputs (reachability, safety, ATVA'22 …)
external_tools/AutoHyper/   the AutoHyper model checker (binary + license)
bench/                the benchmark suite: corpora, drivers, recorded results,
                      and the generators that built the corpora (bench/README.md)
docker/smoke.sh       the tool part of smoke_test.sh
install.sh            installs everything (clean Ubuntu, amd64/arm64)
smoke_test.sh         does this installation work?
Dockerfile            install.sh in a clean Ubuntu 26.04 container
```

The only persistent artifact is the rendered `<base>_mtsr.png` (written next
to the input file). Solver temporaries — the generated systems and formulas,
AutoHyper's `aut1.hoa`/`autRes.hoa`, HyperQB's `commands.txt`, and the
synthesized trace files of operations 2/3 — live in a private temporary
directory per run, which is also the solver's working directory, and are
deleted when the run finishes. So runs can be started side by side, from the
same directory or on files in the same folder, without reading each other's
files.

Stopping a run cleans up after it however it is stopped: Ctrl-C, `kill`,
`timeout`, or closing the terminal all shut down the solver in flight
(AutoHyper, HyperQB, or the `autfilt` behind operation 4) before the tool exits.

## Credits

* [AutoHyper](https://github.com/AutoHyper/autohyper) — Beutner & Finkbeiner,
  the HyperLTL model checker doing the heavy lifting (license in
  `external_tools/AutoHyper/LICENSE`).
* [spot](https://spot.lre.epita.fr/) — ω-automata manipulation, and the
  language equivalence `--check` is built on.
* [HyperQB 2.0](https://github.com/HyperQB/HyperRUSTY) — Hsu et al., the
  bounded HyperLTL model checker behind `--hyperQB` (not shipped here;
  `install.sh` builds it at a pinned commit).
* [CATS](https://github.com/cats-tool/cats) — Beutner, Finkbeiner, Frenkel and
  Metzger, "Checking and Sketching Causality in Reactive Systems". `--check`
  answers their question (is this candidate a cause?) and borrows their
  `[cause]` block for it.
