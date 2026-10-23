# TraceMSR — artifact

Artifact of the TACAS 2027 tool paper

> **TraceMSR: A Tool for Synthesizing Sufficient Reasons for Reactive Systems**
> Nadav Rutman Moshe and Hadar Frenkel (EasyChair submission #5598)

The tool is called TraceMSR in the paper; this repository is named
`MSR_Finder`. Both refer to the same code.

* **Artifact (permanent, DOI):** https://doi.org/10.5281/zenodo.XXXXXXX  <!-- TODO DOI -->
* **Source repository:** https://github.com/NadavRutmanMoshe/MSR_Finder
* **The paper as submitted:** [`paper/TACAS27_paper_5598.pdf`](paper/TACAS27_paper_5598.pdf)  <!-- TODO add the PDF -->

## Badges

We apply for all three badges: **Available**, **Functional** and **Reusable**.

## Contents

| what | where |
|---|---|
| the tool (Python, runs AutoHyper / HyperQB / spot) | `scripts/`, entry point `scripts/tracemsr.py` |
| example inputs, including the running example of the paper | `examples/` |
| AutoHyper (binary, GPL-3.0, shipped unchanged) | `external_tools/AutoHyper/` |
| the benchmark of Section 5: 987 instances + every recorded result | `bench/` ([`bench/README.md`](bench/README.md)) |
| the scripts that produce every table and figure of Section 5 and Appendix D | `bench/paper/` |
| installation, smoke test, container | `install.sh`, `smoke_test.sh`, `Dockerfile` |
| tool documentation: input format, the four operations, flags, statuses | [`docs/TOOL.md`](docs/TOOL.md) |

## Requirements and tested platforms

* Linux, **amd64 or arm64**. No special hardware, no proprietary software.
* About 6 GB of disk for the installation, 8 GB of RAM (16 GB to re-run the
  full benchmark: a few instances reach the 6 GB memory cap of the MTSR
  minimization).
* Network access during installation only (all downloads are version-pinned).

| platform | host | tested |
|---|---|---|
| Ubuntu 26.04 container (`Dockerfile`), amd64 | Windows 11 / Docker Desktop | install + smoke test |
| Ubuntu 26.04 container (`Dockerfile`), arm64 | (emulated) | <!-- TODO --> |
| Ubuntu 22.04 under WSL2, amd64 (Intel i7-8565U, 16 GB) | the machine the paper's numbers were measured on | everything |
| TACAS 2027 VM, amd64 | a clean laptop | <!-- TODO --> |

## 1. Setup (about 15 minutes on amd64, 45 on arm64)

In the TACAS VM (or any Ubuntu with `build-essential`, `curl` and `git`), from
the artifact directory:

```sh
bash install.sh
source env.sh
```

`install.sh` installs everything under your home directory, without root and
in pinned versions: spot 2.15.1 and Python 3.14 (via micromamba), the .NET 8
runtime for AutoHyper, a Rust toolchain to build HyperQB 2.0, and the three
tools the paper compares against (CATS, CORP, ExplainHyper) at the exact
commits we used. On arm64 it builds spot from source, which is the slow part.
Everything it does is logged to `install.log`. Run `source env.sh` in every new
terminal.

**Alternatively, with Docker** (any host with Docker, amd64 or arm64):

```sh
docker build -t tracemsr .            # runs install.sh and the smoke test inside
docker run --rm -it tracemsr          # a shell in the container, ready to use
```

## 2. Smoke test (about 1 minute)

```sh
./smoke_test.sh
```

It checks three things and ends with `SMOKE TEST PASSED`:

1. **the tool**: the MSR of `examples/example_1.txt` (AutoHyper), its MTSR
   (spot, rendered to a PNG with graphviz), `--check`, and the HyperQB backend,
   each against its known answer;
2. **the paper's tables**: all 12 tables and figures of Section 5 and
   Appendix D are regenerated from the recorded results and compared, number by
   number, with the submitted paper (all must say `SAME`);
3. **the baselines**: one benchmark instance through every tool (AutoHyper,
   HyperQB, ExplainHyper, MTSR, CORP, CATS), each answer compared with the
   recorded one.

## 3. Replicating the results of the paper

There are three levels. Each command below is run from the artifact directory
after `source env.sh`.

### 3a. Every table and figure from the recorded results (1 minute)

```sh
cd bench/paper && "$SPOT_ENV_PY" make_tables.py
```

This regenerates every table and figure of Section 5 and Appendix D into
`bench/paper/out/` from the CSVs in `bench/results/`, and reports for each
whether its numbers are those of the submitted paper (copies in
`bench/paper/expected/`). The figures are compared through the numbers in their
captions; their PDFs are next to the submitted ones to look at. The numbers that
the text of Section 5 quotes are written to `out/work/stats/plan2_numbers.json`.

| paper | file | command (`make_tables.py <name>`) | from |
|---|---|---|---|
| Table 1 | MSR and MTSR per number of inputs | `tab_main` | both corpora |
| Table 2, Fig. 10 | AutoHyper vs. HyperQB | `tab_msr_backends` | both corpora |
| Table 3 | MSR vs. ExplainHyper [11] | `tab_msreh` | both corpora |
| Table 4 | CATS's hand-crafted instances | `tab_catshand` | `results/curated/` |
| Fig. 7 | MTSR vs. CATS cause checking | `fig_data_cats_check_time` | \|IP\| ≤ 3 |
| Table 5 | MTSR vs. CATS sketching | `tab_catssketch` | CATS's X^k family (`bench/nexts`) |
| Table 6 | MTSR vs. CORP on CORP's arbiters | `tab_corpcurated` | `results/curated/` |
| Table 7 | summary of the comparisons | `tab_data_resultsizes` | both corpora |
| Fig. 6 | running time vs. system size | `fig_data_msr_mtsr_size_v1` | \|IP\| ≤ 3 |
| Fig. 9 | MSR and ExplainHyper vs. system size | `fig_data_msr_size` | \|IP\| ≤ 3 |
| Table 8 | LTL-expressibility of the MTSR | `tab_data_ltl` | `results/stats/` |
| Figs. 4, 5 | the session of Section 4.5 | `cd scripts && python3 tracemsr.py ../examples/biased_arbiter.txt --run 1,4` | — |

### 3b. Re-measure a representative subset (about 5 hours)

```sh
cd bench
./run.sh --plan        # what will run, and how long it took on our machine
./run.sh               # every 10th instance of each |IP| class: 83 + 17 instances
```

This runs every tool on each selected instance in turn, as the recorded results
were measured, with the paper's timeouts (120 s, 300 s for |IP| = 4), followed
by the CATS sketch comparison and the curated instances of Tables 4 and 6. On
the machine of the paper it takes 4.5 hours; expect more in a VM. Ctrl-C is
safe at any point: the same command continues where it stopped. Results go to
`bench/rerun/`; the recorded results are never touched. Then:

```sh
python3 scripts/compare_runs.py                              # instance by instance
"$SPOT_ENV_PY" paper/make_tables.py --src rerun --subset     # the tables, recorded vs. yours
```

`compare_runs.py` compares the *answers* (status, reason size, automaton size,
verdicts) of every tool on every instance with the recorded ones, and reports
each as `same`, `timeout` (one side timed out — a matter of machine speed) or
`DIFFERENT`, together with how much faster or slower your machine is.
`make_tables.py --subset` builds every table twice over the re-run instances —
from our recorded rows and from yours — so they can be read side by side.
Running times differ between machines; the counts and sizes should not, up to
instances that finish close to the timeout.

### 3c. The full benchmark (about 45 hours)

```sh
cd bench
./run.sh --full
"$SPOT_ENV_PY" paper/make_tables.py --src rerun
```

`./run.sh --corpus ip3`, `--only msr,mtsr-corp` or `--instances NAME ...` run a
part of it (see `python3 scripts/rerun.py --help`). The two offline analyses
behind Table 8 (the LTL search) and the benchmark generation itself are
described in [`bench/README.md`](bench/README.md).

## 4. Using the tool on your own inputs (Reusable)

```sh
cd scripts
python3 tracemsr.py ../examples/example_1.txt              # interactive menu
python3 tracemsr.py ../examples/example_1.txt --run all    # all four operations
python3 tracemsr.py my_system.txt --run 1 --hyperQB        # MSR with HyperQB
```

An input is one text file with the system (a HOA automaton), the inputs, the
LTL effect and optionally a trace. [`docs/TOOL.md`](docs/TOOL.md) documents the
format, the four operations, every flag, the result statuses and the code
structure. To run the benchmark drivers on a new set of instances, put the
files in a directory and pass it with `--corpus` / as a path; see
[`bench/README.md`](bench/README.md).

## License

<!-- TODO: decide (MIT recommended) -->
Our code is under the license in [`LICENSE`](LICENSE). AutoHyper
(`external_tools/AutoHyper/`) is GPL-3.0, shipped unchanged with its license.
CATS and CORP (GPL-3.0) and ExplainHyper (MIT) are downloaded by `install.sh`,
not included.
