"""Re-run the analysis behind Table 8 (bench_stats_hoa + mtsr_ltl): build every
MTSR of the benchmark (ip3 + ip4) on 4 cores, then search for an equivalent LTL
formula of size at most --max-size, enumerating formulas for at most --budget
seconds per number of propositions.

    "$SPOT_ENV_PY" bench/paper/ltl_rerun_catsrl.py --max-size 9 --budget 240
    ... --instances NAME ...      only the MTSRs of these instances (the quick test)

Writes <PAPER_OUT>/ltl_rerun/ (default bench/paper/out/ltl_rerun/), never over
the recorded results/stats/mtsr_ltl.csv. The automata are cached in
mtsr_hoa.json there, so a stopped run continues.
"""
import csv, json, os, sys
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
BENCH = paths.BENCH
#: a re-run writes here, never over the shipped results/stats
OUTDIR = os.environ.get("LTL_OUT") or os.path.join(paths.OUT, "ltl_rerun")
OUT = os.path.join(OUTDIR, "mtsr_hoa.json")
sys.path.insert(0, os.path.join(BENCH, "scripts"))
sys.path.insert(0, HERE)
import bench_stats_hoa as H
import mtsr_ltl as M


def build(only=None):
    res = json.load(open(OUT)) if os.path.exists(OUT) else {}
    jobs = []
    for part in ("ip3", "ip4"):
        for r in csv.DictReader(open(os.path.join(paths.SRC, part, "MTSR_CORP", "mtsr.csv"))):
            if r["mtsr_status"] == "OK":
                inst = r["instance"].removesuffix(".txt")
                if only and inst not in only:
                    continue
                jobs.append(("syntcomp/" + inst, os.path.join(BENCH, "CATS_RL_" + part, inst + ".txt")))
    missing = [p for _, p in jobs if not os.path.exists(p)]
    assert not missing, missing[:3]
    jobs = [j for j in jobs if j[0] not in res]
    print(f"{len(jobs)} to build, {len(res)} cached", flush=True)
    os.makedirs(OUTDIR, exist_ok=True)
    with ProcessPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(H.work, *j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            k, v = f.result(); res[k] = v
            if i % 50 == 0 or i == len(futs):
                json.dump(res, open(OUT + ".tmp", "w")); os.replace(OUT + ".tmp", OUT)
                print(f"  {i}/{len(futs)}", flush=True)
    print("errors:", sum("error" in v for v in res.values()), flush=True)
    return {k for k, _ in jobs} | set(res)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", nargs="+")
    a, rest = ap.parse_known_args()
    csv_out = os.path.join(OUTDIR, "mtsr_ltl.csv")
    before = set(json.load(open(OUT))) if os.path.exists(OUT) else set()
    keys = build(set(a.instances) if a.instances else None)
    if keys <= before and os.path.exists(csv_out):
        print(f"already done: {csv_out}")    # a re-run continues where it stopped
        sys.exit(0)
    M.DIR = OUTDIR
    sys.argv = sys.argv[:1] + rest
    M.main()
