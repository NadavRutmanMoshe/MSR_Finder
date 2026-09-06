# MSR_Finder

**Finding Minimal Sufficient Reasons (MSR) for temporal effects in reactive
systems.**

Given a reactive system (an automaton whose atomic propositions are split into
environment **inputs** and system **outputs**), an LTL **effect** formula Φ,
and an infinite execution **trace** π (a lasso), MSR_Finder answers the
causality question:

> *Which input values, at which time steps, are responsible for the effect?*

A **Sufficient Reason (SR)** is a set of (input, time step) pairs of π such
that *every* system trace that agrees with π on those pairs also satisfies Φ.
A **Minimal Sufficient Reason (MSR)** is an SR from which no pair can be
removed — the tightest explanation of why Φ happened on π.

The problem is encoded in **HyperLTL** (a logic that quantifies over multiple
traces at once) and solved with the model checker
[AutoHyper](https://github.com/AutoHyper/autohyper).

## Quick start

```sh
cd scripts
python3 tracemsr.py ../examples/example_1.txt
```

This opens an interactive checklist:

```
Select analyses for example_1.txt
 > [x] 1. MSR with the given PI
   [ ] 2. MSR for Phi — find a PI automatically
   [ ] 3. Model checking of Phi (counterexample + why)
   [ ] 4. MTSR — Minimal Temporal Sufficient Reason
       ▶ Start
     table  [t]: Pi only, positions pinned by R in green
     solver [h]: AutoHyper — exact
     mtsr   [c]: (no [cause] block in this file)
 ENTER/SPACE toggle · ↑/↓ move · a all · t/h/c switches · ENTER on Start runs · q quit
```

ENTER (or SPACE) toggles the highlighted analysis; move down to **▶ Start**
and press ENTER to run the selection in order. The three keys at the bottom
switch how the run behaves — `t` the result table, `h` the solver
(`--hyperQB`), `c` the MTSR mode (`--check`). For scripts / CI, skip the menu
with `--run`:

```sh
python3 tracemsr.py ../examples/example_1.txt --run all
python3 tracemsr.py ../examples/example_1.txt --run 2,3 --timeout 300
python3 tracemsr.py ../examples/example_1.txt --run 1 --hyperQB
python3 tracemsr.py ../examples/example_1_cause.txt --run 4 --check
```

## The result table

Analyses 1–3 print the trace with the reason on it — one column, the actual
trace π, with the (input, time) positions **pinned by R in green**:

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

The witness τ is no longer shown by default. `"ip"_τ` is the *indicator* of
membership in R — the query pins π with `{"ip"_τ} -> ({"ip"_π} <-> {"ip"_π2})`
— so τ's `T`/`F` cells are exactly the green cells, and showing them as a
second column of a *trace* invited reading a green `r1=T` under τ as "r1 is
true here" even where π has `r1=F` at that pinned position. R itself is
printed as an explicit set of pairs, which is the thing worth reading.

Add `--with-tau` (or press `t` in the menu) to get the old two-column layout
back — the `found reason representation Tau` block plus the τ column:

```sh
python3 tracemsr.py ../examples/example_1.txt --run 1 --with-tau
```

## The four analyses

| # | analysis | needs `[lasso]`? | what it does |
|---|----------|------------------|--------------|
| 1 | **MSR with the given PI** | yes (greyed out if absent) | The classic flow: the MSR for the trace written in the file. |
| 2 | **MSR for Φ — find a PI** | no | Asks AutoHyper for *some* system trace satisfying Φ (`∃π.Φ`, with witness). The found lasso is displayed and its MSR is computed. UNSAT means *no* trace satisfies Φ at all. |
| 3 | **Model checking of Φ** | no | Checks `∃π.¬Φ`. UNSAT ⇒ **PASSED**: every trace satisfies Φ (the empty cause is then trivially sufficient). SAT ⇒ **FAILED**: the counterexample is displayed and the same MSR machinery runs on it with effect `¬Φ` — explaining *why the violation happened*. |
| 4 | **MTSR — Minimal Temporal Sufficient Reason** | no | The automaton of (system × effect) projected onto the inputs and SAT-minimized: the minimal temporal shape of input behaviour that forces Φ. The minimized HOA is displayed with the **accepting states in green** (entirely green when the acceptance is `t` — every state accepting) and rendered to `<base>_mtsr.png` (spot + autfilt + graphviz). With `--check` it also verifies the instance's `[cause]` block against what it built. |

Each analysis reports its wall-clock time when it finishes.

Analyses 2 and 3 only need `[inputs]`, `[effect]` and `[system]` — the user
never has to construct a trace by hand.

## `--hyperQB` — the bounded solver

Analysis 1 can be solved by [HyperQB 2.0](https://github.com/HyperQB/HyperRUSTY)
instead of AutoHyper:

```sh
python3 tracemsr.py ../examples/example_1.txt --run 1 --hyperQB
python3 tracemsr.py ../examples/example_1.txt --run 1 --hyperQB --k 8
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

So `--hyperQB` never answers `UNSAT_NO_CAUSE` or `INVALID_PI`. When a *negative*
answer has to mean something, drop the flag: the AutoHyper backend is exact.

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
* **Analyses 2 and 3 are split, not skipped.** Each is two steps — find a trace
  (or a counterexample), then explain it — and `--hyperQB` moves the second.
  The first stays on AutoHyper because it has to *read a trace out of the
  witness*, and HyperQB's model dump reports NuSMV `VAR`s only, so the system's
  outputs (which are `DEFINE`s) never appear in it; analysis 3 additionally
  reports PASSED from an UNSAT, which a bounded UNSAT cannot support. Once the
  trace exists, explaining it is the ordinary MSR query and runs on whichever
  backend was chosen. The tool prints which half went where. Analysis 4 uses no
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
whether that is one. `--check` gives MSR_Finder the same mode, using the
synthesizer it already has:

```sh
python3 tracemsr.py ../examples/example_1_cause.txt --run 4 --check
```

The candidate goes in a `[cause]` block — the same block CATS uses, an LTL
formula over the inputs (or a QPTL one; see below). Analysis 4 synthesizes the
MTSR as usual, and then asks spot whether the two accept the same input
sequences:

```
==============================================================================
 CAUSE CHECK  (is [cause] the same language as the MTSR?)
==============================================================================
 [cause]: "i1" & (X X (((!"i1") & (!"i2")) U ("i1" & (!"i2"))))
 verdict: EQUIVALENT
 the given cause and the synthesized MTSR accept exactly the same input
 sequences.
==============================================================================
```

Both sides denote an ω-language over the inputs, so this is a language question
and spot answers it exactly. When they differ, the verdict names the direction —
the two ways of being wrong are not the same failure:

| verdict | what it means |
|---|---|
| `EQUIVALENT` | the given cause *is* the MTSR |
| `CAUSE_SUBSET_OF_MTSR` | too **strong**: everything it admits does force Φ, but it excludes inputs that also force Φ. Sufficient, not weakest |
| `MTSR_SUBSET_OF_CAUSE` | too **weak**: it admits inputs that do *not* force Φ, so it is not a sufficient reason at all |
| `INCOMPARABLE` | both at once |
| `ERROR` | unparseable, mentions non-input APs, or is a CATS *sketch* (`?{…}` holes) rather than a concrete formula |

`--check` implies analysis 4 — asking it while running only analyses 1–3 adds
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
`atva2022_arbiter_simple` cause `X "r1"` is reported as *too weak* on a system
whose first step is forced to `!r0 & !r1`: as raw languages `X "r1"` also
"admits" `r0 & X r1`, which is not a trace of that system at all, so nothing is
claimed about it either way. Restricted, it comes out `EQUIVALENT` — the true
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
(`research-artifact/TSR/CATS_examples`), every one of their certified causes
comes out **sufficient** — which is the sanity check that matters, since CATS
proved them causes. Seven are exactly our MTSR; four are strictly stronger than
it, which is expected: CATS returns *an* actual cause for the trace it was
given, MTSR is the weakest sufficient temporal reason for the system.

| instance | `[cause]` | verdict |
|---|---|---|
| `atva2022_arbiter` | `E _q . !_q & G (X _q <-> !_q) & F (_q & "r1")` | `CAUSE_SUBSET_OF_MTSR` |
| `atva2022_arbiter_simple` | `X "r1"` | `EQUIVALENT` |
| `atva2022_arbiter_spurious` | `F "r1"` | `CAUSE_SUBSET_OF_MTSR` |
| `atva2022_tp_left` | `! "a"` | `EQUIVALENT` |
| `atva2022_tp_right` | `! "a"` | `EQUIVALENT` |
| `example_1` | `"i1" & X X ((!"i2") U ("i1" & !"i2"))` | `EQUIVALENT` |
| `example_1_mod` | `"i1" & X X ((!"i2") U ("i1" & !"i2"))` | `EQUIVALENT` |
| `example_6_globally` | `G "r0"` | `CAUSE_SUBSET_OF_MTSR` |
| `example_6_odd` | `E _q . _q & G (X _q <-> !_q) & G (_q -> "r0")` | `CAUSE_SUBSET_OF_MTSR` |
| `example_8` | `"a" \| X "a"` | `EQUIVALENT` |
| `example_8_mod` | `"a" \| X "a"` | `EQUIVALENT` |

## Verifying the results

Reading a pipeline to decide whether to believe it is slow and not very
conclusive. `smv_builder.py` — the 380 lines the `--hyperQB` path depends on —
therefore has a checker that does not read it at all, and instead asks whether
its output *denotes* the right thing.

```sh
~/micromamba/envs/spot/bin/python3 verify_smv.py ../examples/*.txt
~/micromamba/envs/spot/bin/python3 verify_smv.py --self-test ../examples/atva2022_arbiter.txt
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

Analysis 4 also checks itself now: the minimized automaton is compared with the
pre-minimization projected product on every run, and a mismatch is reported
loudly instead of producing a quietly wrong MTSR (`--check` refuses to give a
verdict in that case).

## Input file format

```
[info]       free-text description
[inputs]     quoted AP names that are environment inputs, e.g. "i1" "i2"
[cause]      a candidate cause to verify (optional — only --check reads it):
             an LTL formula over the inputs, e.g.
             "i1" & (X X (((!"i1") & (!"i2")) U ("i1" & (!"i2"))))
[effect]     the LTL effect formula, e.g. F "error"
[lasso]      the actual trace (optional — needed only for analysis 1):
             AP: "i1" "i2" "error"      the trace's AP order
             Prefix: {0 1} {0 1} {0}    one {index set} per step
             Loop: {0 2}                the infinitely repeated part
[system]     the system as a HOA automaton (HOA: v1 ... --END--)
```

A step's `{…}` lists the indices (into the `AP:` line) of the propositions
that are **true** at that step; `{}` means all false. The system must be
**input-deterministic** (same state + same inputs ⇒ same successor); this is
validated before any query runs and violations are reported as
`NON_DETERMINISTIC`.

## Result statuses

| status | meaning |
|--------|---------|
| `SAT_MSR` | a Minimal Sufficient Reason was found (green table, pins highlighted) |
| `SAT_SR` | a Sufficient Reason exists but no *minimal* one (e.g. `G F x` liveness effects: any cause can be shrunk forever) |
| `UNSAT_NO_CAUSE` | π is a valid trace but no sufficient cause exists |
| `INVALID_PI` | the given lasso is not a real system trace, or Φ does not hold on it |
| `NON_DETERMINISTIC` | the system failed the input-determinism firewall |
| `UNKNOWN_BOUNDED` | `--hyperQB` only: nothing was found within the bound `k`. **Not** a proof that no cause exists |

Section headers must sit alone on their own line — that is what tells a
`[cause]` header apart from the word "[cause]" inside the `[info]` prose.

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

The waterfall runs MSR → SR → PI_VAL: if no MSR exists it still reports a
sufficient reason when there is one, and otherwise distinguishes "valid trace,
no cause" from "invalid trace".

**Finding π automatically** (analysis 2) needs no scaffolding at all: `∃π.Φ`
on the Real system, with AutoHyper's `--witness` returning a lasso that is by
construction a valid system trace satisfying Φ. **Model checking** (analysis
3) is the dual `∃π.¬Φ` — UNSAT proves `∀π.Φ`, and a SAT witness is the
counterexample fed back into the causality analysis.

## Setup

Runs on Linux / WSL. Requirements:

1. **Python 3.8+** (standard library only for the pipelines).
2. **.NET 8 runtime** — AutoHyper (shipped in `external_tools/AutoHyper/app/`)
   is a .NET binary. Either install system-wide, or user-local:
   `curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 8.0 --runtime dotnet`
   (installs to `~/.dotnet`; the pipeline sets `DOTNET_ROOT` automatically if
   it finds it there).
3. **spot** (`ltl2tgba`, `autfilt`) — AutoHyper shells out to it. Easiest
   without root, via [micromamba](https://mamba.readthedocs.io):
   `micromamba create -n spot -c conda-forge spot graphviz`.
   Then point `external_tools/AutoHyper/app/paths.json` at the env's
   `autfilt` and `ltl2tgba` absolute paths.
4. **For analysis 4 and `--check`**: spot's Python bindings + graphviz `dot`
   (both in the micromamba env above). `tracemsr.py` runs them under
   `~/micromamba/envs/spot/bin/python3` by default; override with the
   `SPOT_ENV_PY` environment variable.
5. **For `--hyperQB` only**: a HyperQB 2.0 build. It is a Rust binary linked
   against Z3, and is not shipped here:

   ```sh
   git clone https://github.com/HyperQB/HyperRUSTY && cd HyperRUSTY
   cargo build --release          # needs libz3 on the linker path
   ```

   Then put the resulting `target/release/HyperRUSTY` at
   `external_tools/HyperQB/target/release/HyperRUSTY`, or set `$HYPERQB_BIN` to
   wherever it lives. If the build does not carry an rpath to its libz3, set
   `$HYPERQB_LIB` to that library directory.

## Repository layout

```
scripts/
  tracemsr.py         the entry point (interactive checklist / --run)
  msr_analyzer.py     MSR → SR → PI_VAL waterfall, per-quantifier systems
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
```

The only persistent artifact is the rendered `<base>_mtsr.png` (written next
to the input file). Solver temporaries and the synthesized trace files of
analyses 2/3 are deleted automatically when the run finishes (and are
git-ignored as a safety net).

## Credits

* [AutoHyper](https://github.com/AutoHyper/autohyper) — Beutner & Finkbeiner,
  the HyperLTL model checker doing the heavy lifting (license in
  `external_tools/AutoHyper/LICENSE`).
* [spot](https://spot.lre.epita.fr/) — ω-automata manipulation, and the
  language equivalence `--check` is built on.
* [HyperQB 2.0](https://github.com/HyperQB/HyperRUSTY) — Hsu et al., the
  bounded HyperLTL model checker behind `--hyperQB` (not shipped here; see
  Setup).
* [CATS](https://github.com/cats-tool/cats) — Beutner, Finkbeiner, Frenkel and
  Metzger, "Checking and Sketching Causality in Reactive Systems". `--check`
  answers their question (is this candidate a cause?) and borrows their
  `[cause]` block for it.
