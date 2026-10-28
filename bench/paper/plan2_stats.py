#!/usr/bin/env python3
"""
plan2_stats -- the numbers of tacas2027_2 Sec. 6 on the plan-2 benchmark
(CATS's effects: randltl --tree-size=22..30, run by run_interleaved.sh).

    ~/micromamba/envs/spot/bin/python3 bench/paper/plan2_stats.py

1. Builds a results VIEW that the existing table scripts can read unchanged:
       claude_scripts/results/view_catsrl/
         MSR/, MTSR_CORP/            -> symlinks into demo_tool/bench/results_catsrl/ip3
         MTSR_CATS_check/            real dir: symlinked CSVs + agreement.csv (joined here)
         MTSR_CATS_sketch/           -> demo_tool/bench/results/MTSR_CATS_sketch
                                        (CATS's X^k error family; not re-run, effect-independent)
         gate.csv                    the ip3 rows of results_catsrl/gate.csv
   and claude_scripts/results/bench_stats_catsrl/extra.csv (effect size,
   candidate kind) next to symlinks of the old nexts / LTL stats.
2. Prints every number the prose of Sec. 6 quotes (and writes them to
   claude_scripts/results/bench_stats_catsrl/plan2_numbers.json).

Then:  BENCH_RESULTS=claude_scripts/results/view_catsrl paper_tables.py
       BENCH_RESULTS=... BENCH_STATS=claude_scripts/results/bench_stats_catsrl bench_figs.py
Counting rule = paper_tables.is_answer (an MSR, or AutoHyper's proof that none exists).
"""
import collections
import csv
import json
import os
import re
import statistics as st

import spot

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, HERE)
import paths  # noqa: E402
SRC = paths.SRC
IP3 = os.path.join(SRC, "ip3")
CORPUS = paths.CORPUS_IP3
VIEW = paths.VIEW
STATS = paths.STATS
OLD_STATS = paths.SHIPPED_STATS
SKETCH = os.path.join(SRC, "MTSR_CATS_sketch")
if not os.path.isdir(SKETCH):           # a re-run without the sketch stage
    SKETCH = os.path.join(paths.BENCH, "results", "MTSR_CATS_sketch")
WALL_CAP = 130.0


def link(src, dst):
    """Copy, not symlink: the view must survive being zipped or copied into a
    VM, and a stale directory from an earlier run is replaced whole."""
    import shutil
    if os.path.islink(dst) or os.path.isfile(dst):
        os.remove(dst)
    elif os.path.isdir(dst):
        shutil.rmtree(dst)
    (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, dst)


def rows(p):
    return list(csv.DictReader(open(p, newline="")))


def keyed(p):
    return {r.get("instance", r.get("file")).removesuffix(".txt"): r for r in rows(p)}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def is_answer(r):
    return r["status"] == "SAT_MSR" or (r["status"] == "SAT_SR" and r["model"] != "hyperqb")


def answered(r):
    return is_answer(r) and num(r["wall_s"]) is not None and num(r["wall_s"]) <= WALL_CAP


def spearman(xs, ys):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    a, b = rank(xs), rank(ys)
    ma, mb = st.mean(a), st.mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5


def ast_size(f):
    n = len(f)
    if n == 0:
        return 1
    return sum(ast_size(c) for c in f) + max(1, n - 1)


