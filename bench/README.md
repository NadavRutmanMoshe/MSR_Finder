# bench — the benchmark of Section 5

## Layout

```
bench/
  CATS_RL_ip3/      823 instances, |IP| <= 3   (timeout 120 s)    the paper's corpus
  CATS_RL_ip4/      164 instances, |IP| = 4    (timeout 300 s)
  CATS_RL/          all 1970 generated instances, before the gate
  nexts/            CATS's X^k "error" family, 100 systems (Table 5)
  corp_arbiters/    the arbiters of CORP's Table 1 (Table 6)
  results/          everything we measured (read-only; see below)
  scripts/          the drivers, one per tool comparison
  paper/            the generators of the paper's tables and figures
  gen/              how the corpora were generated
  tools/            CATS, CORP, ExplainHyper -- fetched by tools/fetch.sh, not vendored
  run.sh            = scripts/rerun.py
```

## The corpus

CATS's 204 SYNTCOMP systems (their `eval/syntcomp_instances.json`, |Q| <= 300,
1 <= |IP| <= 4, |OP| >= 1), each with a random lasso and ten random effects
drawn as CATS draws them (`randltl --tree-size=22..30` over the outputs). An
instance is kept only if the effect is not trivial in the system (neither
impossible nor universal) and holds on the lasso (`results/gate.csv`, made by
`scripts/pi_gate.py`). That leaves 987 instances over 197 systems.
Rebuilding the corpus from CATS's list of systems, and comparing it with the
shipped one (about 2 minutes; it is byte-identical):

```sh
gen/make_corpus.sh            # needs CATS's eval/syntcomp_instances.json (install.sh fetches it)
```

## `results/`

```
results/
  gate.csv, gate_ok.txt             the gate's verdict for all 1970
  ip3/  ip4/                        one directory per corpus:
    MSR/msr_autohyper.csv             MSR, AutoHyper
    MSR/msr_hyperqb.csv               MSR, HyperQB (every witness certified exactly)
    MSR/explainhyper.csv              ExplainHyper, the tool of [11]
    MTSR_CORP/{mtsr,corp}.csv         MTSR and CORP, same pass
    MTSR_CATS_check/{mtsr,cats_check}.csv   (ip3 only) MTSR --check and CATS --check
  MTSR_CATS_sketch/{mtsr,cats_sketch}.csv   Table 5
  curated/corp_curated.csv          Table 6
  curated/cats_handcrafted.csv      Table 4
  stats/                            derived per-instance data: the LTL search
                                    (Table 8), the shape of the reasons the lasso
                                    sizes of Table 3 need, candidate kinds
```

One row per instance and tool, with the answer (status, the reason or the
automaton size, the verdict) and the wall-clock time. Each instance was run by
every tool before the next instance started, so all tools saw the same machine
state.

## Re-running

`./run.sh` (= `scripts/rerun.py`) — see the top-level README, section 3, for
the subset and the full run. Useful options:

```sh
./run.sh --plan                         # what would run and how long it took us
./run.sh --instances arbiter2__e09      # chosen instances
./run.sh --corpus ip3 --only msr        # one corpus, one group of tools
./run.sh --out /some/dir                # somewhere else than bench/rerun
```

Groups: `msr` (AutoHyper, HyperQB), `eh` (ExplainHyper), `mtsr-corp`,
`cats-check`, `sketch`, `curated`. Every driver appends one row per instance and
skips instances it already has, so a stopped run continues where it stopped.

**Running the drivers on your own instances.** Each driver in `scripts/` takes a
directory of instances (the format of `docs/TOOL.md`):

```sh
python3 scripts/msr_bench.py --model autohyper --corpus my_dir --no-gate --out my.csv
"$SPOT_ENV_PY" scripts/mtsr_corp_bench.py my_dir --no-gate -o my_out/
```

`--no-gate` skips the PI_VAL gate (which reads `results/gate.csv`); run
`scripts/pi_gate.py` on your directory first if you want it.

## The two offline analyses

* **Table 8** (is the MTSR expressible as a small LTL formula?) comes from an
  exhaustive search over formulas up to size 9, on 4 cores:
  `"$SPOT_ENV_PY" paper/ltl_rerun_catsrl.py --max-size 9 --budget 240`
  (writes `paper/out/ltl_rerun/`; `results/stats/mtsr_ltl.csv` is the recorded one).
* **The lasso sizes of Table 3** need the shape of the witness τ, which the
  MSR CSVs do not store; `results/stats/tau_shape_rerun.csv` re-ran the 28
  instances whose reason has a loop pin to record it.

## Notes

`docs/BENCH_NOTES.md` has the longer notes written while building the suite:
why HyperQB witnesses are certified rather than trusted, what ExplainHyper is
handed and why, the orphan-solver reaper and the cooling pauses, and how the
CATS candidates are drawn (re-runs draw exactly the recorded candidates: the
draw is seeded per instance with `PYTHONHASHSEED=0`).
