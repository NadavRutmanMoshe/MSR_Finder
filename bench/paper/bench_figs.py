#!/usr/bin/env python3
"""
bench_figs -- every benchmark statistic as a candidate figure or table for
tacas2027_2's data appendix (Hadar asked for "all results and stats, in tables
and graphs"; the paper keeps a subset later).

    ~/micromamba/envs/spot/bin/python3 bench/paper/bench_figs.py

Reads   demo_tool/bench/results/**.csv                      (the benchmark)
        claude_scripts/results/bench_stats/{extra,extra_nexts,mtsr_ltl}.csv
                                                              (bench_stats_extra.py, mtsr_ltl.py)
Writes  tacas2027_2/figures/data_*.pdf  + figures/fig_data_*.tex  (one float each)
        tacas2027_2/tables/tab_data_*.tex
        claude_scripts/results/bench_stats/numbers.json   (every number quoted in prose)

Conventions (same as paper_tables.py): a mean/median is over the instances the
tool answered, a timeout never enters it; comparisons are over the instances
BOTH tools answered.  Timeouts are drawn as crosses in a band above the 120 s
line, never dropped silently.
"""
import ast
import collections
import csv
import json
import math
import os
import random
import statistics as st

import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, HERE)
import paths  # noqa: E402
RES = paths.VIEW
STATS = paths.STATS
#: plan 2 (2026-10-14): the LTL-definability search (mtsr_ltl.py) has not been
#: re-run on the new corpus, so tab_ltl is skipped when BENCH_STATS is set.
SKIP_LTL = bool(os.environ.get("BENCH_STATS"))
FIG, TAB = paths.FIGURES, paths.TABLES
BUDGET = 120.0
ANSWERED = ("SAT_MSR", "SAT_SR")


def is_answer(row):
    """A reason the paper counts: a certified MINIMAL reason (SAT_MSR, either
    backend), or AutoHyper's SAT_SR, which it reaches only by PROVING the MSR
    query unsat, i.e. no minimal reason exists.  HyperQB's SAT_SR is not an
    answer: its MSR query is bounded, so it never rules an MSR out, and a
    sufficient reason alone is trivial (R = all of pi's inputs always is one).
    Nadav, 2026-10-11."""
    if row["status"] == "SAT_MSR":
        return True
    return row["status"] == "SAT_SR" and row.get("model") != "hyperqb"

# categorical slots 1-3 of the validated reference palette (all-pairs safe)
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
IPC = {1: BLUE, 2: ORANGE, 3: AQUA}
IPM = {1: "o", 2: "s", 3: "^"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
TW = 4.8  # LNCS text width in inches

plt.rcParams.update({
    "font.family": "serif", "mathtext.fontset": "cm", "font.size": 7.5,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "axes.linewidth": 0.6,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.major.width": 0.5,
    "ytick.major.width": 0.5, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.5, "axes.axisbelow": True, "legend.frameon": False,
    "legend.fontsize": 6.5, "axes.spines.top": False, "axes.spines.right": False,
    "pdf.fonttype": 42,
})
NUM = {}


# ------------------------------------------------------------------ data ----
def load(path, key="instance"):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return {r[key].removesuffix(".txt"): r for r in rows}


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mean(v, spec="{:.2f}"):
    v = [x for x in v if x is not None]
    return spec.format(st.mean(v)) if v else "--"


def med(v, spec="{:.2f}"):
    v = [x for x in v if x is not None]
    return spec.format(st.median(v)) if v else "--"


AH = load(os.path.join(RES, "MSR", "msr_autohyper.csv"))
HQ = load(os.path.join(RES, "MSR", "msr_hyperqb.csv"))
VQ = (load(os.path.join(RES, "MSR", "verify_hyperqb.csv"))
      if os.path.exists(os.path.join(RES, "MSR", "verify_hyperqb.csv")) else {})
EH = load(os.path.join(RES, "MSR", "explainhyper.csv"))
MT = load(os.path.join(RES, "MTSR_CORP", "mtsr.csv"))
CO = load(os.path.join(RES, "MTSR_CORP", "corp.csv"))
CC = load(os.path.join(RES, "MTSR_CATS_check", "cats_check.csv"))
CCM = load(os.path.join(RES, "MTSR_CATS_check", "mtsr.csv"))
SK = load(os.path.join(RES, "MTSR_CATS_sketch", "cats_sketch.csv"), "name")
SKM = load(os.path.join(RES, "MTSR_CATS_sketch", "mtsr.csv"), "name")
GATE = [r for r in csv.DictReader(open(os.path.join(RES, "gate.csv")))]
EX = load(os.path.join(STATS, "extra.csv"))
EXN = load(os.path.join(STATS, "extra_nexts.csv"), "name")
LTL = {k.split("/", 1)[1]: r for k, r in
       ((r["key"], r) for r in csv.DictReader(open(os.path.join(STATS, "mtsr_ltl.csv"))))}
LTLMETA = json.load(open(os.path.join(STATS, "mtsr_ltl_meta.json")))
INST = sorted(AH)


def ip(n):
    return int(AH[n]["inputs"])


def q(n):
    return int(AH[n]["sys_states"])


def lasso(n):
    return int(AH[n]["P"]) + int(AH[n]["L"])


def rsize(row):
    a, b = f(row["size"]), f(row.get("loop_size"))
    return None if a is None else a + (b or 0)


WALL_CAP = 130.0   # 120 s solver budget + slack; model building is OUTSIDE the
                   # solver timeout, and 17 answers arrived after 125-4305 s wall


def answered(row):
    return is_answer(row) and f(row["wall_s"]) <= WALL_CAP


def late(row):
    return is_answer(row) and f(row["wall_s"]) > WALL_CAP


def status(row):
    return "LATE" if late(row) else row["status"]


def best(n):
    c = []
    if answered(AH[n]):
        c.append((f(AH[n]["wall_s"]), "AH", AH[n]))
    if answered(HQ[n]):
        c.append((f(HQ[n]["wall_s"]), "HQ", HQ[n]))
    return min(c, key=lambda t: t[0]) if c else None


def minimal_reason(n):
    """|R| of a MINIMAL reason if either backend certified one (AutoHyper first)."""
    for row in (AH[n], HQ[n]):
        if row["status"] == "SAT_MSR" and answered(row):
            return rsize(row)
    return None


def ltl_class(r):
    if r is None:
        return "n/a"
    if r["ltl"] or r["counter_free"] == "True":
        return "ltl"
    if r["non_ltl_witness"]:
        return "not-ltl"
    return "unknown"


# --------------------------------------------------------------- helpers ----
def jitter(v, amt=0.18, seed=[0]):
    rnd = random.Random(seed[0])
    seed[0] += 1
    return [x + rnd.uniform(-amt, amt) for x in v]


def time_scatter(ax, pts, xlabel, logx=None, title=None, by_ip=True, color=BLUE):
    """pts: list of (x, seconds_or_None, ip). None = timeout / no answer."""
    ax.set_yscale("log")
    if logx or (logx is None and "|Q|" in xlabel):
        logq(ax)
    ax.axhline(BUDGET, color=MUTED, lw=0.6, ls=(0, (3, 2)))
    groups = sorted({p[2] for p in pts}, reverse=True) if by_ip else [None]  # small |I|=1 group on top
    for g in groups:
        sel = [p for p in pts if (not by_ip or p[2] == g)]
        ok = [p for p in sel if p[1] is not None]
        to = [p for p in sel if p[1] is None]
        c = IPC[g] if by_ip else color
        ax.scatter([p[0] for p in ok], [max(p[1], 0.05) for p in ok], s=9,
                   marker=IPM[g] if by_ip else "o", facecolor=c, edgecolor="white",
                   linewidth=0.3, alpha=0.8, label=(f"$|I|={g}$" if by_ip else None))
        if to:
            ax.scatter([p[0] for p in to], jitter([BUDGET * 1.9] * len(to), 25),
                       s=10, marker="x", color=c, linewidth=0.6, alpha=0.8)
    ax.set_ylim(0.08, 400)
    ax.set_yticks([0.1, 1, 10, 100, BUDGET * 1.9])
    ax.set_yticklabels(["0.1", "1", "10", "100", "TO"])
    ax.set_xlabel(xlabel)
    if title:
        ax.set_title(title, fontsize=7.5, color=INK)


def logq(ax, axis="x"):
    """System sizes: median 3, max 82 -- a linear axis puts 90% in one column."""
    from matplotlib.ticker import FixedLocator, NullFormatter, NullLocator
    getattr(ax, f"set_{axis}scale")("log")
    a = ax.xaxis if axis == "x" else ax.yaxis
    a.set_major_locator(FixedLocator([1, 2, 5, 10, 20, 50]))
    a.set_major_formatter(matplotlib.ticker.FixedFormatter(["1", "2", "5", "10", "20", "50"]))
    a.set_minor_locator(NullLocator())


def top_legend(fig, ax, ncol=3, y=1.0):
    h, l = ax.get_legend_handles_labels()
    if all(x.startswith("$|I|=") for x in l):
        l, h = zip(*sorted(zip(l, h), key=lambda t: t[0]))
    if ax.get_legend():
        ax.get_legend().remove()
    fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, y), ncol=ncol,
               handletextpad=0.2, columnspacing=1.2, markerscale=1.3)