def build_view():
    os.makedirs(VIEW, exist_ok=True)
    os.makedirs(STATS, exist_ok=True)
    link(os.path.join(IP3, "MSR"), os.path.join(VIEW, "MSR"))
    link(os.path.join(IP3, "MTSR_CORP"), os.path.join(VIEW, "MTSR_CORP"))
    link(SKETCH, os.path.join(VIEW, "MTSR_CATS_sketch"))
    cc = os.path.join(VIEW, "MTSR_CATS_check")
    os.makedirs(cc, exist_ok=True)
    for f in ("cats_check.csv", "mtsr.csv"):
        link(os.path.join(IP3, "MTSR_CATS_check", f), os.path.join(cc, f))
    # agreement.csv: same join as bench/scripts/cats_check_agreement.py
    mt = {r["instance"]: r for r in rows(os.path.join(cc, "mtsr.csv"))}
    with open(os.path.join(cc, "agreement.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["instance", "cats_status", "cats_s", "mtsr_s",
                                           "is_mtsr", "agree", "note"])
        w.writeheader()
        for r in rows(os.path.join(cc, "cats_check.csv")):
            if r["cats_status"] not in ("IS_CAUSE", "NO_CAUSE"):
                continue
            m = mt.get(r["instance"]) or {}
            v = m.get("mtsr_verdict", "")
            rec = {"instance": r["instance"], "cats_status": r["cats_status"],
                   "cats_s": r["cats_s"], "mtsr_s": m.get("mtsr_s", ""),
                   "is_mtsr": "", "agree": "", "note": ""}
            if v in ("MTSR", "NOT_MTSR"):
                rec["is_mtsr"] = v == "MTSR"
                rec["agree"] = rec["is_mtsr"] == (r["cats_status"] == "IS_CAUSE")
            else:
                rec["note"] = f"no verdict: mtsr {m.get('mtsr_status', 'missing')}"
            w.writerow(rec)
    names = {n + ".txt" for n in keyed(os.path.join(IP3, "MSR", "msr_autohyper.csv"))}
    g = [r for r in rows(os.path.join(paths.BENCH, "results", "gate.csv"))]
    ip3_files = set(os.listdir(CORPUS)) if not os.environ.get("BENCH_SUBSET") else names
    systems3 = {f.split("__")[0] for f in ip3_files}
    with open(os.path.join(VIEW, "gate.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(g[0]))
        w.writeheader()
        for r in g:
            if r["file"].split("__")[0] in systems3:
                w.writerow(r)
    assert names == ip3_files, "results and corpus disagree"
    # extra.csv (the columns bench_figs / paper_tables use)
    cats = keyed(os.path.join(IP3, "MTSR_CATS_check", "cats_check.csv"))
    out = []
    for f in sorted(ip3_files):
        n = f.removesuffix(".txt")
        txt = open(os.path.join(CORPUS, f)).read()
        eff = re.search(r"\[effect\]\s*\n(.+)", txt).group(1).strip()
        r = {"instance": n, "effect_source": "randltl", "effect_shape": "random",
             "effect_size": ast_size(spot.formula(eff)), "cand_size": "", "cand_kind": ""}
        c = cats.get(n)
        if c and c["cats_cause"]:
            cf = spot.formula(c["cats_cause"])
            r["cand_size"] = ast_size(cf)
            r["cand_kind"] = ("const-true" if cf.is_tt() else "const-false" if cf.is_ff()
                              else "no-AP" if not spot.atomic_prop_collect(cf) else "proper")
        out.append(r)
    with open(os.path.join(STATS, "extra.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    for f in ("extra_nexts.csv", "mtsr_ltl.csv", "mtsr_ltl_meta.json", "tau_shape_rerun.csv"):
        # BENCH_SRC/stats when it has them (make_tables puts a re-run's LTL search there)
        own = os.path.join(SRC, "stats", f)
        link(own if os.path.exists(own) else os.path.join(OLD_STATS, f), os.path.join(STATS, f))
    return {r["instance"]: r for r in out}, g, systems3


def main():
    ex, gate, systems3 = build_view()
    N = {}
    g3 = [r for r in gate if r["file"].split("__")[0] in systems3]
    N["gen_ip3"], N["systems_ip3"] = len(g3), len(systems3)
    N["gated_ip3"] = sum(r["verdict"] == "OK" for r in g3)
    ah = keyed(os.path.join(IP3, "MSR", "msr_autohyper.csv"))
    hq = keyed(os.path.join(IP3, "MSR", "msr_hyperqb.csv"))
    eh = keyed(os.path.join(IP3, "MSR", "explainhyper.csv"))
    mt = keyed(os.path.join(IP3, "MTSR_CORP", "mtsr.csv"))
    co = keyed(os.path.join(IP3, "MTSR_CORP", "corp.csv"))
    cc = keyed(os.path.join(IP3, "MTSR_CATS_check", "cats_check.csv"))
    ccm = keyed(os.path.join(IP3, "MTSR_CATS_check", "mtsr.csv"))
    inst = sorted(ah)
    N["instances"] = len(inst)
    N["systems_with_instance"] = len({n.split("__")[0] for n in inst})

    def best(n):
        c = [num(r["wall_s"]) for r in (ah[n], hq[n]) if answered(r)]
        return min(c) if c else None

    ip = lambda n: int(ah[n]["inputs"])
    q = lambda n: int(ah[n]["sys_states"])
    for g in (1, 2, 3, None):
        sel = [n for n in inst if g is None or ip(n) == g]
        sol = [n for n in sel if best(n) is not None]
        N[f"msr_ip{g}"] = dict(n=len(sel), solved=len(sol),
                               pct=round(100 * len(sol) / len(sel), 1),
                               avg=round(st.mean(best(n) for n in sol), 2))
    N["late_ah"] = sum(1 for n in inst if is_answer(ah[n]) and num(ah[n]["wall_s"]) > WALL_CAP)
    N["late_hq"] = sum(1 for n in inst if is_answer(hq[n]) and num(hq[n]["wall_s"]) > WALL_CAP)
    late = [n for n in inst if best(n) is None and any(
        is_answer(r) and num(r["wall_s"]) > WALL_CAP for r in (ah[n], hq[n]))]
    N["late_only_unsolved"] = len(late)
    N["late_over_hour"] = sum(1 for n in inst for r in (ah[n], hq[n])
                              if is_answer(r) and num(r["wall_s"]) > 3600 + 120)
    uns = [n for n in inst if best(n) is None]
    N["unsolved"] = len(uns)
    N["unsolved_ip_min"] = min(ip(n) for n in uns) if uns else None
    N["unsolved_by_ip"] = dict(collections.Counter(ip(n) for n in uns))
    N["ah_status"] = dict(collections.Counter(ah[n]["status"] for n in inst))
    N["hq_status"] = dict(collections.Counter(hq[n]["status"] for n in inst))
    N["ah_answers"] = sum(answered(ah[n]) for n in inst)
    N["ah_nomsr_proofs"] = sum(1 for n in inst if ah[n]["status"] == "SAT_SR" and answered(ah[n]))
    N["hq_msr_certified"] = sum(1 for n in inst if hq[n]["status"] == "SAT_MSR" and answered(hq[n]))
    N["hq_only"] = sum(1 for n in inst if answered(hq[n]) and not answered(ah[n]))
    N["no_msr_any"] = N["ah_nomsr_proofs"]
    N["hq_sat_sr_not_counted"] = sum(1 for n in inst if hq[n]["status"] == "SAT_SR")
    sol = [n for n in inst if best(n) is not None]
    N["rho_ip"] = round(spearman([ip(n) for n in sol], [best(n) for n in sol]), 2)
    N["rho_q"] = round(spearman([q(n) for n in sol], [best(n) for n in sol]), 2)
    N["rho_q_within"] = [round(spearman([q(n) for n in sol if ip(n) == g],
                                        [best(n) for n in sol if ip(n) == g]), 2) for g in (1, 2, 3)]
    ok = [n for n in inst if mt[n]["mtsr_status"] == "OK"]
    N["mtsr_ok"], N["mtsr_det"] = len(ok), sum(mt[n]["mtsr_det"] == "True" for n in ok)
    N["mtsr_states_avg"] = round(st.mean(num(mt[n]["mtsr_states"]) for n in ok), 2)
    N["mtsr_t_avg"] = round(st.mean(num(mt[n]["mtsr_s"]) for n in ok), 3)
    N["mtsr_fail"] = dict(collections.Counter(mt[n]["mtsr_status"] for n in inst if n not in ok))
    N["mtsr_fail_min_q"] = min((q(n) for n in inst if n not in ok), default=None)
    for g in (1, 2, 3):
        s = [n for n in ok if ip(n) == g]
        N[f"mtsr_ip{g}"] = dict(n=sum(ip(n) == g for n in inst), ok=len(s),
                                avg=round(st.mean(num(mt[n]["mtsr_s"]) for n in s), 2))
    # ExplainHyper
    sup = [n for n in inst if eh[n]["status"] != "UNSUPPORTED"]
    N["eh_supported"] = len(sup)
    N["eh_supported_systems"] = len({n.split("__")[0] for n in sup})
    N["eh_status"] = dict(collections.Counter(eh[n]["status"] for n in inst))
    N["eh_skip"] = dict(collections.Counter(eh[n]["skip_reason"] for n in inst if eh[n]["status"] == "UNSUPPORTED"))
    both = [n for n in inst if eh[n]["status"] == "OK" and best(n) is not None]
    N["eh_both"] = len(both)

    def rspan(r):
        return max(int(x.split("@")[1].rstrip("*")) for x in r["reason"].split(",") if x) + 1

    def bestrow(n):
        c = [(num(r["wall_s"]), r) for r in (ah[n], hq[n]) if answered(r)]
        return min(c, key=lambda t: t[0])[1]
    import ast as _ast
    cspan = lambda n: min(max(e[1] for e in c) + 1 for c in _ast.literal_eval(eh[n]["causes"]))
    longer = sum(1 for n in both if rspan(bestrow(n)) > cspan(n))
    shorter = sum(1 for n in both if rspan(bestrow(n)) < cspan(n))
    N["eh_reason_longer"], N["eh_reason_shorter"] = longer, shorter
    N["eh_reason_equal"] = len(both) - longer - shorter
    N["eh_t_avg"] = round(st.mean(num(eh[n]["wall_s"]) for n in both), 2)
    N["eh_msr_t_avg"] = round(st.mean(best(n) for n in both), 2)
    # CATS check
    kinds = collections.Counter(ex[n]["cand_kind"] for n in inst)
    N["cand_kinds"] = dict(kinds)
    prop = [n for n in inst if ex[n]["cand_kind"] == "proper"]
    N["cand_proper"] = len(prop)
    rows_ = [n for n in prop if cc[n]["cats_status"] in ("IS_CAUSE", "NO_CAUSE")
             and ccm[n]["mtsr_status"] == "OK"]
    N["cc_rows"] = len(rows_)
    N["cc_cats_status"] = dict(collections.Counter(cc[n]["cats_status"] for n in inst))
    N["cc_cross"] = dict(collections.Counter(
        (cc[n]["cats_status"], ccm[n]["mtsr_verdict"]) for n in rows_))
    N["cc_cross_all"] = {f"{a}|{b}": v for (a, b), v in collections.Counter(
        (cc[n]["cats_status"], ccm[n]["mtsr_verdict"]) for n in inst).items()}
    N["cc_cross"] = {f"{a}|{b}": v for (a, b), v in collections.Counter(
        (cc[n]["cats_status"], ccm[n]["mtsr_verdict"]) for n in rows_).items()}
    N["cc_failed_stage"] = dict(collections.Counter(cc[n]["cats_failed"] for n in rows_))
    N["cc_mtsr_faster"] = sum(num(ccm[n]["mtsr_s"]) < num(cc[n]["cats_s"]) for n in rows_)
    N["cc_mtsr_t"] = round(st.mean(num(ccm[n]["mtsr_s"]) for n in rows_), 2)
    N["cc_cats_t"] = round(st.mean(num(cc[n]["cats_s"]) for n in rows_), 2)
    N["cc_is_mtsr_any"] = [n for n in inst if ccm[n]["mtsr_verdict"] == "MTSR"]
    # CORP
    bc = [n for n in inst if mt[n]["mtsr_status"] == "OK" and co[n]["corp_status"] == "OK"]
    N["corp_both"] = len(bc)
    N["corp_ok"] = sum(co[n]["corp_status"] == "OK" for n in inst)
    N["corp_fail"] = dict(collections.Counter(co[n]["corp_status"] for n in inst if co[n]["corp_status"] != "OK"))
    N["corp_fail_min_q"] = min((q(n) for n in inst if co[n]["corp_status"] != "OK"), default=None)
    N["corp_equiv"] = dict(collections.Counter(co[n]["equiv_note"] for n in bc))
    N["corp_states"] = round(st.mean(num(co[n]["corp_states"]) for n in bc), 2)
    N["corp_states_max"] = max(num(co[n]["corp_states"]) for n in bc)
    N["mtsr_states_bc"] = round(st.mean(num(mt[n]["mtsr_states"]) for n in bc), 2)
    N["corp_t"] = round(st.mean(num(co[n]["corp_s"]) for n in bc), 3)
    N["mtsr_t_bc"] = round(st.mean(num(mt[n]["mtsr_s"]) for n in bc), 3)
    s1 = [n for n in bc if q(n) == 1]
    s17 = [n for n in bc if q(n) >= 17]
    N["corp_q1"] = (len(s1), round(st.mean(num(co[n]["corp_s"]) for n in s1), 2) if s1 else None,
                    round(st.mean(num(mt[n]["mtsr_s"]) for n in s1), 2) if s1 else None)
    N["corp_q17"] = (len(s17), round(st.mean(num(co[n]["corp_s"]) for n in s17), 2),
                     round(st.mean(num(mt[n]["mtsr_s"]) for n in s17), 2))
    N["rho_eh_q"] = round(spearman([q(n) for n in inst if eh[n]["status"] in ("OK", "NO_CAUSE")],
                                   [num(eh[n]["wall_s"]) for n in inst if eh[n]["status"] in ("OK", "NO_CAUSE")]), 2)
    ccr = [n for n in inst if cc[n]["cats_status"] in ("IS_CAUSE", "NO_CAUSE")]
    N["rho_cats_q"] = round(spearman([q(n) for n in ccr], [num(cc[n]["cats_s"]) for n in ccr]), 2)
    ccm_ok = [n for n in inst if ccm[n]["mtsr_status"] == "OK"]
    N["rho_ccmtsr_q"] = round(spearman([q(n) for n in ccm_ok], [num(ccm[n]["mtsr_s"]) for n in ccm_ok]), 2)
    json.dump(N, open(os.path.join(STATS, "plan2_numbers.json"), "w"), indent=1)
    if "--quiet" not in sys.argv:
        print(json.dumps(N, indent=1))


if __name__ == "__main__":
    if os.environ.get("BENCH_SUBSET"):   # make_tables --subset: the view only; the
        build_view()                     # prose numbers are over the full corpus
    else:
        main()
