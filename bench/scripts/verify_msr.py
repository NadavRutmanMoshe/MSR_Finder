#!/usr/bin/env python3
"""
verify_msr -- re-check the MSR rows the benchmark recorded. NOT part of any
measured time.

    ~/micromamba/envs/spot/bin/python3 verify_msr.py            # hyperqb rows
    ~/micromamba/envs/spot/bin/python3 verify_msr.py --no-minimality
    ~/micromamba/envs/spot/bin/python3 verify_msr.py --self-test

    -> results/MSR/verify_hyperqb.csv

WHY IT READS THE CSV INSTEAD OF RE-RUNNING
------------------------------------------
The point is to catch bugs in the answers the benchmark actually recorded, and
to cost nothing on the benchmark's clock. So this reads `reason` back out of
`msr_hyperqb.csv` and checks it against the instance; the pipeline is never
run again, and this script's own wall clock is written into its OWN csv, never
merged into a tool's row.

WHY HYPERQB ONLY
----------------
For HyperQB, pi's model IS the file's [lasso] (smv_builder.build_pi_smv), so the
positions in `reason` are positions in that lasso and can be reconstructed
exactly from the instance. AutoHyper's witness is an UNROLLING of the lasso
chosen by the solver, and the recorded positions are relative to that unrolling,
which the CSV does not carry -- reconstructing from the file would silently
misalign. `--model autohyper` is therefore refused rather than approximated.

THE TWO HALVES
--------------
SUFFICIENCY, with spot:

    L(system)  n  L(pins(R, pi))  n  L(!Phi)   =   empty

`pins(R, pi)` is an AUTOMATON, not a chain of `X`s: pi and R are lassos, so a
pin inside the loop is an infinite modulo-periodic set of positions and is
expressed exactly rather than skipped.

MINIMALITY, with AutoHyper -- the 4-quantifier query, pi and tau PINNED to the
row under test:

    exists pi. exists tau. forall tau2. exists pi2.
        fix(pi) & fix(tau) &
        ( tau2 is not sufficient | tau2 = tau | tau2 not-subset-of tau )

SAT means no proper subset of R is sufficient. Only the minimality half of the
MSR formula, with the two existentials fixed, so it is much smaller than the
query it verifies -- and it is written HERE rather than imported from
msr_formulas, because a query may not certify itself.

Truncated rows (`reason_complete` False) are SKIPPED: only a prefix of R is
known, so neither half can be decided. Raise the benchmark's --k if you want
them covered. Since the pipeline started certifying its own witnesses
(msr_certify), such rows are rare: a flat witness is now either rescued as a
finite R -- certified, and marked complete -- or rejected.

STILL WORTH RUNNING, even though the pipeline now checks itself. This is an
INDEPENDENT check: a different engine (AutoHyper, on explicit LTSs) answering a
differently-written query. A shared bug would have to be in the instance parser
they both use, and nowhere else.
"""
import argparse
import csv
import os
import re
import subprocess
import sys

import bench_lib as BL
from bench_lib import RESULTS, SYNTCOMP

sys.path.insert(0, BL._DEMO_SCRIPTS)
import spot                 # noqa: E402
import converter            # noqa: E402
import msr_analyzer         # noqa: E402  (_exe / _run for the minimality query)
import msr_systems          # noqa: E402  (CliqueQ, the carrier for pi and tau)
import smv_builder          # noqa: E402  (parse_lasso)
import system_builder       # noqa: E402  (Clique + Real)

LASSO_VAR = "__MSR_LASSO__"
FIELDS = ["instance", "status", "size", "sufficient", "minimal", "note",
          "verify_s"]


