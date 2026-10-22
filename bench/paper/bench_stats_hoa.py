#!/usr/bin/env python3
"""
bench_stats_hoa -- rebuild the MTSR witness automata the benchmark measured but
did not keep, so their LANGUAGE can be analysed (LTL-definability, the size of
an equivalent LTL formula, equivalence with CATS's causes).

    ~/micromamba/envs/spot/bin/python3 bench/paper/bench_stats_hoa.py
    -> claude_scripts/results/bench_stats/mtsr_hoa.json   {family/instance: {...}}

Only instances the benchmark recorded as OK are rebuilt; the timings come from
the benchmark CSVs, never from here (this run is untimed and parallel).  The
construction is demo_tool/bench/scripts/mtsr_min.run itself, so the automaton is
the one the benchmark measured, and mtsr_min already checks it is
language-equivalent to the unminimized projection.

Resumable: already-rebuilt instances are skipped.
"""
import csv
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
#: main() below is the old 818-instance driver; the paper's numbers come from
#: ltl_rerun_catsrl.py, which only uses work() from this file.
OUT = os.path.join(HERE, "out", "work", "mtsr_hoa.json")
sys.path.insert(0, os.path.join(BENCH, "scripts"))


def work(key, path):
    import mtsr_min
    try:
        inputs, _, _ = mtsr_min.parse_instance(path)
        r = mtsr_min.run(path)
        return key, {"inputs": sorted(inputs), "hoa": r["hoa"],
                     "states": r["states"], "det": r["deterministic"],
                     "dba_exists": r["dba_exists"], "mode": r["mode"],
                     "product_states": r["product_states"]}
    except Exception as e:  # recorded, not fatal
        return key, {"error": f"{type(e).__name__}: {e}"[:200]}


def main():
    res = json.load(open(OUT)) if os.path.exists(OUT) else {}
    jobs = []
    for r in csv.DictReader(open(os.path.join(BENCH, "results", "MTSR_CORP", "mtsr.csv"))):
        if r["mtsr_status"] == "OK":
            jobs.append(("syntcomp/" + r["instance"].removesuffix(".txt"),
                         os.path.join(BENCH, "SYNTCOMP", r["instance"])))
    for r in csv.DictReader(open(os.path.join(BENCH, "results", "MTSR_CATS_sketch", "mtsr.csv"))):
        if r["mtsr_status"] == "OK":
            jobs.append(("nexts/" + r["name"],
                         os.path.join(BENCH, "nexts", "tracemsr", r["name"] + ".txt")))
    jobs = [j for j in jobs if j[0] not in res]
    print(f"{len(jobs)} to build, {len(res)} cached", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with ProcessPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(work, *j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            k, v = f.result()
            res[k] = v
            if i % 50 == 0 or i == len(futs):
                json.dump(res, open(OUT + ".tmp", "w"))
                os.replace(OUT + ".tmp", OUT)
                print(f"  {i}/{len(futs)}", flush=True)
    print("errors:", sum("error" in v for v in res.values()))


if __name__ == "__main__":
    main()
