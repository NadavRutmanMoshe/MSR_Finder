#!/usr/bin/env python3
r"""
cats_handcrafted -- TraceMSR on the hand-crafted instances of CATS's Table 1.

    ~/micromamba/envs/spot/bin/python3 bench/paper/cats_handcrafted.py --run
    ~/micromamba/envs/spot/bin/python3 bench/paper/cats_handcrafted.py --table

Hadar (2026-10-05): "like in CATS Table 1, a comparison with CATS on those
specific examples".  CATS's Table 1 (Beutner, Finkbeiner, Frenkel, Metzger,
ATVA'23, Sec. 5.1) lists 12 hand-crafted cause-checking instances; its artifact
ships 11 of them in `examples/` (Example 7 is the sketching example of Sec. 6
and has no checking file).  Each file holds a system, a lasso, an effect and
a candidate cause phi_C.

Per instance:
  CATS   --check on CATS's own file: is phi_C an actual cause?  (+ time)
  MTSR   the automaton (states), and whether phi_C IS the MTSR -- the tool's
         own --check (cause_check.py: L(phi_C) restricted to the system's
         input words equals L(MTSR); MTSR / NOT_MTSR, nothing in between,
         Nadav 2026-10-11), including its exact handling of CATS's
         existential QPTL quantifier.  The time is the tool's MTSR plus the
         check, in one process (mtsr_min.py --cause).
  MSR    the minimal sufficient reason on the same lasso (time, |R|)

The TraceMSR side runs on the copies in MSR_Finder/examples (same content; the
names follow the paper numbering, e.g. CATS's example_3_odd is "Example 6, odd").
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import paths  # noqa: E402
BENCH_SCRIPTS = paths.BENCH_SCRIPTS
TOOL_SCRIPTS = paths.TOOL_SCRIPTS
for p in (BENCH_SCRIPTS, TOOL_SCRIPTS, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import cats_util       # noqa: E402  (CATS runner)
import cause_check     # noqa: E402  (the tool's own --check)

SPOT_PY = paths.SPOT_PY
CATS_EX = os.path.join(paths.TOOLS, "CATS", "examples")
OURS_EX = os.path.join(paths.REPO, "examples")
MTSR_MIN = os.path.join(BENCH_SCRIPTS, "mtsr_min.py")
OUT_CSV = os.path.join(paths.CURATED, "cats_handcrafted.csv")
TEX_OUT = os.path.join(paths.TABLES, "tab_catshand.tex")
WORK = os.path.join(paths.OUT, "work", "cats_handcrafted")

#: (row label as in CATS Table 1, CATS's file, our copy, CATS's published Res, t)
CATALOGUE = [
    ("Spurious Arbiter", "atva22_arbiter_spurious", "atva2022_arbiter_spurious", "no", 0.6),
    ("Arbiter simple", "atva22_arbiter_simple", "atva2022_arbiter_simple", "yes", 1.2),
    ("Arbiter", "atva22_arbiter", "atva2022_arbiter", "yes", 9.7),
    ("Example 1", "example_1", "example_1", "yes", 0.9),
    ("Example 1, mod", "example_1_mod", "example_1_mod", "no", 1.0),
    ("Example 6, odd", "example_3_odd", "example_6_odd", "yes", 1.4),
    ("Example 6, globally", "example_3_globally", "example_6_globally", "no", 0.7),
    ("Example 8", "example_5", "example_8", "yes", 1.1),
    ("Example 8, mod", "example_5_mod", "example_8_mod", "no", 1.2),
    ("TP Left", "tp_left", "atva2022_tp_left", "no", 0.3),
    ("TP Right", "tp_right", "atva2022_tp_right", "yes", 1.1),
]

FIELDS = ["label", "cats_file", "ours_file", "cause", "paper_res", "paper_s",
          "cats_status", "cats_failed", "cats_stages", "cats_s",
          "mtsr_status", "mtsr_states", "mtsr_s", "verdict", "is_mtsr",
          "msr_status", "msr_size", "msr_s"]


def _sub(cmd, timeout):
    """`timeout --signal=TERM`: mtsr_min and msr_cause unwind their solver on
    SIGTERM; a SIGKILL would orphan it (see bench/README.md, "Leaked solvers")."""
    t0 = time.time()
    p = subprocess.run(["timeout", "--signal=TERM", "--kill-after=30",
                        str(timeout)] + cmd, capture_output=True, text=True)
    return p, time.time() - t0, p.returncode == 124


def run_mtsr(path, timeout, hoa, cause):
    p, dt, killed = _sub([SPOT_PY, MTSR_MIN, path, "--json", "--save-hoa", hoa,
                          "--cause", cause], timeout)
    if killed:
        return {"mtsr_status": "TIMEOUT", "mtsr_s": round(dt, 3)}
    try:
        j = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:                                        # noqa: BLE001
        return {"mtsr_status": "ERROR", "mtsr_s": round(dt, 3)}
    return {"mtsr_status": j.get("status", "OK"), "mtsr_states": j.get("states", ""),
            "verdict": j.get("cause_verdict", ""), "mtsr_s": round(dt, 3)}


def run_msr(path, timeout):
    cmd = [SPOT_PY, os.path.join(_HERE, "msr_cause.py"), path,
           "--timeout", str(timeout)]
    p, dt, killed = _sub(cmd, timeout + 30)
    if killed:
        return {"msr_status": "TIMEOUT", "msr_s": round(dt, 3)}
    try:
        j = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:                                        # noqa: BLE001
        return {"msr_status": "ERROR", "msr_s": round(dt, 3)}
    ok = j.get("status") in ("SAT_MSR", "SAT_SR")
    return {"msr_status": j.get("status", ""), "msr_size": j.get("size", "") if ok else "",
            "msr_s": round(dt, 3)}


def run(timeout, with_msr=False):
    os.makedirs(WORK, exist_ok=True)
    rows = []
    for label, cfile, ofile, pres, ps in CATALOGUE:
        cpath = os.path.join(CATS_EX, cfile + ".txt")
        opath = os.path.join(OURS_EX, ofile + ".txt")
        cause = cause_check.parse_cause(cpath)   # phi_C as CATS states it
        row = {"label": label, "cats_file": cfile, "ours_file": ofile,
               "cause": cause, "paper_res": pres, "paper_s": ps}
        print(f"[{label}] ", end="", flush=True)

        r = cats_util.run("check", cpath, timeout)
        row.update({"cats_status": r["status"], "cats_failed": r.get("failed", ""),
                    "cats_stages": r.get("stages", ""), "cats_s": r["wall_s"]})
        print(f"cats={r['status']}/{r['wall_s']}s ", end="", flush=True)

        hoa = os.path.join(WORK, ofile + ".mtsr.hoa")
        if os.path.exists(hoa):
            os.remove(hoa)
        row.update(run_mtsr(opath, timeout, hoa, cause))
        row["is_mtsr"] = {"MTSR": 1, "NOT_MTSR": 0}.get(row.get("verdict"), "")
        print(f"mtsr={row['mtsr_status']}/{row.get('mtsr_states')}/{row['mtsr_s']}s "
              f"{row['verdict']} ", end="", flush=True)

        if with_msr:            # not in the table any more (Hadar, 2026-10-07)
            row.update(run_msr(opath, timeout))
            print(f"msr={row['msr_status']}/{row.get('msr_size')}/{row['msr_s']}s")
        else:
            print()
        rows.append(row)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"\nwrote {OUT_CSV}")


def _t(x, status, ok):
    if status not in ok:
        return "TO" if status == "TIMEOUT" else "--"
    return f"{float(x):.2f}"


YES, NO = r"\checkmark", r"$\times$"


def table():
    rows = list(csv.DictReader(open(OUT_CSV, newline="")))
    lines = []
    for r in rows:
        cats = {"IS_CAUSE": YES, "NO_CAUSE": NO}.get(r["cats_status"], "--")
        suff = {"1": YES, "0": NO}.get(r["is_mtsr"], "--")
        lines.append(" & ".join([
            r["label"], cats, _t(r["cats_s"], r["cats_status"], ("IS_CAUSE", "NO_CAUSE")),
            suff, _t(r["mtsr_s"], r["mtsr_status"], ("OK",)),
            r["mtsr_states"] or "--",
        ]) + r" \\")      # MSR columns dropped (Hadar, 2026-10-07)
    tex = TEX % "\n".join(lines)
    with open(TEX_OUT, "w") as f:
        f.write(tex)
    print(f"wrote {TEX_OUT}")


TEX = r"""%% generated by bench/paper/cats_handcrafted.py -- do not edit
\begin{table}[t]
\centering
\footnotesize
\setlength{\tabcolsep}{5pt}
\caption{The hand-crafted instances of Table~1
of~\cite{DBLP:conf/atva/BeutnerFFS23}, each with its candidate cause
$\varphi_{\mathsf{C}}$. `actual cause': the answer of \textsc{CATS}; `$\MTSR$':
the answer of \texttt{-{}-check} (\S\ref{sec:analyses}), i.e.\ whether $\varphi_{\mathsf{C}}$ is the
$\MTSR$. $|A|$ is the number of states of the $\MTSR$ automaton.}
%% caption shortened with Nadav, 2026-10-14
\label{tab:catshand}
\begin{tabular}{@{}l cr cr r@{}}
\toprule
& \multicolumn{2}{c}{\textsc{CATS}} & \multicolumn{3}{c}{$\MTSR$} \\
\cmidrule(lr){2-3}\cmidrule(lr){4-6}
instance & actual cause & $t$ (s) & $\MTSR$ & $t$ (s) & $|A|$ \\
\midrule
%s
\bottomrule
\end{tabular}
\end{table}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()
    if a.run:
        run(a.timeout)
    if a.table:
        table()
    if not (a.run or a.table):
        ap.error("pick --run and/or --table")


if __name__ == "__main__":
    main()