# --------------------------------------------------------------------------- #
# Rebuilding pi and R
# --------------------------------------------------------------------------- #
def lassos(path, reason):
    """(pi_prefix, pi_cycle, tau_prefix, tau_cycle) as lists of input-name sets.

    pi comes from the instance's [lasso] projected onto the inputs -- which is
    exactly the single trace HyperQB's pi module has. R comes from the CSV, as
    `<input>@<t>` items with a `*` suffix marking a loop position.

    THE FRAME. A row's R need not live on pi's own lasso: a reason certified
    from a flat bounded witness is FINITE and its pins may run past pi's loop
    start, in which case the row's positions are indices into an UNROLLED pi
    (pi's loop repeated until it covers them and lands back on the loop start).
    The `*` pins name that frame's repeating block, so the frame's loop start is
    the smallest starred position; with no starred pin, R is finite and pins
    nothing after its last position. pi is unrolled to match, which is exact --
    it is the same trace, written out further."""
    inputs, _aps, _s, _st = converter.parse_hoa(path)
    lasso_aps, prefix, loop = smv_builder.parse_lasso(path)
    if not loop:
        return None

    def true_inputs(idx):
        return {ip for ip in inputs
                if ip in lasso_aps and lasso_aps.index(ip) in idx}

    pi_p = [true_inputs(s) for s in prefix]
    pi_c = [true_inputs(s) for s in loop]
    P, L = len(pi_p), len(pi_c)

    items = []
    for item in filter(None, (reason or "").split(",")):
        ap, _, pos = item.rpartition("@")
        items.append((ap, int(pos.rstrip("*")), pos.endswith("*")))

    starred = [t for _ap, t, star in items if star]
    if starred:
        # A starred position lies in [start, start + L) and start = P + m*L,
        # which pins m down uniquely -- the smallest starred position need not
        # be `start` itself (the frame's first loop step may pin nothing).
        t = max(starred)
        if t < P:
            return None
        start = P + ((t - P) // L) * L
        if any(not (start <= u < start + L) for u in starred):
            return None                  # not one period: not a frame we know
    else:
        start = P
        while start < max([t for _a, t, _s in items] + [P - 1]) + 1:
            start += L
    # pi, unrolled onto the frame
    chain = [pi_p[t] if t < P else pi_c[(t - P) % L] for t in range(start)]
    fr_pi_p, fr_pi_c = chain, list(pi_c)
    tau_p = [set() for _ in range(start)]
    tau_c = [set() for _ in range(L)]
    for ap, t, star in items:
        if star:
            tau_c[t - start].add(ap)
        elif 0 <= t < start:
            tau_p[t].add(ap)
        else:
            return None
    return fr_pi_p, fr_pi_c, tau_p, tau_c


def pin_automaton(pi_p, pi_c, tau_p, tau_c, inputs):
    """L(pins(R, pi)) as an automaton over the inputs (see the docstring)."""
    chain_pi, chain_tau = list(pi_p) + list(pi_c), list(tau_p) + list(tau_c)
    L, start = len(chain_pi), len(pi_p)
    lines = ["HOA: v1", f"States: {L}", "Start: 0",
             f"AP: {len(inputs)} " + " ".join(f'"{a}"' for a in inputs),
             "acc-name: all", "Acceptance: 0 t", "--BODY--"]
    for t in range(L):
        lits = [(f"{i}" if ap in chain_pi[t] else f"!{i}")
                for i, ap in enumerate(inputs) if ap in chain_tau[t]]
        lines.append(f"State: {t}")
        lines.append(f"[{'&'.join(lits) or 't'}] {t + 1 if t + 1 < L else start}")
    lines.append("--END--")
    return spot.automaton("\n".join(lines))


def _instance(path):
    with open(path, errors="replace") as f:
        c = f.read()
    eff = re.search(r'\[effect\]\s*\n\s*(.+)', c)
    hoa = re.search(r'(HOA:.*--END--)', c, re.DOTALL)
    return spot.automaton(hoa.group(1)), eff.group(1).strip()


def check_sufficient(path, ls):
    system, effect = _instance(path)
    inputs, _a, _s, _st = converter.parse_hoa(path)
    pins = pin_automaton(*ls, sorted(inputs))
    not_phi = spot.translate(spot.formula("!(%s)" % effect))
    return spot.product(spot.product(system, pins), not_phi).is_empty()


# --------------------------------------------------------------------------- #
# Minimality: the 4-quantifier query
# --------------------------------------------------------------------------- #
def _fixation(trace, aps, prefix, loop, marker):
    """Pin `trace` to a lasso, in the formula.

    Written here rather than imported from msr_formulas on purpose: this file
    verifies that module's queries and must not inherit its mistakes. The shape
    follows the CliqueQ system, which carries a free `marker` boolean: the
    marker holds at time 0, the loop body is described relative to every
    position where it holds, and the final conjunct makes it hold exactly at the
    loop starts."""
    def state(active, marker_val):
        parts = [(f'"{ap}"_{trace}' if ap in active else f'!"{ap}"_{trace}')
                 for ap in aps]
        if marker_val is True:
            parts.append(f'"{marker}"_{trace}')
        elif marker_val is False:
            parts.append(f'!"{marker}"_{trace}')
        return " & ".join(parts) if parts else "true"

    blk = f'"{marker}"_{trace}\n    & G (\n'
    for i, st in enumerate(loop):
        sc = state(st, None)
        blk += (f'      ("{marker}"_{trace} -> ({sc}))' if i == 0 else
                f'      ("{marker}"_{trace} -> {"X" * i}'
                f'(!"{marker}"_{trace} & {sc}))') + '\n      &\n'
    blk += (f'      ("{marker}"_{trace} -> {"X" * len(loop)}'
            f'("{marker}"_{trace}))\n    )')
    full = blk
    for st in reversed(prefix):
        full = f'{state(st, False)}\n  &\n  X(\n    {full}\n  )'
    return full


def minimality_hq(effect, inputs, pi_p, pi_c, tau_p, tau_c):
    eff_pi2 = re.sub(r'"([^"]+)"', r'{"\1"_pi2}', effect)
    if inputs:
        j = "\n      & \n      "
        ip_pi2 = j.join(f'({{"{i}"_tau2}} -> ({{"{i}"_pi}} <-> {{"{i}"_pi2}}))'
                        for i in inputs)
        ip_eq = j.join(f'({{"{i}"_tau}} <-> {{"{i}"_tau2}})' for i in inputs)
        ip_imp = j.join(f'({{"{i}"_tau2}} -> {{"{i}"_tau}})' for i in inputs)
    else:
        ip_pi2 = ip_eq = ip_imp = "true"
    return f"""exists pi. exists tau. forall tau2. exists pi2.
(
  {_fixation('pi', inputs, pi_p, pi_c, LASSO_VAR)}
)
&
(
  {_fixation('tau', inputs, tau_p, tau_c, LASSO_VAR)}
)
&
(
  !
  (
    ( G ( {ip_pi2} ) )
    ->
    ( {eff_pi2} )
  )
  |
  G ( {ip_eq} )
  |
  !
  ( G ( {ip_imp} ) )
)"""


def check_minimal(path, ls, timeout, workdir):
    pi_p, pi_c, tau_p, tau_c = ls
    inputs, _a, _s, _st = converter.parse_hoa(path)
    _sys, effect = _instance(path)
    hq = minimality_hq(effect, list(inputs), pi_p, pi_c, tau_p, tau_c)
    stem = os.path.join(workdir, "vmin_" +
                        os.path.splitext(os.path.basename(path))[0])
    written = []
    try:
        cliqueq, _ = msr_systems.build_cause_system_with_q(path)
        clique, _ = system_builder.build_cause_system(path)
        real, _ = system_builder.build_real_system(path)
        paths = []
        for label, content in (("pi", cliqueq), ("tau", cliqueq),
                               ("tau2", clique), ("pi2", real)):
            f = f"{stem}_{label}.txt"
            open(f, "w").write(content)
            written.append(f)
            paths.append(f)
        f_hq = stem + ".hq"
        open(f_hq, "w").write(hq)
        written.append(f_hq)
        out, err = msr_analyzer._run(paths, f_hq, timeout)
        if msr_analyzer._is_solver_error(out, err):
            return None, f"AutoHyper error: {(out or err).strip()[:80]}"
        if "UNSAT" in out:
            return False, "a proper subset of R is also sufficient"
        if "SAT" in out:
            return True, ""
        return None, f"no verdict: {(out or err).strip()[:80]}"
    except subprocess.TimeoutExpired:
        return None, f"minimality query timed out (>{timeout}s)"
    finally:
        for f in written:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
def verify_row(r, corpus, timeout, workdir, do_min):
    import time
    path = os.path.join(corpus, r["instance"] + ".txt")
    out = {"instance": r["instance"], "status": r.get("status", ""),
           "size": r.get("size", ""), "sufficient": "", "minimal": "",
           "note": ""}
    t0 = time.perf_counter()
    if r.get("status") not in ("SAT_MSR", "SAT_SR"):
        out["note"] = "nothing to verify"
    elif str(r.get("reason_complete", "True")).lower() == "false":
        out["note"] = "SKIPPED: R truncated at the solver's bound"
    elif not os.path.exists(path):
        out["note"] = "instance file not found"
    else:
        ls = lassos(path, r.get("reason", ""))
        if ls is None:
            out["note"] = "could not rebuild pi/R from the row"
        else:
            out["sufficient"] = check_sufficient(path, ls)
            if not out["sufficient"]:
                out["note"] = ("NOT SUFFICIENT -- a system trace agrees with pi "
                               "everywhere R pins and still violates Phi")
            elif do_min and r.get("status") == "SAT_MSR":
                ok, note = check_minimal(path, ls, timeout, workdir)
                out["minimal"] = "" if ok is None else ok
                out["note"] = note
    out["verify_s"] = round(time.perf_counter() - t0, 3)
    return out


def self_test(corpus, timeout, workdir, src):
    """Mutate a verified row and require both halves to reject the mutant."""
    rows = [r for r in csv.DictReader(open(src, newline=""))
            if r.get("status") == "SAT_MSR" and r.get("reason")
            and str(r.get("reason_complete", "True")).lower() != "false"]
    if not rows:
        print(f"{BL._R}no complete SAT_MSR row in {src} to self-test on{BL._0}")
        return 1
    r = rows[0]
    print(f"self-test on {r['instance']}  (R = {r['reason']})\n")
    bad = 0

    def show(label, got, want):
        nonlocal bad
        ok = (got is want)
        bad += 0 if ok else 1
        mark = f"{BL._G}as expected{BL._0}" if ok else f"{BL._R}*** WRONG ***{BL._0}"
        print(f"  {label:<44} {str(got):<6} {mark}")

    base = verify_row(r, corpus, timeout, workdir, True)
    show("control: sufficient?", base["sufficient"], True)
    show("control: minimal?", base["minimal"], True)

    pins = r["reason"].split(",")
    dropped = dict(r, reason=",".join(pins[1:]))
    show(f"mutant: R without {pins[0]} -- sufficient?",
         verify_row(dropped, corpus, timeout, workdir, False)["sufficient"], False)

    inputs, _a, _s, _st = converter.parse_hoa(
        os.path.join(corpus, r["instance"] + ".txt"))
    have = {p.rpartition("@")[0] for p in pins}
    extra = next((i for i in inputs if i not in have), None)
    if extra:
        added = dict(r, reason=r["reason"] + f",{extra}@0")
        show(f"mutant: R plus {extra}@0 -- minimal?",
             verify_row(added, corpus, timeout, workdir, True)["minimal"], False)
    print(f"\n{'self-test PASSED' if not bad else BL._R + 'self-test FAILED' + BL._0}")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        description="Re-check the recorded MSR answers. Never timed as tool work.")
    ap.add_argument("--csv", default=os.path.join(RESULTS, "MSR",
                                                  "msr_hyperqb.csv"))
    ap.add_argument("--out", default=os.path.join(RESULTS, "MSR",
                                                  "verify_hyperqb.csv"))
    ap.add_argument("--corpus", default=SYNTCOMP)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--no-minimality", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    BL.install_sigint()

    workdir = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                           f"verify_msr_{os.getpid()}")
    os.makedirs(workdir, exist_ok=True)
    if args.self_test:
        return self_test(args.corpus, args.timeout, workdir, args.csv)

    if not os.path.exists(args.csv):
        sys.exit(f"no MSR results at {args.csv} -- run the benchmark first")
    rows = BL.Rows(args.out, FIELDS)
    src = [r for r in csv.DictReader(open(args.csv, newline=""))
           if r["instance"] not in rows.done]
    BL.banner(f"verifying {os.path.basename(args.csv)} "
              f"({len(src)} rows) -- NOT counted as benchmark time")
    bad = 0
    for i, r in enumerate(src, 1):
        out = verify_row(r, args.corpus, args.timeout, workdir,
                         not args.no_minimality)
        rows.write(out)
        BL.tick()
        flag = ""
        if out["sufficient"] is False or out["minimal"] is False:
            flag = f"{BL._R}FAIL{BL._0}"
            bad += 1
        elif out["sufficient"] is True:
            flag = f"{BL._G}ok{BL._0}"
        BL.progress(i, len(src), out["instance"],
                    f"{flag} suff={out['sufficient']} min={out['minimal']} "
                    f"{out['note'][:60]}")
    rows.close()
    print(f"\n{bad} rows failed verification")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
