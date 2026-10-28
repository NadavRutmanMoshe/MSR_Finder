#!/usr/bin/env python3
r"""
make_tables -- regenerate every table and figure of the paper that comes from
the benchmark, and compare each with the version in the submitted paper.

    python3 make_tables.py                    # all 12, from the recorded results
    python3 make_tables.py --src rerun        # each from its dataset of your re-run
    python3 make_tables.py --src rerun tab_main tab_msreh   # only these
    python3 make_tables.py --list             # what is generated, from what

Run it with the spot environment's interpreter (it needs spot and matplotlib):
~/micromamba/envs/spot/bin/python3 make_tables.py. Output goes to paper/out/
(--out to change): tables/*.tex, figures/*.pdf + figures/*.tex. Nothing in the
repository is overwritten.

THE CHECK
---------
For every generated .tex, each table row is reduced to the tuple of numbers in
it and compared with the same file of the submitted paper (paper/expected/).
Layout, colour and caption wording are ignored on purpose; the paper's floats
were re-typeset by hand on Overleaf after they were generated, so only the
numbers are comparable. A figure is compared by the numbers in its caption; its
PDF is in out/figures/ next to the submitted one in expected/figures/ to look at.

    SAME      every row's numbers are in the paper's table, and vice versa
    SAME except times
              only running times differ (the floats print times, and only
              times, with exactly two decimals); expected from a re-run
    DIFFERS   the rows that differ are printed, paper first

From a re-run (--src), each float is made from its dataset (DATA and USES
below; bench/run.sh --dataset re-runs one), with our recorded rows for the
datasets you did not re-run. A dataset re-run on all its instances is compared
with the submitted paper; one re-run on part of them (the subset, the quick
test, a stopped run) is compared with our recorded rows over the same
instances, built the same way (out/recorded/ vs out/yours/).
DIFFERS is expected wherever a time is printed: the rows are compared exactly,
and wall-clock times depend on the machine. Read the counts (instances, solved,
sizes); scripts/compare_runs.py compares a re-run with the recorded rows
instance by instance.

WHICH DATA
----------
The paper's benchmark has two corpora: CATS_RL_ip3 (823 instances, |IP| <= 3,
120 s timeout) and CATS_RL_ip4 (164 instances, |IP| = 4, 300 s). Some floats are
over |IP| <= 3 only (the CATS comparisons were not run on |IP| = 4, and two
figures say "with |IP| <= 3" in their captions), the others over both. Two
views of --src are built under out/work/ to serve both:

    view_ip3   ip3/ results + the CATS-check agreement join + the sketch results
    view_all   ip3/ and ip4/ concatenated (header once)
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
# --src rerun = your re-run: bench/rerun, or $RERUN_OUT (the Docker image sets
# it, and PAPER_OUT, to folders under /shared, which is on the host)
RERUN = os.environ.get("RERUN_OUT") or os.path.join(BENCH, "rerun")
PY = sys.executable


def src_ip4(src):
    return os.path.join(src, "ip4")


# name -> (files, view, what it is, command). `view` is ip3 / all / none.
def floats(src):
    pt = lambda fn, label: ["-c",
        "import re, random, paper_tables as P; random.seed(1); "
        "open(P.os.path.join(P.TABLES, %r), 'w').write(re.sub(r'\\\\num\\{([^{}]*)\\}', r'\\1', "
        "P.BANNER.format(%r) + P.%s()))" % (fn + ".tex", label, fn)]
    bf = lambda fn: ["-c", "import random; random.seed(1); import bench_figs as B; B.%s()" % fn]
    return {
        "tab_main": (["tables/tab_main.tex"], "ip3+ip4", "Table 1: MSR and MTSR per |IP|",
                     pt("tab_main", "ip3/ + ip4/ MSR, MTSR_CORP/mtsr.csv")),
        "tab_msr_backends": (["tables/tab_msr_backends.tex", "figures/fig_data_msr_backends_time.tex",
                              "figures/data_msr_backends_time.pdf"], "ip3+ip4",
                             "Table 2 + Fig. 10: AutoHyper vs HyperQB", ["tab_msr_backends.py"]),
        "tab_msreh": (["tables/tab_msreh.tex"], "all", "Table 3: MSR vs ExplainHyper [11]",
                      pt("tab_msreh", "ip3/ + ip4/ MSR/")),
        "tab_catshand": (["tables/tab_catshand.tex"], "none", "Table 4: CATS's hand-crafted instances",
                         ["cats_handcrafted.py", "--table"]),
        "fig_data_cats_check_time": (["figures/fig_data_cats_check_time.tex", "figures/data_cats_check_time.pdf"],
                                     "ip3", "Fig. 7: MTSR vs CATS cause checking", bf("fig_cats_check_time")),
        "tab_catssketch": (["tables/tab_catssketch.tex"], "ip3", "Table 5: MTSR vs CATS sketch",
                           pt("tab_catssketch", "MTSR_CATS_sketch/")),
        "tab_corpcurated": (["tables/tab_corpcurated.tex"], "none", "Table 6: MTSR vs CORP on CORP's arbiters",
                            ["corp_curated.py", "--table"]),
        "tab_data_resultsizes": (["tables/tab_data_resultsizes.tex"], "all", "Table 7: summary of the comparisons",
                                 bf("tab_resultsizes")),
        "fig_data_msr_mtsr_size_v1": (["figures/fig_data_msr_mtsr_size_v1.tex", "figures/data_msr_mtsr_size_v1.pdf"],
                                      "ip3", "Fig. 6: running time vs system size", bf("fig_msr_mtsr_size_variants")),
        "fig_data_msr_size": (["figures/fig_data_msr_size.tex", "figures/data_msr_size.pdf"], "ip3",
                              "Fig. 9: MSR and ExplainHyper vs system size", bf("fig_msr_size")),
        "tab_data_ltl": (["tables/tab_data_ltl.tex"], "none", "Table 8: LTL-expressibility of the MTSR",
                         ["tab_ltl.py"]),
    }


# --------------------------------------------------------------------------- #
def concat(parts, rel, dst):
    rows, header = [], None
    for part in parts:
        p = os.path.join(part, rel)
        if not os.path.exists(p):
            continue
        with open(p, newline="") as f:
            r = csv.DictReader(f)
            header = header or r.fieldnames
            rows += list(r)
    if header is None:
        return 0
    os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
    with open(os.path.join(dst, rel), "w", newline="") as f:
        w = csv.DictWriter(f, header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def build_views(src, work, env):
    """view_ip3 through plan2_stats.py (which also writes extra.csv and
    plan2_numbers.json, the numbers Section 5's prose quotes), then view_all."""
    v3, va, stats = (os.path.join(work, "view_ip3"), os.path.join(work, "view_all"),
                     os.path.join(work, "stats"))
    for d in (v3, va):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(stats, exist_ok=True)
    e = dict(env, BENCH_RESULTS=v3)
    p = subprocess.run([PY, os.path.join(HERE, "plan2_stats.py"), "--quiet"], env=e,
                       cwd=HERE, capture_output=True, text=True)
    if p.returncode:
        sys.exit("building the |IP|<=3 view failed:\n" + p.stderr[-2000:])
    os.makedirs(va, exist_ok=True)
    for rel in ("MSR/msr_autohyper.csv", "MSR/msr_hyperqb.csv", "MSR/explainhyper.csv",
                "MTSR_CORP/mtsr.csv", "MTSR_CORP/corp.csv"):
        concat([os.path.join(src, "ip3"), src_ip4(src)], rel, va)
    for d in ("MTSR_CATS_check", "MTSR_CATS_sketch"):
        shutil.copytree(os.path.join(v3, d), os.path.join(va, d))
    shutil.copy(os.path.join(BENCH, "results", "gate.csv"), os.path.join(va, "gate.csv"))
    return v3, va, stats


# --------------------------------------------------------------------------- #
_CMD = re.compile(r"\\(multicolumn|multirow|cmidrule|hspace|vspace|setlength|arraystretch)"
                  r"(\{[^}]*\}){0,3}|\\(textwidth|linewidth|tabcolsep)")


def strip_comments(s):
    return re.sub(r"(?<!\\)%.*", "", s)


def numbers(s):
    return tuple(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", _CMD.sub("", s)))


def table_rows(path):
    s = strip_comments(open(path).read())
    m = re.search(r"\\begin\{tabular\}(.*)\\end\{tabular\}", s, re.S)
    s = m.group(1) if m else s
    s = re.sub(r"\\cmidrule(\([^)]*\))?\{[^}]*\}", "", s)
    out = []
    for line in s.split("\\\\"):
        if "&" in line:
            t = numbers(line.split(r"\midrule")[-1].split(r"\toprule")[-1])
            if t:
                out.append(t)
    return out


def caption_numbers(path):
    s = strip_comments(open(path).read())
    m = re.search(r"\\caption\{(.*)\}\s*\\label", s, re.S)
    return [numbers(m.group(1))] if m else []


def untimed(gen, exp):
    """Equal once the running times (the numbers with exactly two decimals:
    every float prints times that way, and nothing else) are left out."""
    get = table_rows if os.path.basename(gen).startswith("tab_") else caption_numbers
    drop = lambda rows: sorted(tuple(x for x in r if not re.fullmatch(r"\d+\.\d\d", x)) for r in rows)
    return drop(get(gen)) == drop(get(exp))


def compare(gen, exp, labels=("paper", "here ")):
    if not os.path.exists(exp):
        return "NO REFERENCE", []
    get = table_rows if os.path.basename(gen).startswith("tab_") else caption_numbers
    a, b = get(exp), get(gen)
    if sorted(a) == sorted(b):
        return "SAME", []
    diff = [(labels[0], r) for r in a if r not in b] + [(labels[1], r) for r in b if r not in a]
    return "DIFFERS", diff


# --------------------------------------------------------------------------- #
def generate(src, out, names, quiet=False, extra_env=None):
    """Regenerate `names` from `src` into `out`. Returns {name: ok}."""
    F = floats(src)
    work = os.path.join(out, "work")
    env = dict(os.environ, BENCH_SRC=src, PAPER_OUT=out, BENCH_STATS=os.path.join(work, "stats"),
               MPLBACKEND="Agg", **(extra_env or {}))
    if os.path.isdir(src_ip4(src)):
        env["IP4_RESULTS"] = src_ip4(src)
    elif not quiet:
        print(f"note: {src_ip4(src)} does not exist; the |IP| = 4 rows are left out")
    v3, va, _ = build_views(src, work, env)
    ok = {}
    for n in names:
        files, view, what, cmd = F[n]
        e = dict(env, BENCH_RESULTS=va if view == "all" else v3)
        if view != "ip3+ip4":
            e.pop("IP4_RESULTS", None)
        p = subprocess.run([PY] + cmd, cwd=HERE, env=e, capture_output=True, text=True)
        ok[n] = p.returncode == 0
        if not ok[n]:
            print(f"  {n:28s} FAILED\n" + "\n".join("      " + l for l in p.stderr.strip().splitlines()[-8:]))
    return ok


def report(names, out, ref, labels, F, ok):
    worst = 0
    for n in names:
        files, view, what, _ = F[n]
        if not all(o.get(n) for o in ok):
            print(f"  {n:28s} FAILED   {what}")
            worst = 2
            continue
        verdicts = [(f,) + compare(os.path.join(out, f), os.path.join(ref, f), labels)
                    for f in files if f.endswith(".tex") and os.path.exists(os.path.join(out, f))]
        if not verdicts:
            worst = 2
            continue
        times = [v == "SAME" or untimed(os.path.join(out, f), os.path.join(ref, f))
                 for f, v, _ in verdicts]
        status = ("SAME" if all(v == "SAME" for _, v, _ in verdicts) else
                  "SAME except times" if all(times) else "DIFFERS")
        worst = max(worst, status != "SAME")
        print(f"  {n:28s} {status:17s} {what}")
        for (f, v, diff), t in zip(verdicts, times):
            if not t:
                print(f"      {f}: {v}")
                for who, r in diff[:12]:
                    print(f"        {who}: {' '.join(r)}")
    return worst


# --------------------------------------------------------------------------- #
# DATASETS: what each float is made from (bench/run.sh --dataset NAME re-runs one).
# Every float uses one dataset; Table 7 sums up three comparisons, two from
# `main` and one from `cats-sketch`.  (part, file, key column); part "" = top level.
MAIN_CSVS = ("MSR/msr_autohyper.csv", "MSR/msr_hyperqb.csv", "MSR/explainhyper.csv",
             "MTSR_CORP/mtsr.csv", "MTSR_CORP/corp.csv")
DATA = {
    "main": [(p, f, "instance") for p in ("ip3", "ip4") for f in MAIN_CSVS],
    "cats-check": [("ip3", "MTSR_CATS_check/mtsr.csv", "instance"),
                   ("ip3", "MTSR_CATS_check/cats_check.csv", "instance")],
    "cats-sketch": [("", "MTSR_CATS_sketch/mtsr.csv", "name"),
                    ("", "MTSR_CATS_sketch/cats_sketch.csv", "name")],
    "cats-handcrafted": [("", "handcrafted/cats_handcrafted.csv", "label")],
    "corp-arbiters": [("", "handcrafted/corp_curated.csv", "instance")],
    "ltl": [("", "stats/mtsr_ltl.csv", "key")],
}
USES = {"tab_main": ("main",), "tab_msr_backends": ("main",), "tab_msreh": ("main",),
        "fig_data_msr_mtsr_size_v1": ("main",), "fig_data_msr_size": ("main",),
        "fig_data_cats_check_time": ("cats-check",), "tab_catssketch": ("cats-sketch",),
        "tab_catshand": ("cats-handcrafted",), "tab_corpcurated": ("corp-arbiters",),
        "tab_data_ltl": ("ltl",), "tab_data_resultsizes": ("main", "cats-sketch")}


def read(p, key):
    with open(p, newline="") as f:
        r = csv.DictReader(f)
        return r.fieldnames, [x for x in r if x.get(key) is not None]


def keys_of(root, part, rel, key):
    p = os.path.join(root, part, rel)
    return {x[key].removesuffix(".txt") for x in read(p, key)[1]} if os.path.exists(p) else None


def rerun_keys(src, ds):
    """{part: the instances every file of `ds` in the re-run has} (a stopped run
    leaves the last tools one instance behind); {} if `ds` was not re-run."""
    out = {}
    for part, rel, key in DATA[ds]:
        k = keys_of(src, part, rel, key)
        if k is not None:
            out[part] = k if part not in out else out[part] & k
    return out


def write_rows(src_file, dst_file, key, keep):
    header, rows = read(src_file, key)
    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
    with open(dst_file, "w", newline="") as f:
        w = csv.DictWriter(f, header)
        w.writeheader()
        w.writerows(x for x in rows if keep is None or x[key].removesuffix(".txt") in keep)


def compose(src, rec, uses, yours, recorded):
    """Two result trees for the floats made from the datasets `uses`: `yours`
    (the re-run's rows of those datasets, our recorded rows for the rest) and
    `recorded` (ours), both over the instances the re-run has.  Returns
    (full, notes): full when every one of `uses` that was re-run was re-run on
    all of its instances."""
    for d in (yours, recorded):
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(rec, d)
    full, notes, ran = True, [], {ds: rerun_keys(src, ds) for ds in uses}
    for ds in uses:
        if not ran[ds]:
            notes.append(f"{ds}: not re-run, our recorded rows used")
    # instances: a part (ip3 / ip4) is kept over the instances all re-run
    # datasets have; a part of `main` that was not re-run is left out on both sides
    for part in ("ip3", "ip4"):
        ks = [ran[ds][part] for ds in uses if part in ran[ds]]
        if "main" in uses and ran["main"] and part not in ran["main"]:
            for d in (yours, recorded):
                shutil.rmtree(os.path.join(d, part), ignore_errors=True)
            notes.append(f"main: {part} not re-run, left out")
            full = False
            continue
        if not ks:
            continue
        keep = set.intersection(*ks)
        if keep != keys_of(rec, part, "MSR/msr_autohyper.csv", "instance"):
            full = False
        for ds, entries in DATA.items():
            for pt, rel, key in entries:
                if pt != part:
                    continue
                for d in (yours, recorded):
                    write_rows(os.path.join(rec, pt, rel), os.path.join(d, pt, rel), key, keep)
                if ds in uses and ran[ds] and os.path.exists(os.path.join(src, pt, rel)):
                    write_rows(os.path.join(src, pt, rel), os.path.join(yours, pt, rel), key, keep)
    for ds in uses:
        for pt, rel, key in DATA[ds]:
            if pt or not ran[ds]:
                continue
            keep = ran[ds][""]
            if keep != keys_of(rec, "", rel, key):
                full = False
            write_rows(os.path.join(rec, rel), os.path.join(recorded, rel), key, keep)
            if os.path.exists(os.path.join(src, rel)):
                write_rows(os.path.join(src, rel), os.path.join(yours, rel), key, keep)
        if ds == "ltl" and ran[ds]:
            shutil.copy(os.path.join(src, "stats", "mtsr_ltl_meta.json"),
                        os.path.join(yours, "stats", "mtsr_ltl_meta.json"))
    return full, notes


def from_rerun(src, out, names):
    """Each float from its dataset of the re-run.  A dataset re-run in full is
    compared with the submitted paper; a partly re-run one is compared with our
    recorded rows over the same instances (out/recorded vs out/yours)."""
    rec = os.path.join(BENCH, "results")
    F = floats(src)
    print(f"re-run:  {src}\nwriting  {out}\n")
    groups = {}
    for n in sorted(names, key=lambda n: list(DATA).index(USES[n][0]) + len(USES[n]) / 10):
        groups.setdefault(USES[n], []).append(n)
    worst = 0
    for uses, ns in groups.items():
        what = " + ".join(uses)
        if not any(rerun_keys(src, ds) for ds in uses):
            print(f"[{what}] not re-run (bench/run.sh --dataset {uses[0]}); skipped: {', '.join(ns)}\n")
            continue
        work = os.path.join(out, "work", "_".join(uses))
        yours, recorded = os.path.join(work, "yours_src"), os.path.join(work, "recorded_src")
        full, notes = compose(src, rec, uses, yours, recorded)
        sizes = ", ".join(f"{len(k)} {p or ds}" for ds in uses for p, k in rerun_keys(src, ds).items())
        if full:
            print(f"[{what}] re-run in full ({sizes}); compared with the submitted paper:")
            for x in notes:
                print(f"  note: {x}")
            ok = generate(yours, out, ns, quiet=True)
            w = report(ns, out, os.path.join(HERE, "expected"), ("paper", "here "), F, [ok])
            print()
        else:
            o_rec, o_you = os.path.join(out, "recorded"), os.path.join(out, "yours")
            print(f"[{what}] re-run on part of the instances ({sizes}); each table and figure over "
                  f"those instances, from our results and from yours:")
            for x in notes:
                print(f"  note: {x}")
            ok1 = generate(recorded, o_rec, ns, quiet=True, extra_env={"BENCH_SUBSET": "1"})
            ok2 = generate(yours, o_you, ns, quiet=True, extra_env={"BENCH_SUBSET": "1"})
            w = report(ns, o_you, o_rec, ("recorded", "yours   "), F, [ok1, ok2])
            print()
        worst = max(worst, w)
    print("The tables and figures: tables/ and figures/ (a dataset re-run in full) or yours/ "
          f"next to recorded/ (in part), under {out}")
    print("Times depend on the machine, so a table that prints times can differ there; "
          "the counts and sizes should not.")
    return worst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*")
    ap.add_argument("--src", default=os.path.join(BENCH, "results"),
                    help="results directory to read (default: the recorded results)")
    ap.add_argument("--subset", action="store_true", help=argparse.SUPPRESS)   # now automatic
    ap.add_argument("--out", default=os.environ.get("PAPER_OUT") or os.path.join(HERE, "out"))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    src, out = os.path.abspath(RERUN if a.src == "rerun" else a.src), os.path.abspath(a.out)
    F = floats(src)
    if a.list:
        for n, (files, view, what, _) in F.items():
            print(f"  {n:28s} {view:8s} {what}")
        return
    names = a.names or list(F)
    for n in names:
        if n not in F:
            sys.exit(f"unknown table or figure {n}; see --list")

    if src == os.path.abspath(os.path.join(BENCH, "results")):
        print(f"reading  {src}\nwriting  {out}\n")
        ok = generate(src, out, names)
        worst = report(names, out, os.path.join(HERE, "expected"), ("paper", "here "), F, [ok])
        print(f"\ngenerated files: {os.path.join(out, 'tables')}  {os.path.join(out, 'figures')}")
        print(f"submitted ones:  {os.path.join(HERE, 'expected')}")
        print(f"numbers quoted in Section 5's text: {os.path.join(out, 'work', 'stats', 'plan2_numbers.json')}")
        sys.exit(worst)
    from_rerun(src, out, names)
    sys.exit(0)


if __name__ == "__main__":
    main()
