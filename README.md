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
- **The TACAS 2027 VM** on a clean laptop: install, smoke test and the subset re-run. <!-- TODO laptop test -->

How long each part takes, and what it needs:

- Smoke test: about 1 minute.
- All tables and figures from our recorded results: about 1 minute.
- Re-running a subset of the benchmark (100 of the 987 instances): about 5 hours.
- Re-running the full benchmark: about 45 hours, and 16 GB of RAM (a few instances use up to 6 GB).

The tools run one at a time, on one instance at a time, so more cores don't make it faster. The times are from our laptop; expect more in a VM.

### Reproducing the paper

There are three levels, from quick to complete:

1. All tables and figures from our recorded results (1 minute).
2. Re-running a subset of the benchmark (about 5 hours).
3. Re-running the full benchmark (about 45 hours).

All commands are run from the artifact folder.

#### 1. All tables and figures from our recorded results

```
"$SPOT_ENV_PY" bench/paper/make_tables.py
```

This regenerates every table and figure of the evaluation (Section 5 and Appendix D) from our recorded results in `bench/results/`. For each one it prints `SAME` if its numbers are exactly those in the submitted paper. The new tables and figures go to `bench/paper/out/` (in Docker: `shared/tables/` on your machine).

To regenerate a single table, add its name. For example, Table 1:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py tab_main
```

prints

```
  tab_main                     SAME     Table 1: MSR and MTSR per |IP|
```

and writes the table to `bench/paper/out/tables/tab_main.tex`. The names of all the tables and figures:

- Table 1, MSR and MTSR by number of inputs: `tab_main`
- Table 2 and Fig. 10, AutoHyper vs. HyperQB: `tab_msr_backends`
- Table 3, MSR vs. ExplainHyper: `tab_msreh`
- Table 4, CATS's hand-crafted instances: `tab_catshand`
- Table 5, MTSR vs. CATS sketching: `tab_catssketch`
- Table 6, MTSR vs. CORP on CORP's arbiters: `tab_corpcurated`
- Table 7, summary of the comparisons: `tab_data_resultsizes`
- Table 8, which MTSRs can be written as a small LTL formula: `tab_data_ltl`
- Fig. 6, running time vs. system size: `fig_data_msr_mtsr_size_v1`
- Fig. 7, MTSR vs. CATS cause checking: `fig_data_cats_check_time`
- Fig. 9, MSR and ExplainHyper vs. system size: `fig_data_msr_size`

Optionally, `"$SPOT_ENV_PY" bench/paper/check_text.py` checks the same way the 37 numbers quoted in the text of the paper.

Figs. 4 and 5 (the example session of Section 4.5) come from running the tool on the paper's running example, with operations 1 (MSR) and 4 (MTSR):

```
python3 scripts/tracemsr.py examples/biased_arbiter.txt --run 1,4
```

The picture of the MTSR (Fig. 5) is saved next to the input, as `examples/biased_arbiter_mtsr.png`. If you use Docker and want the PNG of the MTSR saved on your machine, also run:

```
cp examples/*_mtsr.png /shared/
```

#### 2. Re-running a subset of the benchmark (about 5 hours)

The subset is every 10th instance of the benchmark: 83 of the 823 instances with |IP| ≤ 3, and 17 of the 164 with |IP| = 4. To see what will run, and how long it took on our laptop:

```
bench/run.sh --plan
```

Then start it:

```
bench/run.sh
```

It runs every tool on each instance in turn, as we did for the paper, with the paper's timeouts (120 s, and 300 s for |IP| = 4). Then it runs the CATS sketching comparison (Table 5) and the hand-crafted instances of Tables 4 and 6. It took 4.5 hours on our laptop; expect more in a VM. Keep the machine from going to sleep while it runs. You can stop it at any point with Ctrl-C: running the same command again continues where it stopped.

The results go to `bench/rerun/` (in Docker: `shared/rerun/` on your machine); our recorded results are never changed. When it finishes, compare the two by running these two commands, one after the other:

```
python3 bench/scripts/compare_runs.py
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun --subset
```

1. `compare_runs.py` compares the answers of every tool on every instance with ours (the reason found, the automaton size, the verdict). It marks each as `same`, `timeout` (one side ran out of time, which depends on the machine's speed) or `DIFFERENT`. It also shows how much faster or slower your machine is than ours.
2. `make_tables.py --src rerun --subset` builds every table twice over the instances you re-ran, once from our results and once from yours, so you can compare them side by side.

Running times differ from machine to machine. The answers should not, except for instances that finish close to the timeout.

#### 3. Re-running the full benchmark (about 45 hours)

```
bench/run.sh --full
```

This is the same as level 2, on all 987 instances. It took 45 hours on our laptop and needs 16 GB of RAM. It writes to the same folder as level 2, so the instances you already re-ran there are not run again. As in level 2, Ctrl-C stops it, and the same command continues where it stopped.

When it finishes, build the tables and figures from your results by running these two commands, one after the other:

```
"$SPOT_ENV_PY" bench/paper/make_tables.py --src rerun
"$SPOT_ENV_PY" bench/paper/check_text.py --src rerun
```

They work as in level 1, but on your results instead of ours. Tables that report running times will show `DIFFERS`, since times depend on the machine. The counts and sizes should match the paper, except for instances that finish close to the timeout. `compare_runs.py` (level 2) also works here.

You can also run only part of the benchmark:

- `bench/run.sh --full --corpus ip3`: only the instances with |IP| ≤ 3.
- `bench/run.sh --full --only msr,mtsr-corp`: only some of the tools. The groups are:
  - `msr`: MSR with AutoHyper and with HyperQB (Tables 1–3, Figs. 6, 9, 10)
  - `explainhyper`: ExplainHyper (Table 3, Fig. 9)
  - `mtsr-corp`: MTSR and CORP (Table 1, Fig. 6)
  - `cats-check`: MTSR and CATS checking a candidate cause (Fig. 7)
  - `cats-sketch`: MTSR and CATS sketching (Table 5)
  - `handcrafted`: the hand-crafted instances of CATS (Table 4) and CORP (Table 6)

  Table 7 summarizes all of them.
- `bench/run.sh --instances arbiter2__e09 TorcsGearing__e02`: chosen instances.

How the benchmark itself was generated, and the LTL search behind Table 8, are described in `bench/README.md`.

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

