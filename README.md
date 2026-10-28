# TraceMSR — artifact

Artifact of the TACAS 2027 tool paper

> **TraceMSR: A Tool for Synthesizing Sufficient Reasons for Reactive Systems**
> Nadav Rutman Moshe and Hadar Frenkel (EasyChair submission #5598)

The tool is called TraceMSR in the paper; this repository is named
`MSR_Finder`. Both refer to the same code.

* **Artifact (permanent, DOI):** https://doi.org/10.5281/zenodo.23589523
* **Source repository:** https://github.com/NadavRutmanMoshe/MSR_Finder
* **The paper as submitted:** [`paper/TACAS27_paper_5598.pdf`](paper/TACAS27_paper_5598.pdf)

## Quickstart

### Requirements

- Linux, amd64 or arm64 (the TACAS 2027 VM works).
- About 7 GB of free disk and 8 GB of RAM.
- Internet access during installation only.

### The TACAS 2027 VM

We tested the artifact on the TACAS 2027 VM, in VirtualBox. To set it up:

1. Install VirtualBox (https://www.virtualbox.org/wiki/Downloads), choosing the installer for your system.
2. Download the VM from https://zenodo.org/records/23472158. It comes in two versions:
   - `TACAS27-AEC-amd64.ova` for Intel/AMD 64-bit machines (most Windows and Linux PCs, and older Intel Macs),
   - `TACAS27-AEC-arm64.ova` for ARM 64-bit machines (Macs with an Apple chip: M1, M2, ...).
3. In VirtualBox: **File → Import Appliance**, choose the `.ova`, and before importing set **CPU** to 4 and **RAM** to 8192 MB (you can use more RAM or fewer CPUs depending on your machine).
4. Start the VM and log in: user `tacas`, password `tacas`.
5. Get the artifact into the VM. In a terminal in the VM, download it from Zenodo:

   ```
   curl -L -o MSRfinder-artifact.zip "https://zenodo.org/records/23589523/files/MSRfinder-artifact.zip?download=1"
   ```

   or download it on your machine and then copy it in through a VirtualBox shared folder.

### Setup (about 10 minutes; about 45 minutes on arm64)

Unzip the artifact (or extract it with the file manager), open a terminal, and go into its folder (the one that contains `install.sh`):

```
unzip MSRfinder-artifact.zip
cd MSRfinder-artifact
```

Then run these two commands, one after the other:

```
bash install.sh
source env.sh
```

1. `bash install.sh` installs everything the tool and the benchmark need, under your home directory and without root. Every version is pinned, and everything it does is logged to `install.log`.
2. `source env.sh` sets up this terminal for the tool. New terminals are set up automatically.

**Or with Docker.** In a terminal on your machine, in the artifact folder, run these two commands, one after the other:

```
docker build -t tracemsr .
docker run --rm -it -v "$PWD/shared:/shared" tracemsr
```

1. `docker build` creates the image: it runs `install.sh` and then the smoke test inside it (about 10 minutes).
2. `docker run` opens a terminal inside the container, in the artifact folder, ready to use. Every command below works there as written.

Everything inside the container is deleted when you leave it (`exit`), except the folder `/shared`: it is the folder `shared/` next to the artifact on your machine. We use this folder to store the results on your machine for easy access: inside the container, the commands below save their results there automatically.

### Smoke test (about 1 minute)

In the same terminal (or in the container), run:

```
./smoke_test.sh
```

It checks three things, and the last line should be `SMOKE TEST PASSED`:

1. **The tool:** runs each of the tool's operations on an example and compares the result with the known answer.
2. **The paper's numbers:** regenerates all 12 tables and figures of the evaluation from our recorded results, and checks them number by number against the submitted paper. It does the same for the 37 numbers quoted in the text.
3. **The other tools:** runs one benchmark instance through every tool we compare against (AutoHyper, HyperQB, ExplainHyper, CATS, CORP) and checks that each gives the recorded answer.

### Quick test (about 10 minutes)

Between the smoke test and the long runs: every dataset of the paper (see [Reproducing the paper](#reproducing-the-paper)) re-run on a few instances, and every table and figure built from it.

```
bench/run.sh --quick
```

It runs every tool on 16 instances of the benchmark (|IP| = 1 to 4), 10 of the 100 CATS sketching instances, the hand-crafted instances of CATS and CORP (except one, `full_4_GF`, which takes the most time and memory: about 1.5 minutes and 6 GB of RAM), and the LTL search on the 16 MTSRs. It took 8 minutes on our laptop. Then compare with our results by running these two commands, one after the other:

```
python3 bench/scripts/compare_runs.py
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun
```

1. `compare_runs.py` compares the answers of every tool on every instance with ours (the reason found, the automaton size, the verdict). It marks each as `same`, `timeout` (one side ran out of time, which depends on the machine's speed) or `DIFFERENT`, and ends with `nothing DIFFERENT` when all agree. It also shows how much faster or slower your machine is than ours.
2. `make_tables.py --src rerun` builds every table and figure from your results and compares it with the same table built from our recorded results, over the same instances (Table 4, whose instances all ran, is compared with the paper instead). It prints `SAME` for each table whose numbers agree, or `SAME except times` when only the running times differ.

The results go to `bench/rerun/` (in Docker: `shared/rerun/` on your machine); our recorded results are never changed. The quick test writes to the same folder as the runs below, so the instances it ran are not run again there.

## Available badge

The artifact is permanently archived on Zenodo: https://doi.org/10.5281/zenodo.23589523.
The source code is also on GitHub: https://github.com/NadavRutmanMoshe/MSR_Finder.

## Functional badge

### What's inside

- `scripts/`: the tool itself (Python). The main script is `tracemsr.py`.
- `examples/`: example inputs, including the running example of the paper (`biased_arbiter.txt`).
- `external_tools/`: the HyperLTL model checkers the tool calls, AutoHyper (included) and HyperQB (built by `install.sh`).
- `bench/`: the benchmark of Section 5 (it has its own [README](bench/README.md)):
  - `CATS_RL_ip3/`, `CATS_RL_ip4/`: the 987 instances (823 with |IP| ≤ 3, 164 with |IP| = 4)
  - `results/`: every result we recorded, one CSV per tool
  - `paper/`: the scripts that regenerate every table and figure of Section 5 and Appendix D
  - `scripts/`: the scripts that re-run the tools on the benchmark
- `docs/`: documentation: how to use the tool (input format, operations, flags, results) and notes on how the benchmark was built.
- `paper/`: the paper as submitted.
- `install.sh`, `smoke_test.sh`, `Dockerfile`: installation and the smoke test (see Quickstart).

### Tested platforms and resources

We tested the artifact on:

- **Our laptop** (Ubuntu 22.04 under WSL2, Intel i7-8565U, 4 cores, 16 GB RAM): every result in the paper was measured here.
- **A clean Ubuntu 26.04 container** (the `Dockerfile`), amd64: install and smoke test.
- **The same container on arm64** (emulated): install and smoke test.
- **The TACAS 2027 VM**, amd64, on a clean laptop: install, smoke test and most of the subset re-run (94 of 100 instances).
- **The TACAS 2027 VM**, arm64, on a Mac with an Apple M3 Max (36 GB RAM): install and smoke test.

How long each part takes, and what it needs:

- Smoke test: about 1 minute.
- Quick test: about 10 minutes.
- Re-running the datasets of the paper: from 1 minute to 42 hours each (see [Reproducing the paper](#reproducing-the-paper)); all of them, about 45 hours, and 16 GB of RAM (a few instances use up to 6 GB). With the subset of the benchmark, about 4.5 hours.

The tools run one at a time, on one instance at a time, so more cores don't make it faster. The times are from our laptop; expect more in a VM.

### Reproducing the paper

Every table and figure of the evaluation (Section 5 and Appendix D) is made from one of six datasets. For each dataset, below, there is one command that re-runs the tools and regenerates the data, and then one command per table or figure that builds it from your data. Run as many datasets as you like, in any order.

| Dataset | What is run | Time (our laptop) | Tables and figures |
|---|---|---|---|
| `main` | MSR (AutoHyper, HyperQB), ExplainHyper, MTSR and CORP on the 987 benchmark instances | 4 h (subset of 100), 42 h (all) | Tables 1, 2, 3, 7; Figs. 6, 9, 10 |
| `cats-check` | MTSR and CATS checking a candidate cause, on the 823 instances with \|IP\| ≤ 3 | 12 min (subset of 83), 1.8 h (all) | Fig. 7 |
| `cats-sketch` | MTSR and CATS sketching, on CATS's 100 instances | 20 min | Tables 5, 7 |
| `cats-handcrafted` | MTSR and CATS on CATS's 11 hand-crafted instances | 1 min | Table 4 |
| `corp-arbiters` | MTSR and CORP on the 15 arbiters of CORP's paper | 3 min | Table 6 |
| `ltl` | the search for an LTL formula equivalent to each MTSR | 6 min (4 cores) | Table 8 |

All commands are run from the artifact folder. The results go to `bench/rerun/` (in Docker: `shared/rerun/` on your machine); our recorded results, in `bench/results/`, are never changed. A run can be stopped at any point with Ctrl-C: running the same command again continues where it stopped. Keep the machine from going to sleep while it runs.

How to read the output of `make_tables.py --src rerun`:

- **A dataset you re-ran in full:** each table and figure is compared with the submitted paper, and prints `SAME` if its numbers are exactly those in the paper. The new files go to `bench/paper/out/tables/` and `bench/paper/out/figures/` (in Docker: `shared/tables/`).
- **A dataset you re-ran in part** (the subset, the quick test, or a run you stopped): each table and figure is built over the instances you re-ran, once from our results (`bench/paper/out/recorded/`) and once from yours (`bench/paper/out/yours/`), and prints `SAME` if the two agree.

Running times differ from machine to machine, so a table that prints times shows `SAME except times` when everything else agrees. The counts and sizes should not differ, except for instances that finish close to the timeout; where they do, it shows `DIFFERS` and prints the rows that differ. To compare the answers instance by instance, run `python3 bench/scripts/compare_runs.py` (see [Quick test](#quick-test-about-10-minutes)).

#### Dataset `main`: Tables 1, 2, 3 and Figs. 6, 9, 10

To regenerate the data of Tables 1, 2, 3 and Figs. 6, 9, 10, run every tool on the benchmark, the subset of 100 instances (about 4 hours):

```
bench/run.sh --dataset main
```

or all 987 instances (about 42 hours, and 16 GB of RAM: a few instances use up to 6 GB):

```
bench/run.sh --dataset main --full
```

The subset is every 10th instance: 83 of the 823 instances with |IP| ≤ 3, and 17 of the 164 with |IP| = 4. To see what will run, and how long it took on our laptop, add `--plan`. To run only the instances with |IP| ≤ 3 (about 18 hours in full), add `--corpus ip3`.

To generate from this data Table 1 (MSR and MTSR by number of inputs), run:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_main
```

Table 2 and Fig. 10 (AutoHyper vs. HyperQB):

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_msr_backends
```

Table 3 (MSR vs. ExplainHyper):

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_msreh
```

Fig. 6 (running time vs. system size):

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun fig_data_msr_mtsr_size_v1
```

Fig. 9 (MSR and ExplainHyper vs. system size):

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun fig_data_msr_size
```

After a full run, `"$SPOT_ENV_PY" bench/paper/check_text.py --src rerun` also checks the 37 numbers quoted in the text of the paper.

#### Dataset `cats-check`: Fig. 7

To regenerate the data of Fig. 7, run MTSR and CATS on the same candidate cause, on the subset (83 instances, about 12 minutes):

```
bench/run.sh --dataset cats-check
```

or on all 823 instances with |IP| ≤ 3 (about 1.8 hours):

```
bench/run.sh --dataset cats-check --full
```

To generate from this data Fig. 7 (MTSR vs. CATS cause checking), run:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun fig_data_cats_check_time
```

#### Dataset `cats-sketch`: Tables 5 and 7

To regenerate the data of Table 5, run MTSR and CATS sketching on CATS's 100 instances (about 20 minutes):

```
bench/run.sh --dataset cats-sketch
```

To generate from this data Table 5 (MTSR vs. CATS sketching), run:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_catssketch
```

#### Table 7: summary of the comparisons

Table 7 sums up three comparisons, one per row: MSR vs. ExplainHyper and MTSR vs. CORP come from dataset `main`, and MTSR vs. CATS sketching from dataset `cats-sketch`. To generate it from your data, run:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_data_resultsizes
```

If you re-ran only one of the two datasets, the rows of the other come from our results, and the output says so.

#### Dataset `cats-handcrafted`: Table 4

To regenerate the data of Table 4, run MTSR and CATS on CATS's 11 hand-crafted instances (about 1 minute):

```
bench/run.sh --dataset cats-handcrafted
```

To generate from this data Table 4, run:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_catshand
```

#### Dataset `corp-arbiters`: Table 6

To regenerate the data of Table 6, run MTSR and CORP on the 15 arbiters of CORP's paper (about 3 minutes; one of them, `full_4_GF`, uses 6 GB of RAM):

```
bench/run.sh --dataset corp-arbiters
```

To generate from this data Table 6, run:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_corpcurated
```

On `full_4_GF`, CORP's answer varies from run to run: an automaton of 284 states (as in the paper) or 286. This is CORP's own behavior; the quick test leaves this instance out.

#### Dataset `ltl`: Table 8

To regenerate the data of Table 8, build the MTSR of every benchmark instance and search for the smallest equivalent LTL formula, up to size 9 (about 6 minutes, on 4 cores):

```
bench/run.sh --dataset ltl
```

To generate from this data Table 8, run:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun tab_data_ltl
```

The search stops after 4 minutes per number of inputs, so on a slower machine it may reach a smaller formula size; `compare_runs.py` then marks the affected automata as `timeout`.

#### Figs. 4 and 5: the running example

Figs. 4 and 5 (the example session of Section 4.5) come from running the tool on the paper's running example, with operations 1 (MSR) and 4 (MTSR):

```
python3 scripts/tracemsr.py examples/biased_arbiter.txt --run 1,4
```

The picture of the MTSR (Fig. 5) is saved next to the input, as `examples/biased_arbiter_mtsr.png`. If you use Docker and want the PNG of the MTSR saved on your machine, also run:

```
cp examples/*_mtsr.png /shared/
```

#### All datasets at once

Without `--dataset`, `bench/run.sh` runs all six datasets one after the other: `bench/run.sh` for the subset (about 4.5 hours), or `bench/run.sh --full` for everything (about 45 hours). Then `"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun` builds all the tables and figures, and `python3 bench/scripts/compare_runs.py` compares the answers instance by instance.

How the benchmark itself was generated is described in `bench/README.md`.

## Reusable badge

### Using the tool on your own inputs

```
python3 scripts/tracemsr.py examples/example_1.txt
```

This opens a menu of the tool's four operations:

1. MSR with the given trace: the minimal sufficient reason for the effect on the trace in the file.
2. MSR for the effect: finds a trace automatically.
3. Model checking of the effect: a counterexample, and why.
4. MTSR: the minimal temporal sufficient reason, as an automaton (also saved as a PNG next to the input).

Move with the arrow keys, select with ENTER or SPACE, then choose **Start**. To skip the menu, give the operations directly. For example, all four:

```
python3 scripts/tracemsr.py examples/example_1.txt --run all
```

or the MSR with HyperQB instead of AutoHyper:

```
python3 scripts/tracemsr.py examples/example_1.txt --run 1 --hyperQB
```

In Docker, to get the PNGs on your machine: `cp examples/*_mtsr.png /shared/`.

An input is one text file: the system (a HOA automaton), which propositions are inputs, the effect (an LTL formula), and optionally a trace. `docs/TOOL.md` documents the format, every operation and flag, and what each result means. The files in `examples/` are a good starting point.

### Comparing the tools on your own instances

Put your input files in a folder and run:

```
bench/run.sh --corpus my_folder
```

This runs the same comparison as in the paper on every file in the folder: MSR (with AutoHyper and HyperQB), ExplainHyper, MTSR, CORP and CATS, with 120 seconds per tool (change it with `--timeout`). Like the paper's benchmark, it first skips instances where the trace is not a run of the system or the effect does not hold on it. The results go to `bench/rerun/my_folder/`, one CSV per tool, and at the end it prints how many instances each tool answered, and how. In Docker, put the folder inside `shared/` on your machine and run `bench/run.sh --corpus /shared/my_folder`.

### Installing only the tool

If you only want TraceMSR and not the tools we compare against, run:

```
bash install.sh --tool-only
```

This installs spot, the .NET runtime for AutoHyper, and HyperQB, about half of the full installation. The top of `install.sh` lists every dependency with its exact version, for installing them another way.

## License

Our code is under the MIT license (see [`LICENSE`](LICENSE)).

The artifact also uses tools by others, each under its own license:

- AutoHyper (GPL-3.0) is included in `external_tools/AutoHyper/`, unchanged, with its license file.
- `install.sh` downloads the others; they are not part of the artifact:
  - HyperQB: MIT
  - CORP: GPL-3.0
  - ExplainHyper: MIT
  - CATS: its repository states no license
  - spot: GPL-3.0

