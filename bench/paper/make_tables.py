#!/usr/bin/env python3
r"""
make_tables -- regenerate every table and figure of the paper that comes from
the benchmark, and compare each with the version in the submitted paper.

    python3 make_tables.py                    # all 12, from the recorded results
    python3 make_tables.py --src ../rerun     # the same, from a full re-run of yours
    python3 make_tables.py --src ../rerun --subset   # a partial re-run: recorded vs yours
    python3 make_tables.py tab_main tab_msreh # only these
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
    DIFFERS   the rows that differ are printed, paper first

From a re-run (--src), DIFFERS is expected wherever a time is printed: the rows
are compared exactly, and wall-clock times depend on the machine. Read the
counts (instances, solved, sizes) against the paper; scripts/compare_runs.py
compares a re-run with the recorded rows instance by instance.

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
        status = "SAME" if all(v == "SAME" for _, v, _ in verdicts) else "DIFFERS"
        worst = max(worst, status != "SAME")
        print(f"  {n:28s} {status:8s} {what}")
        for f, v, diff in verdicts:
            if v != "SAME":
                print(f"      {f}: {v}")
                for who, r in diff[:12]:
                    print(f"        {who}: {' '.join(r)}")
    return worst


def instances(src, part):
    """The instances of `part` every CSV of the re-run has (a stopped run
    leaves the last tools one instance behind)."""
    sets = []
    for rel in ("MSR/msr_autohyper.csv", "MSR/msr_hyperqb.csv", "MSR/explainhyper.csv",
                "MTSR_CORP/mtsr.csv", "MTSR_CORP/corp.csv",
                "MTSR_CATS_check/mtsr.csv", "MTSR_CATS_check/cats_check.csv"):
        p = os.path.join(src, part, rel)
        if os.path.exists(p):
            sets.append({r["instance"].removesuffix(".txt") for r in csv.DictReader(open(p, newline=""))})
    return set.intersection(*sets) if sets else set()


def restrict(src_dir, dst, part, keep, fill_from=None, notes=None):
    """Copy the CSVs of src_dir/part into dst/part, keeping the rows of `keep`.
    A CSV missing from src_dir is taken from fill_from (the recorded results),
    restricted the same way, and named in `notes`."""
    for rel in ("MSR/msr_autohyper.csv", "MSR/msr_hyperqb.csv", "MSR/explainhyper.csv",
                "MTSR_CORP/mtsr.csv", "MTSR_CORP/corp.csv",
                "MTSR_CATS_check/mtsr.csv", "MTSR_CATS_check/cats_check.csv"):
        p = os.path.join(src_dir, part, rel)
        if not os.path.exists(p):
            if not fill_from or not os.path.exists(os.path.join(fill_from, part, rel)):
                continue
            p = os.path.join(fill_from, part, rel)
            if notes is not None:
                notes.append(f"{part}/{rel}")
        with open(p, newline="") as f:
            r = csv.DictReader(f)
            rows = [x for x in r if x["instance"].removesuffix(".txt") in keep]
            header = r.fieldnames
        os.makedirs(os.path.dirname(os.path.join(dst, part, rel)), exist_ok=True)
        with open(os.path.join(dst, part, rel), "w", newline="") as f:
            w = csv.DictWriter(f, header)
            w.writeheader()
            w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*")
    ap.add_argument("--src", default=os.path.join(BENCH, "results"),
                    help="results directory to read (default: the recorded results)")
    ap.add_argument("--subset", action="store_true",
                    help="--src is a partial re-run: build every table twice over the instances it "
                         "has -- from the recorded rows and from the re-run -- and compare the two")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    src, out = os.path.abspath(a.src), os.path.abspath(a.out)
    F = floats(src)
    if a.list:
        for n, (files, view, what, _) in F.items():
            print(f"  {n:28s} {view:8s} {what}")
        return
    names = a.names or list(F)
    for n in names:
        if n not in F:
            sys.exit(f"unknown float {n}; see --list")

    if not a.subset:
        print(f"reading  {src}\nwriting  {out}\n")
        ok = generate(src, out, names)
        worst = report(names, out, os.path.join(HERE, "expected"), ("paper", "here "), F, [ok])
        print(f"\ngenerated files: {os.path.join(out, 'tables')}  {os.path.join(out, 'figures')}")
        print(f"submitted ones:  {os.path.join(HERE, 'expected')}")
        print(f"numbers quoted in Section 5's text: {os.path.join(out, 'work', 'stats', 'plan2_numbers.json')}")
        sys.exit(worst)

    # --subset: the same tables over the same instances, recorded vs re-run
    rec = os.path.join(BENCH, "results")
    yours_src, rec_src = os.path.join(out, "work", "yours_src"), os.path.join(out, "work", "recorded_src")
    for d in (yours_src, rec_src):
        shutil.rmtree(d, ignore_errors=True)
    notes, counts = [], []
    for part in ("ip3", "ip4"):
        keep = instances(src, part)
        if not keep:
            continue
        counts.append(f"{len(keep)} {part}")
        restrict(src, yours_src, part, keep, fill_from=rec, notes=notes)
        restrict(rec, rec_src, part, keep)
    for d in ("MTSR_CATS_sketch", "curated"):
        s = os.path.join(src, d) if os.path.isdir(os.path.join(src, d)) else os.path.join(rec, d)
        if s.startswith(rec):
            notes.append(d)
        shutil.copytree(s, os.path.join(yours_src, d))
        shutil.copytree(os.path.join(rec, d), os.path.join(rec_src, d))
    print(f"re-run:   {src}  ({', '.join(counts)} instances)")
    for n in notes:
        print(f"  not in the re-run, recorded rows used: {n}")
    o_rec, o_you = os.path.join(out, "recorded"), os.path.join(out, "yours")
    print(f"writing  {o_rec}  and  {o_you}\n")
    ok1 = generate(rec_src, o_rec, names, quiet=True, extra_env={"BENCH_SUBSET": "1"})
    ok2 = generate(yours_src, o_you, names, quiet=True, extra_env={"BENCH_SUBSET": "1"})
    print("each table over the re-run's instances; times differ with the machine, the counts should not:\n")
    report(names, o_you, o_rec, ("recorded", "yours   "), F, [ok1, ok2])
    sys.exit(0)


if __name__ == "__main__":
    main()