def _plain(s):
    """Numbers are plain in the paper (Hadar, 2026-10-05): drop \\num{}."""
    import re
    return re.sub(r"\\num\{([^{}]*)\}", r"\1", s)


def save(fig, name, caption, label, width=r"\textwidth", placement="htbp"):
    caption = _plain(caption)
    fig.savefig(os.path.join(FIG, f"data_{name}.pdf"), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    with open(os.path.join(FIG, f"fig_data_{name}.tex"), "w") as fh:
        fh.write("% generated by bench/paper/bench_figs.py -- do not edit\n"
                 f"\\begin{{figure}}[{placement}]\n\\centering\n"
                 f"\\includegraphics[width={width}]{{figures/data_{name}.pdf}}\n"
                 f"\\caption{{{caption}}}\n\\label{{fig:data-{label}}}\n"
                 "\\end{figure}\n")


def table(name, caption, colspec, head, rows, label=None, size=r"\footnotesize", extra="", placement="htbp"):
    caption = _plain(caption)
    body = [r"\toprule", *head, r"\midrule"]
    for r in rows:
        body.append(r if isinstance(r, str) else " & ".join(str(c) for c in r) + r" \\")
    body.append(r"\bottomrule")
    with open(os.path.join(TAB, f"tab_data_{name}.tex"), "w") as fh:
        fh.write("% generated by bench/paper/bench_figs.py -- do not edit\n"
                 f"\\begin{{table}}[{placement}]\n\\centering\n" + size + "\n"
                 "\\renewcommand{\\arraystretch}{1.3}\n"
                 f"\\caption{{{caption}}}\n\\label{{tab:data-{label or name}}}\n"
                 f"\\begin{{tabular}}{{@{{}}{colspec}@{{}}}}\n" + "\n".join(body)
                 + "\n\\end{tabular}\n" + extra + "\\end{table}\n")


def pct(a, b):
    return f"{100 * a / b:.0f}\\,\\%" if b else "--"


# ================================================================ TABLES ====
def tab_funnel():
    verdicts = collections.Counter(r["verdict"] for r in GATE)
    src_all = collections.Counter()
    for r in GATE:
        txt_src = "template" if r["file"] in () else None
    gated_src = collections.Counter(EX[n]["effect_source"] for n in INST)
    systems = {n.split("__")[0] for n in INST}
    all_systems = {r["file"].split("__")[0] for r in GATE}
    ah = collections.Counter(status(AH[n]) for n in INST)
    hq = collections.Counter(status(HQ[n]) for n in INST)
    NUM["late_ah"], NUM["late_hq"] = ah["LATE"], hq["LATE"]
    eh = collections.Counter(EH[n]["status"] for n in INST)
    mt = collections.Counter(MT[n]["mtsr_status"] for n in INST)
    co = collections.Counter(CO[n]["corp_status"] for n in INST)
    cc = collections.Counter(CC[n]["cats_status"] for n in INST)
    NUM.update(gen=len(GATE), gated=len(INST), systems=len(systems),
               all_systems=len(all_systems), effect_false=verdicts["EFFECT_FALSE"])
    rows = [
        ("instances generated (10 effects per system)", len(GATE), f"{len(all_systems)} systems"),
        (r"\quad rejected by \texttt{PI\_VAL}: $\pit \not\models \eff$", verdicts["EFFECT_FALSE"], ""),
        (r"\textbf{gated instances}", rf"\textbf{{{len(INST)}}}",
         f"{len(systems)} systems; {gated_src['template']} templated, {gated_src['randltl']} random"),
        r"\midrule",
        (r"$\MSR$, \textsc{AutoHyper}", f"{ah['SAT_MSR'] + ah['SAT_SR']}",
         f"{ah['SAT_MSR']} minimal, {ah['SAT_SR']} sufficient, "
         f"{sum(1 for n in INST if AH[n]['status']=='ERROR' and 'TIMED OUT' in AH[n]['err'])} timeout, "
         f"{sum(1 for n in INST if AH[n]['status']=='ERROR' and 'TIMED OUT' not in AH[n]['err'])} solver error"
         + (f", {ah['LATE']} late (wall $>$ {WALL_CAP:.0f}\\,s)" if ah['LATE'] else "")),
        (r"$\MSR$, \textsc{HyperQB} (bounded)", f"{hq['SAT_MSR'] + hq['SAT_SR']}",
         f"{hq['SAT_MSR']} minimal, {hq['SAT_SR']} sufficient, {hq['UNKNOWN_BOUNDED']} unknown"
         + (f", {hq['LATE']} late (wall $>$ {WALL_CAP:.0f}\\,s)" if hq['LATE'] else "")),
        (r"$\MSR$, either backend", sum(1 for n in INST if best(n)), ""),
        (r"\textsc{ExplainHyper}", eh["OK"] + eh["NO_CAUSE"],
         f"{eh['OK']} cause, {eh['NO_CAUSE']} no cause, {eh['UNSUPPORTED']} not expressible, "
         f"{eh['TIMEOUT']} timeout, {eh['ERROR']} error"),
        r"\midrule",
        (r"$\MTSR$", mt["OK"], f"{mt['TIMEOUT']} timeout, {mt['ERROR']} out of memory (6\\,GB)"),
        (r"\textsc{CORP}", co["OK"], f"{co['TIMEOUT']} timeout, {co['ERROR']} error"),
        (r"\textsc{CATS} \texttt{--check}", cc["IS_CAUSE"] + cc["NO_CAUSE"],
         f"{cc['IS_CAUSE']} cause, {cc['NO_CAUSE']} no cause, {cc['TIMEOUT']} timeout"),
        (r"\textsc{CATS} sketch / $\MTSR$ (next-only family)",
         f"{sum(1 for r in SK.values() if r['cats_status']=='FOUND')} / "
         f"{sum(1 for r in SKM.values() if r['mtsr_status']=='OK')}",
         f"of {len(SK)} instances"),
    ]
    table("funnel",
          r"The whole benchmark at a glance: how the \textsc{CATS} SYNTCOMP "
          r"instances are gated, and what every tool returned on the gated set "
          r"(\num{120}\,s per query). `answered' counts every definite answer.",
          r"p{4.1cm}rp{5.3cm}", [r"& answered & breakdown \\"], rows, size=r"\scriptsize")


def binlab(b):
    return str(b[0]) if b[0] == b[1] else (f"{b[0]}--{b[1]}" if b[1] < 999 else f"$\\geq{b[0]}$")


BINS = [(1, 1), (2, 2), (3, 4), (5, 8), (9, 16), (17, 999)]


def binlab(b):
    return str(b[0]) if b[0] == b[1] else (f"{b[0]}--{b[1]}" if b[1] < 999 else f"$\\geq{b[0]}$")


def tab_sizebins():
    rows = []
    for b in BINS + [None]:
        sel = [n for n in INST if b is None or b[0] <= q(n) <= b[1]]
        if not sel:
            continue
        sol = [n for n in sel if best(n)]
        mt = [n for n in sel if MT[n]["mtsr_status"] == "OK"]
        co = [n for n in sel if CO[n]["corp_status"] == "OK"]
        cc = [n for n in sel if CC[n]["cats_status"] in ("IS_CAUSE", "NO_CAUSE")]
        eh = [n for n in sel if EH[n]["status"] in ("OK", "NO_CAUSE")]
        rows.append(["all" if b is None else binlab(b), len(sel),
                     mean([ip(n) for n in sel], "{:.1f}"),
                     pct(len(sol), len(sel)), med([best(n)[0] for n in sol]),
                     mean([best(n)[0] for n in sol]),
                     mean([f(MT[n]["mtsr_s"]) for n in mt]),
                     mean([f(CO[n]["corp_s"]) for n in co]),
                     mean([f(CC[n]["cats_s"]) for n in cc]),
                     f"{mean([f(EH[n]['wall_s']) for n in eh])} ({len(eh)})"])
        if b == BINS[-1]:
            rows.append(r"\midrule")
    table("sizebins",
          r"Runtime against system size $|Q|$, every tool, over the instances "
          r"that tool answered (\textsc{ExplainHyper}'s answered count in "
          r"brackets --- it expresses only a quarter of the family). The mean "
          r"$|\ip|$ column is the confounder: $\MSR$ time follows it, not $|Q|$.",
          r"lrr@{\hspace{8pt}}rrr@{\hspace{8pt}}rrrr",
          [r"& & & \multicolumn{3}{c}{$\MSR$ (either backend)} & \multicolumn{4}{c}{mean time (s)} \\",
           r"\cmidrule(lr){4-6}\cmidrule(lr){7-10}",
           r"$|Q|$ & inst. & $\overline{|\ip|}$ & solved & med.\ s & mean s & $\MTSR$ & \textsc{CORP} & \textsc{CATS} & \textsc{ExplHyp.} \\"],
          rows)


def spearman(xs, ys):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def tab_corr():
    """Spearman rank correlation of runtime with each size measure."""
    def row(label, sel, t):
        xs = {"|Q|": [q(n) for n in sel], "|I|": [ip(n) for n in sel],
              "lasso": [lasso(n) for n in sel],
              "|eff|": [int(EX[n]["effect_size"]) for n in sel]}
        ts = [t(n) for n in sel]
        return [label, len(sel)] + [f"{spearman(xs[k], ts):+.2f}" for k in ("|Q|", "|I|", "lasso", "|eff|")]
    rows = [
        row(r"$\MSR$ (either)", [n for n in INST if best(n)], lambda n: best(n)[0]),
        row(r"\textsc{ExplainHyper}", [n for n in INST if EH[n]["status"] in ("OK", "NO_CAUSE")], lambda n: f(EH[n]["wall_s"])),
        row(r"$\MTSR$", [n for n in INST if MT[n]["mtsr_status"] == "OK"], lambda n: f(MT[n]["mtsr_s"])),
        row(r"\textsc{CORP}", [n for n in INST if CO[n]["corp_status"] == "OK"], lambda n: f(CO[n]["corp_s"])),
        row(r"\textsc{CATS} \texttt{--check}", [n for n in INST if CC[n]["cats_status"] in ("IS_CAUSE", "NO_CAUSE")], lambda n: f(CC[n]["cats_s"])),
    ]
    NUM["rho_msr_q"], NUM["rho_msr_ip"] = rows[0][2], rows[0][3]
    # |Q| and |I| are themselves correlated, so hold |I| fixed as well
    within = [row(rf"\quad $\MSR$, $|\ip|={g}$ only",
                  [n for n in INST if best(n) and ip(n) == g], lambda n: best(n)[0])
              for g in (1, 2, 3)]
    for w in within:
        w[3] = "--"
    rows[1:1] = within
    NUM["rho_msr_q_within"] = [w[2] for w in within]
    table("corr",
          r"What drives runtime: Spearman rank correlation between each tool's "
          r"time and four size measures of the instance --- system states "
          r"$|Q|$, input propositions $|\ip|$, lasso length $|u|+|v|$, and "
          r"effect size $|\eff|$ (AST nodes). Over answered instances.",
          r"lrrrrr",
          [r"tool & inst. & $|Q|$ & $|\ip|$ & $|u|+|v|$ & $|\eff|$ \\"], rows)


def reason_span(n):
    """Trace length of our minimal reason: steps 0..last pinned step.  A pin in
    the loop (`@t*`) recurs forever but sits at one lasso position, so the loop
    is counted once -- the lasso is the finite form of the infinite trace."""
    ps = [int(x.split("@")[1].rstrip("*")) for x in AH[n]["reason"].split(",") if x]
    return max(ps) + 1


def cause_span(n):
    """Trace length of ExplainHyper's shortest cause: steps 0..its last event."""
    return min(max(e[1] for e in c) + 1 for c in ast.literal_eval(EH[n]["causes"]))


def msr_best(n):
    """(seconds, row) of the fastest run that returned a certified MINIMAL
    reason (SAT_MSR, either backend), or None.  Same rule as
    paper_tables.tab_msreh, so the two tables share their bottom line
    (Hadar, 2026-10-14)."""
    # |IP| = 4 ran with a 300 s timeout: same cap as paper_tables.tab_msreh, so
    # the summary row matches Table msreh's all* row in a view with ip4 in it
    # (the submitted paper's 152 instances; with the 130 s cap it was 146).
    cap = 300.0 + 10.0 if AH[n]["inputs"] == "4" else WALL_CAP
    c = [(f(r["wall_s"]), r) for r in (AH[n], HQ[n])
         if r["status"] == "SAT_MSR" and is_answer(r) and f(r["wall_s"]) <= cap]
    return min(c, key=lambda t: t[0]) if c else None


def span_of(row):
    ps = [int(x.split("@")[1].rstrip("*")) for x in row["reason"].split(",") if x]
    return max(ps) + 1


def tab_resultsizes():
    """Average size AND running time of what each tool returns (time columns
    added for Hadar, 2026-10-05), over the instances both answered."""
    # 2026-10-14: same rule as paper_tables.tab_msreh (fastest certified MSR,
    # either backend), so the bottom lines match (Hadar).  The column-per-tool
    # style tried the same day was rejected (Nadav); this is the row style.
    import lasso_size as LS
    both_eh = [n for n in INST if EH[n]["status"] == "OK" and msr_best(n)]
    both_co = [n for n in INST if MT[n]["mtsr_status"] == "OK" and CO[n]["corp_status"] == "OK"]
    sk_ans = [n for n in SK if SK[n]["cats_status"] == "FOUND" and SKM[n]["mtsr_status"] == "OK"]

    def avg(v):
        return mean(v, "{:.1f}")

    def avt(v):
        return mean(v, "{:.2f}")
    rows = [
        # size = smallest lasso of the explanation (Nadav, 2026-10-14)
        [r"$\MSR$ vs.\ \textsc{\cite{coenen2022hyperproperty}}", len(both_eh), "lasso size",  # Overleaf zip (12), Hadar, 2026-10-15
         avg([LS.sizes(n, msr_best(n)[1])[0] for n in both_eh]),
         avg([LS.sizes(n, msr_best(n)[1])[1] for n in both_eh]),
         avt([msr_best(n)[0] for n in both_eh]), avt([f(EH[n]["wall_s"]) for n in both_eh])],
        [r"$\MTSR$ vs.\ \textsc{CORP}", len(both_co), r"$|A|$",
         avg([f(MT[n]["mtsr_states"]) for n in both_co]), avg([f(CO[n]["corp_states"]) for n in both_co]),
         avt([f(MT[n]["mtsr_s"]) for n in both_co]), avt([f(CO[n]["corp_s"]) for n in both_co])],
        [r"$\MTSR$ vs.\ \textsc{CATS} sketch", len(sk_ans), r"$|A|$ / $|\varphi_{\mathsf{C}}|$",
         avg([f(SKM[n]["mtsr_states"]) for n in sk_ans]), avg([f(EXN[n]["cats_ast_size"]) for n in sk_ans]),
         avt([f(SKM[n]["mtsr_s"]) for n in sk_ans]), avt([f(SK[n]["cats_s"]) for n in sk_ans])],
    ]
    NUM["span_ours"], NUM["span_theirs"] = rows[0][3], rows[0][4]
    NUM["span_loop"] = sum(1 for n in both_eh if "*" in msr_best(n)[1]["reason"])
    # caption shortened with Nadav, 2026-10-14
    table("resultsizes",
          r"Average size and running time of the answers of both tools, over the "
          r"instances both answered. Sizes: the smallest lasso that describes the "
          r"explanation for $\MSR$ and \textsc{ExplainHyper} (\Cref{tab:msreh}); the "
          r"number of states $|A|$ of each automaton; and for \textsc{CATS} sketch, "
          r"the number of nodes $|\varphi_{\mathsf{C}}|$ of the formula it returns.",
          r"lr@{\hspace{10pt}}l@{\hspace{10pt}}rr@{\hspace{12pt}}rr",
          [r"& & & \multicolumn{2}{c}{avg.\ size} & \multicolumn{2}{c}{avg.\ $t$ (s)} \\",
           r"\cmidrule(lr){4-5}\cmidrule(lr){6-7}",
           r"comparison & instances & measure & ours & theirs & ours & theirs \\"],
          rows,
          # Hadar's label, verbatim from Overleaf zip (10), 2026-10-14 (tab_msreh refers to it)
          extra="\\label{tab:sumall}\n", placement="t")


def hd(*lines):
    """A header cell stacked over several lines, no extra package needed."""
    return r"\begin{tabular}[b]{@{}c@{}}" + r"\\".join(lines) + r"\end{tabular}"


def tab_candidates():
    """Only candidates that mention an input: randltl's simplification turns
    347 of the 818 draws into a constant or a proposition-free formula, which
    is trivially (in)sufficient and says nothing about either tool."""
    kinds = collections.Counter(EX[n]["cand_kind"] for n in INST)
    proper = [n for n in INST if EX[n]["cand_kind"] == "proper"]
    NUM.update(cand_const=kinds["const-true"] + kinds["const-false"],
               cand_true=kinds["const-true"], cand_false=kinds["const-false"],
               cand_noap=kinds["no-AP"], cand_proper=len(proper),
               cand_trivial_suff=sum(1 for n in INST if EX[n]["cand_kind"] in ("const-false", "no-AP")
                                     and EX[n]["cand_sufficient"] == "True"))
    groups = [("is sufficient", "True"), (r"is \emph{not} sufficient", "False")]
    rows = []
    for lab, v in groups + [("all", None)]:
        sel = [n for n in proper if (EX[n]["cand_sufficient"] in ("True", "False") if v is None
                                     else EX[n]["cand_sufficient"] == v)]
        fail = collections.Counter(CC[n]["cats_failed"] for n in sel)
        if v is None:
            rows.append(r"\midrule")
        rows.append([lab, len(sel), sum(1 for n in sel if CC[n]["cats_status"] == "IS_CAUSE"),
                     mean([f(EX[n]["cand_size"]) for n in sel], "{:.1f}"),
                     mean([f(CCM[n]["mtsr_s"]) for n in sel], "{:.2f}"),
                     mean([f(CC[n]["cats_s"]) for n in sel], "{:.2f}")])
        NUM[f"cand_size_med_{v}"] = med([f(EX[n]["cand_size"]) for n in sel], "{:g}")
        if v == "True":
            NUM["cand_suff_proper"] = len(sel)
            NUM["cand_suff_pc1"] = fail["PC1"]
    NUM["cand_mtsr_failed"] = sum(1 for n in proper if EX[n]["cand_sufficient"] not in ("True", "False"))
    table("candidates",
          r"$\MTSR$ against \textsc{CATS} cause checking, on the \num{"
          + str(len(proper) - NUM["cand_mtsr_failed"]) + r"} candidates $\varphi_C$ that "
          r"mention an input and on which $\MTSR$ answered. Rows are our verdict "
          r"($L(\varphi_C)\subseteq L(\MTSR)$); columns are \textsc{CATS}'s "
          r"verdict. $|\varphi_C|$ is the average candidate size in AST nodes; "
          r"times are averages, $\MTSR$ including the construction of the "
          r"automaton. \textsc{CATS} answered every candidate within the limit.",
          r"l@{\hspace{10pt}}r@{\hspace{12pt}}c@{\hspace{12pt}}r@{\hspace{12pt}}r@{\hspace{8pt}}r",
          [r"& & & & \multicolumn{2}{c}{avg.\ time (s)} \\",
           r"\cmidrule(lr){5-6}",
           r"$\MTSR$: candidate & inst. & "
           + hd(r"\textsc{CATS}:", "actual cause") + r" & avg.\ $|\varphi_C|$"
           + r" & $\MTSR$ & \textsc{CATS} \\"],
          rows)


def tab_ltl():
    """2026-10-14 (Hadar): the `is it LTL?' columns (yes/no/open) were dropped;
    the table only says whether the search found an equivalent formula within
    its size bound (yes = found, no = not found), without percentages."""
    rows = []
    b = LTLMETA["reached"]
    for g in ("1", "2", "3", None):
        sel = [n for n in INST if n in LTL and (g is None or LTL[n]["k"] == g)]
        found = [n for n in sel if LTL[n]["ltl_size"]]
        if g is None:
            rows.append(r"\midrule")
        rows.append([r"\yel{%s}" % x for x in [
                     "all" if g is None else g, len(sel), len(found), len(sel) - len(found),
                     mean([f(LTL[n]["ltl_size"]) for n in found], "{:.1f}"),
                     mean([f(LTL[n]["states"]) for n in found], "{:.1f}")]])
    NUM["ltl_found"] = len(found)
    # caption shortened with Nadav, 2026-10-14
    table("ltl",
          r"The smallest LTL formula equivalent to the $\MTSR$ automaton, searched up "
          r"to size " + str(b["1"]) + r" (" + str(b["3"]) + r" for $|\ip| = 3$). "
          r"`yes' / `no': whether one was found; the averages are over the automata "
          r"for which one was found.",
          r"rr@{\hspace{14pt}}r@{\hspace{12pt}}r@{\hspace{14pt}}r@{\hspace{12pt}}r",
          [r"& & \multicolumn{2}{c}{found} & & \\",
           r"\cmidrule(lr){3-4}",
           r"$|\ip|$ & automata & yes & no & "
           + hd("avg.", "formula size") + " & " + hd("avg.", "automaton size") + r" \\"],
          rows)


def tab_sketch_ltl():
    """How large each tool's ANSWER is, as a function of the effect depth k.

    WHAT THIS TABLE DELIBERATELY NO LONGER CLAIMS (2026-09-20)
    ----------------------------------------------------------
    It used to carry two more columns -- "our formula" (the smallest LTL
    formula equivalent to our automaton, from the size-9 search) and a count of
    the instances where that formula beat CATS's.  Both were dropped, for two
    independent reasons:

      * the number is a tautology.  On a same-language instance CATS's formula
        already IS an LTL formula for our automaton, so a formula for ours
        exists by construction and the search can only tie or improve on it.
        "Ours is never larger" says nothing about either tool.
      * \tool does not return a formula.  `--run 4` prints an automaton and a
        PNG; mtsr_ltl.py is an offline analysis script, not an output of the
        tool.  Reporting the size of something the user never receives measures
        the experiment, not the artifact.

    What is left is the one thing the sizes do say: how each tool's answer, in
    ITS OWN representation, grows with k.  The two size columns are in
    different units and the caption says so -- they are a pair of growth
    curves, not a difference.
    """
    rows = []
    ks = sorted({int(SK[n]["k"]) for n in SK})
    groups = [(k, k) for k in ks if k <= 7] + [(8, max(ks)), (None, None)]

    for lo, hi in groups:
        sel = [n for n in SK if lo is None or lo <= int(SK[n]["k"]) <= hi]
        eq = [n for n in sel if EXN[n]["mtsr_eq_cats"] == "True"]
        if lo is None:
            rows.append(r"\midrule")
        rows.append(["all" if lo is None else (lo if lo == hi else f"{lo}--{hi}"), len(sel),
                     len(eq), mean([f(EXN[n]["cats_ast_size"]) for n in eq], "{:.1f}"),
                     mean([f(SKM[n]["mtsr_states"]) for n in eq], "{:.1f}")])
    table("sketchltl",
          r"How each tool's answer grows with the depth $k$ of the effect "
          r"(\textsc{CATS}'s next-only family). `same language': our automaton "
          r"accepts exactly the language of \textsc{CATS}'s formula; the two "
          r"size columns are averages over those instances. They are in "
          r"different units --- \textsc{CATS} returns an LTL formula, we return "
          r"an automaton --- and are placed side by side to compare their "
          r"growth in $k$, not their magnitudes.",
          r"r@{\hspace{8pt}}r@{\hspace{10pt}}r@{\hspace{14pt}}r@{\hspace{12pt}}r",
          [r"& & & \multicolumn{2}{c}{avg.\ size of the answer} \\",
           r"\cmidrule(lr){4-5}",
           r"$k$ & inst. & " + hd("same", "language") + " & "
           + hd(r"\textsc{CATS}", "formula (nodes)") + " & "
           + hd("our", "automaton (states)") + r" \\"],
          rows)


def tab_nondet():
    both = [n for n in INST if MT[n]["mtsr_status"] == "OK" and CO[n]["corp_status"] == "OK"]
    rows = []
    for lab, pred in [("deterministic", lambda n: MT[n]["mtsr_det"] == "True"),
                      ("nondeterministic", lambda n: MT[n]["mtsr_det"] == "False"),
                      ("all", lambda n: True)]:
        sel = [n for n in both if pred(n)]
        if lab == "all":
            rows.append(r"\midrule")
        fg = sum(1 for n in sel if EX[n]["effect_shape"] == "FG")
        row = [lab, len(sel), f"{fg} ({pct(fg, len(sel))})",
               mean([f(MT[n]["mtsr_s"]) for n in sel], "{:.2f}"),
               mean([f(CO[n]["corp_s"]) for n in sel], "{:.2f}"),
               mean([f(MT[n]["mtsr_states"]) for n in sel], "{:.1f}"),
               mean([f(CO[n]["corp_states"]) for n in sel], "{:.1f}")]
        rows.append(row)
        NUM[f"nondet_{lab}"] = row[1:]
    fail = [n for n in INST if MT[n]["mtsr_status"] != "OK"]
    NUM["mtsr_fail"] = len(fail)
    table("nondet",
          r"Instances split by whether the automaton $\MTSR$ returns is "
          r"deterministic, over the instances both tools answered (means). "
          r"`$\mathsf{F}\mathsf{G}$ effect': the effect has that shape. "
          r"This column says where the nondeterminism comes from: "
          r"$\mathsf{F}\mathsf{G}$ languages in general have no deterministic "
          r"B\"uchi automaton, and they make up most nondeterministic results "
          r"but few deterministic ones, so the nondeterminism is forced by the "
          r"effect, not introduced by our construction.",
          r"lr@{\hspace{10pt}}r@{\hspace{10pt}}rr@{\hspace{10pt}}rr",
          [r"& & & \multicolumn{2}{c}{time (s)} & \multicolumn{2}{c}{states} \\",
           r"\cmidrule(lr){4-5}\cmidrule(lr){6-7}",
           r"$\MTSR$ automaton & inst. & " + hd(r"$\mathsf{F}\mathsf{G}$", "effect")
           + r" & $\MTSR$ & \textsc{CORP} & $\MTSR$ & \textsc{CORP} \\"],
          rows)


def legend_ip(ax, loc=None):
    ax.legend()
    top_legend(ax.figure, ax)


# ---- CATS-style scatter (Hadar, 2026-10-05) --------------------------------
# Fig. 2 of the CATS paper: small light points, no outline, so that overlapping
# points add up to a darker colour and dense regions read as dark.  Linear
# system-size axis and logarithmic time axis, as there.  Replaces the earlier
# black, white-ringed markers that hid each other.
CATS_ALPHA, CATS_S = 0.35, 7
TO_Y = BUDGET * 1.9


def density_scatter(ax, pts, color, marker="o", label=None, x_log=False):
    """pts: list of (x, seconds_or_None). None = no answer (drawn at TO)."""
    ok = [p for p in pts if p[1] is not None]
    to = [p for p in pts if p[1] is None]
    ax.scatter([p[0] for p in ok], [max(p[1], 0.05) for p in ok], s=CATS_S,
               marker=marker, color=color, alpha=CATS_ALPHA, linewidths=0,
               label=label)
    if to:
        ax.scatter([p[0] for p in to], [TO_Y] * len(to), s=CATS_S + 3,
                   marker="x", color=color, alpha=CATS_ALPHA + 0.15, linewidths=0.7)
    return len(ok), len(to)


def time_axis(ax, xlabel, xmax=None):
    ax.set_yscale("log")
    ax.axhline(BUDGET, color=MUTED, lw=0.6, ls=(0, (3, 2)))
    ax.set_ylim(0.08, 400)
    ax.set_yticks([0.1, 1, 10, 100, TO_Y])
    ax.set_yticklabels(["0.1", "1", "10", "100", "TO"])
    ax.set_xlabel(xlabel)
    ax.set_ylabel("time (s)")
    if xmax:
        ax.set_xlim(0, xmax)


def fig_msr_size():
    """Fig. 3: MSR and ExplainHyper in ONE plot (Hadar: combine the two panels),
    on the instances ExplainHyper supports.  Fig. 4: MSR alone on all 818."""
    expr = [n for n in INST if EH[n]["status"] != "UNSUPPORTED"]

    def msr(n):
        return best(n)[0] if best(n) and best(n)[0] < BUDGET - 1 else None

    xmax = max(q(n) for n in INST) + 3
    fig, ax = plt.subplots(figsize=(TW * 0.8, 2.1))
    density_scatter(ax, [(q(n), msr(n)) for n in expr], BLUE, "o", "MSR (TraceMSR)")
    density_scatter(ax, [(q(n), f(EH[n]["wall_s"]) if EH[n]["status"] in ("OK", "NO_CAUSE") else None)
                         for n in expr], ORANGE, "^", "ExplainHyper")
    time_axis(ax, r"system size $|T|$", xmax)
    leg = ax.legend(loc="lower right", markerscale=1.6, handletextpad=0.2)
    for h in leg.legend_handles:
        h.set_alpha(0.9)
    NUM["eh_expressible"] = len(expr)
    # caption shortened, Hadar's comment removed (Nadav, 2026-10-14)
    save(fig, "msr_size",
         r"$\MSR$ and \textsc{ExplainHyper}: running time against the number of "
         r"states of the system on the " + str(len(expr)) + r" instances that "
         r"\textsc{ExplainHyper} supports (one point per instance and tool; darker "
         r"= more points; crosses = timeouts).", "msr-size", width=r"0.85\textwidth")

    fig, ax = plt.subplots(figsize=(TW * 0.8, 2.1))
    density_scatter(ax, [(q(n), msr(n)) for n in INST], BLUE)
    time_axis(ax, r"system size $|T|$", xmax)
    save(fig, "msr_size_all",
         r"$\MSR$ running time against the number of states of the system on "
         r"all " + str(len(INST)) + r" instances, one point per instance "
         r"(darker = more points; crosses = no answer from either model "
         r"checker within 120\,s)"
         # Hadar's comment, verbatim from Overleaf zip (10), 2026-10-14
         r"\hadar{timeout for finding the counter example? or for finding the reason?}",
         "msr-size-all", width=r"0.85\textwidth")


def fig_msr_mtsr_size():
    """Hadar, 2026-10-14: Fig. msr-size-all and Fig. mtsr-corp-size side by
    side, one figure with panels (a) MSR and (b) MTSR, same axes."""
    xmax = max(q(n) for n in INST) + 3
    fig, axs = plt.subplots(1, 2, figsize=(TW, 2.0), sharey=True)
    density_scatter(axs[0], [(q(n), best(n)[0] if best(n) and best(n)[0] < BUDGET - 1 else None)
                             for n in INST], BLUE)
    density_scatter(axs[1], [(q(n), f(MT[n]["mtsr_s"]) if MT[n]["mtsr_status"] == "OK" else None)
                             for n in INST], BLUE)
    for ax, t in zip(axs, ("(a) MSR", "(b) MTSR")):
        time_axis(ax, r"system size $|T|$", xmax)
        ax.set_title(t, fontsize=7.5)
    axs[1].set_ylabel("")
    fig.tight_layout(w_pad=1.0)
    save(fig, "msr_mtsr_size",
         r"\yel{Running time against the number of states of the system on all "
         r"" + str(len(INST)) + r" instances, one point per instance (darker = "
         r"more points): (a) $\MSR$, where a cross means no answer from either "
         r"model checker within 120\,s; (b) $\MTSR$, where a cross means a "
         r"timeout or out of memory.}"
         # Hadar's comment on the MSR panel's former figure, Overleaf zip (10)
         r"\hadar{timeout for finding the counter example? or for finding the reason?}",
         "msr-mtsr-size")


def fig_msr_mtsr_size_variants():
    """Two layouts of the merged MSR/MTSR figure (Nadav, 2026-10-14):
    v1 side by side with more space, v2 one below the other."""
    xmax = max(q(n) for n in INST) + 3
    pts = [[(q(n), best(n)[0] if best(n) and best(n)[0] < BUDGET - 1 else None) for n in INST],
           [(q(n), f(MT[n]["mtsr_s"]) if MT[n]["mtsr_status"] == "OK" else None) for n in INST]]
    # caption with Nadav, 2026-10-14; answers Hadar's question (timeout for the
    # reason, not a counterexample: pi is given), so her comment was removed
    # wording as submitted (Overleaf zip 21, Hadar)
    cap = (r"Running time w.r.t. system size (number of states) on the "
           + str(len(INST)) + r" instances with $|\ip| \leq " + str(max(ip(n) for n in INST))
           + r"$. A cross is an instance without an answer: (a) no $\MSR$ found "
           r"within the timeout, (b) no $\MTSR$ within the timeout or the memory limit.")
    for name, shape, size in (("msr_mtsr_size_v1", (1, 2), (TW * 1.1, 2.45)),
                              ("msr_mtsr_size_v2", (2, 1), (TW * 0.8, 3.9))):
        fig, axs = plt.subplots(*shape, figsize=size, sharey=True)
        for ax, p, t in zip(axs, pts, ("(a) MSR", "(b) MTSR")):
            density_scatter(ax, p, BLUE)
            time_axis(ax, r"system size $|T|$", xmax)
            ax.set_title(t, fontsize=7.5)
        if shape == (1, 2):
            fig.tight_layout(w_pad=4.0)
        else:
            axs[0].set_xlabel("")
            fig.tight_layout(h_pad=1.2)
        save(fig, name, cap, name.replace("_", "-"),
             width=r"\textwidth" if shape == (1, 2) else r"0.85\textwidth",
             placement="!t" if shape == (1, 2) else "htbp")  # Overleaf zip (12), Hadar, 2026-10-15


def fig_cats_check_time():
    """Replaces the appendix table of CATS check verdicts (Hadar, 2026-10-05):
    one point per candidate, x = CATS time, y = MTSR time.  2026-10-15 (Hadar,
    zip 16: "can we have them as x at the top/left for each tool"): candidates
    on which a tool gave no answer (timeout / error) are crosses on that tool's
    edge -- MTSR's at the top, CATS's at the right, both in the corner."""
    prop = [n for n in INST if EX.get(n, {}).get("cand_kind") == "proper"]
    cats_ok = lambda n: CC.get(n, {}).get("cats_status") in ("IS_CAUSE", "NO_CAUSE")
    mtsr_ok = lambda n: CCM.get(n, {}).get("mtsr_status") == "OK"
    sel = [n for n in prop if cats_ok(n) and mtsr_ok(n)]
    miss = [n for n in prop if not (cats_ok(n) and mtsr_ok(n))]
    xs = [f(CC[n]["cats_s"]) for n in sel]
    ys = [f(CCM[n]["mtsr_s"]) for n in sel]
    fig, ax = plt.subplots(figsize=(TW * 0.55, 2.3))
    ax.scatter(xs, ys, s=CATS_S, color=BLUE, alpha=CATS_ALPHA, linewidths=0)
    ax.set_xscale("log"); ax.set_yscale("log")
    lo, hi = 0.1, max(xs + ys) * 1.3
    na = hi * 2.2                                   # the "no answer" band
    mx = [na if not cats_ok(n) else f(CC[n]["cats_s"]) for n in miss]
    my = [na if not mtsr_ok(n) else f(CCM[n]["mtsr_s"]) for n in miss]
    ax.scatter(mx, my, s=14, marker="x", color=BLUE, alpha=CATS_ALPHA, linewidths=0.8, zorder=3)
    ax.axhline(hi * 1.5, color="0.6", lw=0.4, ls=":"); ax.axvline(hi * 1.5, color="0.6", lw=0.4, ls=":")
    ax.set_xlim(lo, na * 1.6); ax.set_ylim(lo, na * 1.6)
    diag(ax, lo, hi)
    ticks = [0.1, 1, 10, 100, na]
    labels = [r"$10^{-1}$", r"$10^{0}$", r"$10^{1}$", r"$10^{2}$", "n/a"]
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_ticks(ticks); axis.set_ticklabels(labels); axis.set_minor_locator(plt.NullLocator())
    ax.set_xlabel("CATS time (s)")
    ax.set_ylabel("MTSR time (s)")
    ax.set_aspect("equal")
    NUM["catscheck_scatter_n"] = len(sel)
    NUM["catscheck_mtsr_faster"] = sum(1 for x, y in zip(xs, ys) if y < x)
    NUM["catscheck_noanswer"] = len(miss)
    save(fig, "cats_check_time",
         r"$\MTSR$ against \textsc{CATS} cause checking on the " + str(len(prop)) +
         r" random candidates, one point per candidate. Below the diagonal, $\MTSR$ is "
         r"faster. Crosses (n/a): the tool gave no answer within 120\,s.", "cats-check-time",
         width=r"0.55\textwidth", placement="!t")


def fig_msr_factors():
    fig, axs = plt.subplots(1, 2, figsize=(TW, 2.0), sharey=True)
    be = lambda n: best(n)[0] if best(n) and best(n)[0] < BUDGET - 1 else None
    time_scatter(axs[0], [(lasso(n) + random.uniform(-.2, .2), be(n), ip(n)) for n in INST],
                 "lasso length $|u|+|v|$")
    ax = axs[1]
    ax.set_yscale("log")
    data = [[best(n)[0] for n in INST if ip(n) == g and best(n)] for g in (1, 2, 3)]
    bp = ax.boxplot(data, positions=[1, 2, 3], widths=0.5, patch_artist=True, showfliers=False,
                    medianprops=dict(color=INK, lw=0.8), whiskerprops=dict(color=MUTED, lw=0.6),
                    capprops=dict(color=MUTED, lw=0.6), boxprops=dict(lw=0.6, color=MUTED))
    for patch, g in zip(bp["boxes"], (1, 2, 3)):
        patch.set_facecolor(IPC[g])
        patch.set_alpha(0.55)
    for g in (1, 2, 3):
        ys = data[g - 1]
        ax.scatter(jitter([g] * len(ys), 0.12), [max(y, .05) for y in ys], s=3, color=IPC[g], alpha=0.5, lw=0)
    ax.axhline(BUDGET, color=MUTED, lw=0.6, ls=(0, (3, 2)))
    ax.set_xticks([1, 2, 3])
    ax.set_xlabel(r"input propositions $|I|$")
    axs[0].set_ylabel("MSR time (s)")
    legend_ip(axs[0], "upper right")
    save(fig, "msr_factors",
         r"The other two drivers of $\MSR$ runtime (either backend): the length "
         r"of the lasso $\pit$, and $|\ip|$ (answered instances; box = "
         r"quartiles). Only $|\ip|$ separates the populations outright.",
         "msr-factors")


def diag(ax, lo, hi):
    ax.plot([lo, hi], [lo, hi], color=MUTED, lw=0.6, ls=(0, (3, 2)), zorder=0)


def fig_msr_eh():
    both = [n for n in INST if EH[n]["status"] == "OK" and best(n)]
    fig, axs = plt.subplots(1, 2, figsize=(TW, 2.1))
    ax = axs[0]
    for g in (1, 2, 3):
        s = [n for n in both if ip(n) == g]
        ax.scatter([f(EH[n]["wall_s"]) for n in s], [best(n)[0] for n in s], s=9, marker=IPM[g],
                   facecolor=IPC[g], edgecolor="white", lw=0.3, alpha=0.8, label=f"$|I|={g}$")
    ax.set_xscale("log"); ax.set_yscale("log")
    diag(ax, 0.1, 150)
    ax.set_xlabel("ExplainHyper time (s)"); ax.set_ylabel("MSR time (s)")
    ax.legend()
    ax = axs[1]
    mins = [n for n in both if minimal_reason(n) is not None]
    for g in (1, 2, 3):
        s = [n for n in mins if ip(n) == g]
        ax.scatter(jitter([f(EH[n]["cause_min"]) for n in s], .15), jitter([minimal_reason(n) for n in s], .15),
                   s=9, marker=IPM[g], facecolor=IPC[g], edgecolor="white", lw=0.3, alpha=0.8)
    diag(ax, 0, 12)
    ax.set_xlabel(r"smallest actual cause $|C|$"); ax.set_ylabel(r"minimal reason $|R|$")
    fig.tight_layout(w_pad=0.8)
    top_legend(fig, axs[0], y=0.98)
    save(fig, "msr_eh",
         r"$\MSR$ against \textsc{ExplainHyper} per instance (both answered; "
         r"dashed = equal). Left: time; right: size, jittered. Points above "
         r"the diagonal are where ours is slower / larger.", "msr-eh")


def fig_mtsr_corp_size():
    """Only the MTSR panel (Hadar, 2026-10-05), in the CATS style of Fig. 3."""
    xmax = max(q(n) for n in INST) + 3
    fig, ax = plt.subplots(figsize=(TW * 0.8, 2.1))
    density_scatter(ax, [(q(n), f(MT[n]["mtsr_s"]) if MT[n]["mtsr_status"] == "OK" else None)
                         for n in INST], BLUE)
    time_axis(ax, r"system size $|T|$", xmax)
    save(fig, "mtsr_corp_size",
         r"$\MTSR$ running time against the number of states of the system on "
         r"all " + str(len(INST)) + r" instances, one point per instance "
         r"(darker = more points; crosses = timeout or out of memory).",
         "mtsr-corp-size", width=r"0.85\textwidth")


def fig_aut_size():
    fig, axs = plt.subplots(1, 3, figsize=(TW, 1.9))
    both = [n for n in INST if MT[n]["mtsr_status"] == "OK" and CO[n]["corp_status"] == "OK"]
    for ax, key, src, t in ((axs[0], "mtsr_states", MT, "MTSR"), (axs[1], "corp_states", CO, "CORP")):
        for g in (1, 2, 3):
            s = [n for n in both if ip(n) == g]
            ax.scatter(jitter([q(n) for n in s], .1), [f(src[n][key]) for n in s], s=8, marker=IPM[g],
                       facecolor=IPC[g], edgecolor="white", lw=0.3, alpha=0.8, label=f"$|I|={g}$")
        ax.set_yscale("log"); logq(ax); ax.set_xlabel("system states $|Q|$"); ax.set_title(t, fontsize=7.5)
    axs[0].set_ylabel("result automaton $|Q|$")
    top = max(max(f(MT[n]["mtsr_states"]), f(CO[n]["corp_states"])) for n in both) * 1.3
    axs[0].set_ylim(0.8, top); axs[1].set_ylim(0.8, top)
    axs[0].legend()
    ax = axs[2]
    for det, c, lab in (("True", BLUE, "MTSR det."), ("False", ORANGE, "MTSR nondet.")):
        s = [n for n in both if MT[n]["mtsr_det"] == det]
        ax.scatter(jitter([f(CO[n]["corp_states"]) for n in s], .15), jitter([f(MT[n]["mtsr_states"]) for n in s], .15),
                   s=8, color=c, alpha=0.7, lw=0, label=lab, marker="o" if det == "True" else "s")
    ax.set_xscale("log"); ax.set_yscale("log")
    diag(ax, 1, 300)
    ax.set_xlabel("CORP $|Q|$"); ax.set_ylabel("MTSR $|Q|$")
    ax.legend(loc="upper left", handletextpad=0.1, borderaxespad=0.1)
    fig.tight_layout(w_pad=0.6)
    top_legend(fig, axs[0], y=0.98)
    save(fig, "aut_size",
         r"Size of the synthesized automaton. Left and middle: against system "
         r"size, for $\MTSR$ and \textsc{CORP}. Right: per instance, ours "
         r"against theirs (dashed = equal size; below = ours smaller), split by "
         r"whether our witness is deterministic.", "aut-size")


def fig_cats_check_size():
    fig, axs = plt.subplots(1, 2, figsize=(TW, 1.9), sharey=True)
    time_scatter(axs[0], [(q(n), f(CC[n]["cats_s"]) if CC[n]["cats_status"] != "TIMEOUT" else None, ip(n)) for n in INST],
                 "system states $|Q|$", title="CATS --check")
    time_scatter(axs[1], [(q(n), f(CCM[n]["mtsr_s"]) if CCM[n]["mtsr_status"] == "OK" else None, ip(n)) for n in INST],
                 "system states $|Q|$", title="MTSR")
    axs[0].set_ylabel("time (s)")
    legend_ip(axs[0], "upper right")
    save(fig, "cats_check_size",
         r"\textsc{CATS}'s Fig.~2 on our instance set: cause-checking time "
         r"against system size, next to $\MTSR$ synthesis on the same "
         r"instances.", "cats-check-size")


def fig_candidates():
    fig, axs = plt.subplots(1, 2, figsize=(TW, 1.9))
    ax = axs[0]
    sizes = sorted({int(EX[n]["cand_size"]) for n in INST if EX[n]["cand_size"]})
    cats = [("const", "constant ($\\mathit{true}$/$\\mathit{false}$/no AP)", MUTED),
            ("suff", r"proper, sufficient", BLUE), ("ins", r"proper, not sufficient", ORANGE)]

    def cat(n):
        if EX[n]["cand_kind"] != "proper":
            return "const"
        return "suff" if EX[n]["cand_sufficient"] == "True" else "ins"
    bottom = [0] * len(sizes)
    for key, lab, c in cats:
        h = [sum(1 for n in INST if EX[n]["cand_size"] and int(EX[n]["cand_size"]) == s and cat(n) == key) for s in sizes]
        ax.bar(sizes, h, bottom=bottom, color=c, width=0.8, label=lab, lw=0)
        bottom = [a + b for a, b in zip(bottom, h)]
    ax.set_xlabel(r"tested candidate size $|\varphi_C|$ (AST nodes)")
    ax.set_ylabel("instances")
    ax.legend()
    ax = axs[1]
    for key, lab, c in cats:
        s = [n for n in INST if EX[n]["cand_size"] and cat(n) == key and CC[n]["cats_status"] != "TIMEOUT"]
        ax.scatter(jitter([int(EX[n]["cand_size"]) for n in s], .25), [f(CC[n]["cats_s"]) for n in s],
                   s=6, color=c, alpha=0.7, lw=0)
    ax.set_yscale("log")
    ax.set_xlabel(r"$|\varphi_C|$"); ax.set_ylabel("CATS --check time (s)")
    fig.tight_layout(w_pad=0.5)
    top_legend(fig, axs[0], y=0.98)
    save(fig, "candidates",
         r"The candidate causes handed to \textsc{CATS} \texttt{--check}. "
         r"Left: size distribution after \texttt{randltl}'s simplification, "
         r"split by our sufficiency verdict (the spike at 1 is constants). "
         r"Right: \textsc{CATS} time against candidate size.", "candidates")


def fig_sketch():
    fig, axs = plt.subplots(1, 3, figsize=(TW, 1.9))
    ax = axs[0]
    ks = sorted({int(SK[n]["k"]) for n in SK})
    for key, src, c, lab, m, dx in (("cats_s", SK, ORANGE, "CATS sketch", "s", .15), ("mtsr_s", SKM, BLUE, "MTSR", "o", -.15)):
        pts = [(int(SK[n]["k"]) + dx, f(src[n][key])) for n in SK if f(src[n][key]) is not None
               and (key != "cats_s" or SK[n]["cats_status"] == "FOUND")]
        ax.scatter(jitter([p[0] for p in pts], .08), [p[1] for p in pts], s=8, color=c, marker=m, alpha=0.75, lw=0, label=lab)
    ax.set_yscale("log"); ax.set_xticks(ks); ax.set_xlabel(r"effect depth $k$ ($X^k\,\mathit{error}$)")
    ax.set_ylabel("time (s)"); ax.legend()
    ax = axs[1]
    for key, src, c, lab, m, dx in (("sys_states", SK, MUTED, None, "o", 0),):
        pass
    for src, key, c, m, lab in ((SK, "cats_s", ORANGE, "s", "CATS"), (SKM, "mtsr_s", BLUE, "o", "MTSR")):
        pts = [(int(SK[n]["sys_states"]), f(src[n][key])) for n in SK if f(src[n][key]) is not None
               and (key != "cats_s" or SK[n]["cats_status"] == "FOUND")]
        ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=8, color=c, marker=m, alpha=0.75, lw=0, label=lab)
    ax.set_yscale("log"); ax.set_xlabel("system states $|Q|$")
    ax = axs[2]
    eq = [n for n in SK if EXN[n]["mtsr_eq_cats"] == "True"]
    neq = [n for n in SK if EXN[n]["mtsr_eq_cats"] == "False"]
    ax.scatter(jitter([f(EXN[n]["cats_ast_size"]) for n in eq], .2), jitter([f(SKM[n]["mtsr_states"]) for n in eq], .2),
               s=8, color=BLUE, lw=0, alpha=0.75, label=r"$L(\mathrm{MTSR}) = L(\varphi_C)$")
    ax.scatter(jitter([f(EXN[n]["cats_ast_size"]) for n in neq], .2), jitter([f(SKM[n]["mtsr_states"]) for n in neq], .2),
               s=10, color=ORANGE, marker="s", lw=0, alpha=0.85, label=r"$L(\varphi_C) \subsetneq L(\mathrm{MTSR})$")
    ax.set_xlabel(r"CATS cause $|\varphi_C|$"); ax.set_ylabel("MTSR $|Q|$")
    ax.legend(loc="upper left", handletextpad=0.1, borderaxespad=0.1)
    fig.tight_layout(w_pad=0.5)
    top_legend(fig, axs[0], ncol=2, y=0.98)
    save(fig, "sketch",
         r"\textsc{CATS} sketch search against $\MTSR$ on the next-only family. "
         r"Left: time against the depth $k$ of the effect; middle: against "
         r"system size; right: result size, their formula against our automaton.",
         "sketch")


