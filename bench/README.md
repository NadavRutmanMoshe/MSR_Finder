# bench: the benchmark of Section 5

How to re-run it is in the [main README](../README.md) (Reproducing the paper). This file describes what is here and how it was made.

- `CATS_RL_ip3/`: the 823 instances with |IP| ≤ 3 (timeout 120 s).
- `CATS_RL_ip4/`: the 164 instances with |IP| = 4 (timeout 300 s).
- `CATS_RL/`: all 1970 generated instances, before filtering (see below).
- `nexts/`: CATS's X^k "error" family, 100 systems (Table 5).
- `corp_arbiters/`: the arbiters of CORP's Table 1 (our Table 6).
- `results/`: every result we recorded, one CSV per tool.
- `scripts/`: the scripts that run each tool on the benchmark.
- `paper/`: the scripts that make the tables and figures of the paper.
- `gen/`: the scripts that generated the benchmark.
- `tools/`: CATS, CORP and ExplainHyper (downloaded by `install.sh`, not included).
- `run.sh`: runs the benchmark (main README, levels 2 and 3).

## The benchmark

We built the benchmark the way CATS built theirs, from the same systems:

1. **Systems:** CATS's 204 SYNTCOMP systems (their list `eval/syntcomp_instances.json`: at most 300 states, 1 to 4 inputs, at least one output). Each system gets one random trace.
2. **Effects:** for each system, random LTL formulas over its outputs, drawn as CATS draws them (spot's `randltl`, formula size 22 to 30). We keep the first 10 that are neither impossible nor always true in the system. For 7 systems no formula passes, which leaves 197 systems and 1970 instances (`CATS_RL/`).
3. **Filter:** we keep only the instances where the effect holds on the trace; otherwise there is nothing to explain. That leaves 987 instances (`results/gate.csv` has the verdict for every one of the 1970).
4. **Split:** by number of inputs, into `CATS_RL_ip3/` (823) and `CATS_RL_ip4/` (164).

Everything is seeded, so the benchmark can be rebuilt exactly. This command rebuilds it from CATS's list and compares it with ours, file by file (about 2 minutes):

```
bench/gen/make_corpus.sh
```

It should say `identical to the shipped one` for all three folders.

## The results files

`results/` holds every result we recorded, one CSV per tool:

- `ip3/` and `ip4/`: one folder per part of the benchmark, each with:
  - `MSR/msr_autohyper.csv`, `MSR/msr_hyperqb.csv`: MSR with AutoHyper and with HyperQB
  - `MSR/explainhyper.csv`: ExplainHyper
  - `MTSR_CORP/mtsr.csv`, `MTSR_CORP/corp.csv`: MTSR and CORP
  - `MTSR_CATS_check/mtsr.csv`, `MTSR_CATS_check/cats_check.csv`: MTSR and CATS checking a candidate cause (`ip3/` only)
- `MTSR_CATS_sketch/`: MTSR and CATS sketching (Table 5)
- `handcrafted/cats_handcrafted.csv`, `handcrafted/corp_curated.csv`: the hand-crafted instances of CATS (Table 4) and CORP (Table 6)
- `gate.csv`: the filter's verdict for each of the 1970 generated instances
- `stats/`: data derived from the runs, used for Tables 3 and 8

Each CSV has one row per instance. The row says which instance it is (`instance`, its `effect`, the number of states and inputs of the system), what the tool answered (a status, and the result: for MSR the reason and its size, for MTSR or CORP the size of the automaton), and how long it took, in seconds. For example, the row of `TorcsGearing__e02` in `ip3/MTSR_CORP/mtsr.csv` says that MTSR succeeded (`mtsr_status` = `OK`) with an automaton of 1 state (`mtsr_states`) in 0.168 seconds (`mtsr_s`). What each status means is explained in `docs/TOOL.md`.

A re-run (`bench/run.sh`) writes the same files, in the same layout, to `bench/rerun/`.

## The offline analysis

**Table 8** (which MTSRs can be written as a small LTL formula) needs more than the benchmark runs. For each MTSR of the benchmark, this searches all LTL formulas up to size 9 for one with the same language (about 6 minutes, on 4 cores):

```
"$SPOT_ENV_PY" bench/paper/ltl_rerun_catsrl.py --max-size 9 --budget 240
```

The result goes to `bench/paper/out/ltl_rerun/`. The recorded one is `results/stats/mtsr_ltl.csv`.

## More

- How to re-run the benchmark, and how to compare the tools on your own instances: the [main README](../README.md).
- [`docs/BENCH_NOTES.md`](../docs/BENCH_NOTES.md): background notes written while building the benchmark (historical; not needed to use it).

