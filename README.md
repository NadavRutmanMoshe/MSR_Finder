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
     table [t]: Pi only, positions pinned by R in green
 ENTER/SPACE toggle · ↑/↓ move · a all · t table · ENTER on Start runs · q quit
```

ENTER (or SPACE) toggles the highlighted analysis; move down to **▶ Start**
and press ENTER to run the selection in order. For scripts / CI, skip the
menu with `--run`:

```sh
python3 tracemsr.py ../examples/example_1.txt --run all
python3 tracemsr.py ../examples/example_1.txt --run 2,3 --timeout 300
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
| 4 | **MTSR — Minimal Temporal Sufficient Reason** | no | The automaton of (system × effect) projected onto the inputs and SAT-minimized: the minimal temporal shape of input behaviour that forces Φ. The minimized HOA is displayed with the **accepting states in green** (entirely green when the acceptance is `t` — every state accepting) and rendered to `<base>_mtsr.png` (spot + autfilt + graphviz). |

Each analysis reports its wall-clock time when it finishes.

Analyses 2 and 3 only need `[inputs]`, `[effect]` and `[system]` — the user
never has to construct a trace by hand.

## Input file format

```
[info]       free-text description
[inputs]     quoted AP names that are environment inputs, e.g. "i1" "i2"
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
4. **For analysis 4 only**: spot's Python bindings + graphviz `dot` (both in
   the micromamba env above). `tracemsr.py` runs it under
   `~/micromamba/envs/spot/bin/python3` by default; override with the
   `SPOT_ENV_PY` environment variable.

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
  mtsr_view.py        thin compatibility wrapper around MTSR_one_example
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
* [spot](https://spot.lre.epita.fr/) — ω-automata manipulation.