def fig_ltl():
    fig, axs = plt.subplots(1, 3, figsize=(TW, 1.9))
    found = [n for n in INST if n in LTL and LTL[n]["ltl_size"]]
    ax = axs[0]
    sizes = list(range(1, 10))
    bottom = [0] * len(sizes)
    for g in (1, 2, 3):
        h = [sum(1 for n in found if LTL[n]["k"] == str(g) and int(LTL[n]["ltl_size"]) == s) for s in sizes]
        ax.bar(sizes, h, bottom=bottom, color=IPC[g], width=0.8, lw=0, label=f"$|I|={g}$")
        bottom = [a + b for a, b in zip(bottom, h)]
    ax.set_xlabel(r"smallest LTL $|\varphi|$"); ax.set_ylabel("instances"); ax.set_xticks(sizes)
    ax.legend()
    ax = axs[1]
    for g in (1, 2, 3):
        s = [n for n in found if LTL[n]["k"] == str(g)]
        ax.scatter(jitter([f(LTL[n]["states"]) for n in s], .2), jitter([f(LTL[n]["ltl_size"]) for n in s], .2),
                   s=8, marker=IPM[g], facecolor=IPC[g], edgecolor="white", lw=0.3, alpha=0.8)
    ax.set_xlabel(r"MTSR automaton $|Q|$"); ax.set_ylabel(r"smallest LTL $|\varphi|$")
    ax = axs[2]
    for g in (1, 2, 3):
        s = [n for n in found if LTL[n]["k"] == str(g)]
        ax.scatter(jitter([f(EX[n]["effect_size"]) for n in s], .2), jitter([f(LTL[n]["ltl_size"]) for n in s], .2),
                   s=8, marker=IPM[g], facecolor=IPC[g], edgecolor="white", lw=0.3, alpha=0.8)
    diag(ax, 1, 9)
    fig.tight_layout(w_pad=0.5)
    top_legend(fig, axs[0], y=0.98)
    ax.set_xlabel(r"effect size $|\varphi|$ (over outputs)"); ax.set_ylabel(r"smallest LTL $|\varphi|$")
    save(fig, "ltl",
         r"$\MTSR$ automata that have an LTL form found by enumeration: the "
         r"size of the smallest equivalent formula (left), against the "
         r"automaton size (middle), and against the size of the effect it "
         r"explains (right, dashed = equal).", "ltl")


