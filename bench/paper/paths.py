"""Where the paper generators read and write.

Every generator in this folder takes its locations from here, so the layout is
stated once. make_tables.py sets the environment variables below; run by hand,
each generator falls back to the shipped results and writes into paper/out/.

    BENCH_SRC     the results to read: bench/results (the recorded runs) or
                  a re-run directory with the same layout (ip3/, ip4/, ...)
    BENCH_RESULTS a merged VIEW of BENCH_SRC built by make_tables.py
                  (ip3 only, or ip3+ip4), in the layout the generators expect
    BENCH_STATS   derived per-instance data (extra.csv, mtsr_ltl.csv, ...)
    IP4_RESULTS   BENCH_SRC/ip4, for the |IP| = 4 rows
    PAPER_OUT     where tables/ and figures/ are written
"""
import os

PAPER_DIR = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(PAPER_DIR)
REPO = os.path.dirname(BENCH)
TOOL_SCRIPTS = os.path.join(REPO, "scripts")
BENCH_SCRIPTS = os.path.join(BENCH, "scripts")
TOOLS = os.path.join(BENCH, "tools")

SRC = os.environ.get("BENCH_SRC") or os.path.join(BENCH, "results")
VIEW = os.environ.get("BENCH_RESULTS") or os.path.join(PAPER_DIR, "out", "work", "view_ip3")
STATS = os.environ.get("BENCH_STATS") or os.path.join(PAPER_DIR, "out", "work", "stats")
SHIPPED_STATS = os.path.join(BENCH, "results", "stats")
CURATED = os.path.join(SRC, "curated")
if not os.path.isdir(CURATED):          # a re-run without the curated tables
    CURATED = os.path.join(BENCH, "results", "curated")

OUT = os.environ.get("PAPER_OUT") or os.path.join(PAPER_DIR, "out")
TABLES = os.path.join(OUT, "tables")
FIGURES = os.path.join(OUT, "figures")
EXPECTED = os.path.join(PAPER_DIR, "expected")

CORPUS_IP3 = os.path.join(BENCH, "CATS_RL_ip3")
CORPUS_IP4 = os.path.join(BENCH, "CATS_RL_ip4")
CORP_ARBITERS = os.path.join(BENCH, "corp_arbiters")

SPOT_PY = os.environ.get("SPOT_ENV_PY") or os.path.expanduser(
    "~/micromamba/envs/spot/bin/python3")
SPOT_BIN = os.path.dirname(SPOT_PY)

for _d in (TABLES, FIGURES):
    os.makedirs(_d, exist_ok=True)