def fig_nondet():
    fig, axs = plt.subplots(1, 2, figsize=(TW, 1.9))
    ok = [n for n in INST if MT[n]["mtsr_status"] == "OK" and CO[n]["corp_status"] == "OK"]
    groups = [("det.\nDBA", lambda n: MT[n]["mtsr_det"] == "True" and MT[n]["mtsr_dba_exists"] == "True"),
              ("det.\nno DBA", lambda n: MT[n]["mtsr_det"] == "True" and MT[n]["mtsr_dba_exists"] == "False"),
              ("nondet.", lambda n: MT[n]["mtsr_det"] == "False")]
    for ax, (mk, ck, yl) in zip(axs, (("mtsr_s", "corp_s", "time (s)"), ("mtsr_states", "corp_states", "automaton $|Q|$"))):
        for i, (lab, pred) in enumerate(groups):
            s = [n for n in ok if pred(n)]
            ax.scatter(jitter([i - 0.18] * len(s), .1), [f(MT[n][mk]) for n in s], s=4, color=BLUE, lw=0, alpha=0.5,
                       label="MTSR" if i == 0 else None)
            ax.scatter(jitter([i + 0.18] * len(s), .1), [f(CO[n][ck]) for n in s], s=4, color=ORANGE, lw=0, alpha=0.5,
                       marker="s", label="CORP" if i == 0 else None)
            for x, src, key in ((i - 0.18, MT, mk), (i + 0.18, CO, ck)):
                m = st.median([f(src[n][key]) for n in s])
                ax.plot([x - 0.14, x + 0.14], [m, m], color=INK, lw=1)
        ax.set_yscale("log"); ax.set_xticks(range(3)); ax.set_xticklabels([g[0] for g in groups])
        ax.set_ylabel(yl)
    axs[0].legend(markerscale=2)
    top_legend(fig, axs[0], ncol=2)
    save(fig, "nondet",
         r"$\MTSR$ and \textsc{CORP} split by the kind of witness $\MTSR$ "
         r"returns (black bar = median). Nondeterminism is where \textsc{CORP} "
         r"is faster; the size advantage of $\MTSR$ holds in every group.",
         "nondet")


# ================================================================== MAIN ====
def main():
    random.seed(0)
    # only the floats appendix_data.tex \inputs (cut 2026-09-18); the other
    # tab_*/fig_* functions stay here and can still be called by hand
    # 2026-10-05 (Hadar): tab_candidates -> fig_cats_check_time; tab_sketch_ltl
    # merged into paper_tables.tab_catssketch; tab_nondet removed.
    for t in (tab_resultsizes,) if SKIP_LTL else (tab_resultsizes, tab_ltl):
        t()
    for g in (fig_msr_size, fig_mtsr_corp_size, fig_cats_check_time):
        random.seed(1)
        g()
    json.dump(NUM, open(os.path.join(STATS, "numbers.json"), "w"), indent=1, default=str)
    print(json.dumps(NUM, indent=1, default=str))


if __name__ == "__main__":
    main()
