#!/usr/bin/env python3
r"""
corp_curated -- the curated, per-instance comparison against the CORP paper.

    ~/micromamba/envs/spot/bin/python3 bench/paper/corp_curated.py --gen
    ~/micromamba/envs/spot/bin/python3 bench/paper/corp_curated.py --run
    ~/micromamba/envs/spot/bin/python3 bench/paper/corp_curated.py --table

Sec. 7 reports AVERAGES over 818 generated instances.  This script produces the
other kind of evidence a referee asks for: a handful of NAMED instances, in full
detail, that can be looked up in the paper we are comparing against.  The
instances are the ones CORP's own Table 1 reports --
Finkbeiner, Frenkel, Metzger, Siber, "Synthesis of Temporal Causality", CAV'24 --
that is, the arbiter family Spurious n / Unfair n / Full n on the trace where
every client requests at every position.

WHY THE SYSTEMS ARE REBUILT HERE AND NOT DOWNLOADED
---------------------------------------------------
CORP's GitHub repository (external_tools/corp) ships only `examples/simple1` and
`examples/simple2`; the arbiter benchmarks live in the Zenodo artifact, which is
a 2.4 GB VM image.  So the three families are reconstructed from the prose of
CORP Sec. 6.1, which specifies them exactly:

  Spurious n  "give out grants to all clients in a round-robin manner,
              regardless of previous requests"
  Unfair n    "prioritize one client with request r_prio over the others"
  Full n      "only give out grants that were requested beforehand"

The reconstruction is checked against the one number CORP publishes for each,
|T|.  Spurious n and Unfair n reproduce it exactly (n and 2(n-1)); Full n does
not (see FULL ARBITER below).  Since BOTH tools are then run here, on these
files, the table is internally valid either way -- CORP's published times are a
sanity anchor, not a column.

  Spurious n: n states, state k emits g_k and moves to k+1 mod n.  Inputs are
              unconstrained, which is the point: the effect F g_0 holds on every
              trace, so the cause is `true`.   |T| = n   (CORP: 1, 2, 3, 4)

  Unfair n:   a MOORE machine -- the grant is decided from the state, i.e. one
              step after the requests that chose it.  State (P, t) emits g_prio,
              state (N, t) emits g_t, and t is the next non-prioritised client
              in the round robin.  r_prio holding keeps the machine in P.
              |T| = 2(n-1)  (CORP: 2, 4, 6).  The Moore delay is what makes it
              2(n-1) rather than (n-1); that it lands on CORP's count for all
              three n is the evidence that this is their machine.

  Full n:     synthesized with ltlsynt from
                 /\_i  G(r_i -> F g_i)                    every request served
                    /\ (!g_i W r_i)                       no grant before a request
                    /\ G(g_i -> X(!g_i W r_i))            no second grant without one
                 /\_{i<j} G(!(g_i & g_j))                 mutual exclusion
              |T| = 1, 3, 10, 29 against CORP's 1, 4, 11, 46.  Several other
              formulations of "requested beforehand" were tried (a one-step
              X-guard, g_i -> r_i, plain responsiveness); none lands on
              1/4/11/46, and without their artifact there is no way to close the
              gap.  This is disclosed in the table caption.

WHAT IS MEASURED
----------------
Per instance: CORP's cause automaton (time, states, determinism), TraceMSR's
MTSR witness (same three), whether the two accept the same language, and -- for
the instances inside the |IP| <= 3 range -- the MSR on CORP's own trace.  The
LTL column is the analogue of CORP's phi_C, except that theirs is "guessed
manually" and ours is machine-checked with spot.are_equivalent.

Reuses corp_compare.py for everything about running CORP, so the translation
from a TraceMSR .txt to CORP's three input files is the same code path the
818-instance sweep used.
"""
import argparse
import csv
import itertools
import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import paths  # noqa: E402
for p in (_HERE, paths.TOOL_SCRIPTS, os.path.join(paths.BENCH, "gen")):
    if p not in sys.path:
        sys.path.insert(0, p)

import converter                      # noqa: E402  (parse_hoa, eval_cond)
import hoa_canonicalize               # noqa: E402  (don't-care resolution)
import corp_compare as CC             # noqa: E402  (CORP runner + .txt -> CORP)

# 2026-10-07: run the SAME code as the paper's 818-instance data (demo_tool/
# bench), not the older copies in this repo.  CORP is byte-identical either
# way; bench's mtsr_min.py computes the same automaton but also unwinds on
# SIGTERM, so a timed-out run does not leave `autfilt --sat-minimize` running.
CC.MTSR_MIN = os.path.join(paths.BENCH_SCRIPTS, "mtsr_min.py")
CC.CORP_DIR = os.path.join(paths.TOOLS, "CORP")

SPOT_PY = paths.SPOT_PY
LTLSYNT = os.path.join(paths.SPOT_BIN, "ltlsynt")
INST_DIR = paths.CORP_ARBITERS
OUT_CSV = os.path.join(paths.HANDCRAFTED, "corp_curated.csv")
TEX_OUT = os.path.join(paths.TABLES, "tab_corpcurated.tex")

FIELDS = [
    "instance", "family", "n", "sys_states", "inputs", "effect", "effect_tex",
    "corp_status", "corp_states", "corp_det", "corp_s",
    "mtsr_status", "mtsr_states", "mtsr_det", "mtsr_s", "mtsr_ltl", "mtsr_ltl_tex",
    "mtsr_premin", "mtsr_err",
    "equiv", "msr_status", "msr_size", "msr_s", "msr_ltl",
    "corp_phi_c", "phi_c_equiv",
]


# --------------------------------------------------------------------------- #
# building the three arbiter families
# --------------------------------------------------------------------------- #
def _aps(n):
    return [f"r{i}" for i in range(n)] + [f"g{i}" for i in range(n)]


def _grant_label(n, k):
    """HOA label fixing exactly g_k true and every other grant false."""
    return " & ".join(("%d" % (n + j)) if j == k else ("!%d" % (n + j))
                      for j in range(n))


def spurious(n):
    """Round-robin grants, requests ignored.  |T| = n."""
    body = []
    for k in range(n):
        body.append(f"State: {k}")
        body.append(f"[({_grant_label(n, k)})] {(k + 1) % n}")
    # every client requests at every step; state k emits g_k
    loop = [sorted(set(range(n)) | {n + k}) for k in range(n)]
    return _hoa(n, body, 0), [], loop


def unfair(n):
    """Moore arbiter with a prioritised client.  |T| = 2(n-1).

    prio = client n-1.  P_t (state t) emits g_prio, N_t (state m+t) emits g_t,
    t ranges over the n-1 non-prioritised clients.  r_prio -> P, else -> N.
    """
    m = n - 1
    prio = n - 1
    rp = prio                      # AP index of r_prio
    body = []
    for t in range(m):             # P_t : granting the prioritised client
        body.append(f"State: {t}")
        body.append(f"[({rp} & {_grant_label(n, prio)})] {t}")
        body.append(f"[(!{rp} & {_grant_label(n, prio)})] {m + t}")
    for t in range(m):             # N_t : granting non-prioritised client t
        body.append(f"State: {m + t}")
        body.append(f"[({rp} & {_grant_label(n, t)})] {(t + 1) % m}")
        body.append(f"[(!{rp} & {_grant_label(n, t)})] {m + (t + 1) % m}")
    # all clients request forever -> r_prio always holds -> stay in P_0
    loop = [sorted(set(range(n)) | {n + prio})]
    return _hoa(n, body, 0), [], loop


def full_spec(n):
    ins = ",".join(f"r{i}" for i in range(n))
    outs = ",".join(f"g{i}" for i in range(n))
    parts = []
    for i in range(n):
        parts.append(f"G(r{i} -> F g{i})")
        parts.append(f"(!g{i} W r{i})")
        parts.append(f"G(g{i} -> X(!g{i} W r{i}))")
    for i in range(n):
        for j in range(i + 1, n):
            parts.append(f"G(!(g{i} & g{j}))")
    return " & ".join(parts), ins, outs


def full(n):
    """ltlsynt's implementation of the specification in full_spec."""
    f, ins, outs = full_spec(n)
    p = subprocess.run([LTLSYNT, f"--formula={f}", f"--ins={ins}", f"--outs={outs}"],
                       capture_output=True, text=True)
    out = p.stdout
    if not out.startswith("REALIZABLE"):
        raise RuntimeError(f"full {n}: {out.splitlines()[:1]}")
    hoa = out.split("\n", 1)[1]

    # ltlsynt emits a STRATEGY: an edge may leave an output unconstrained, which
    # converter.check_determinism rejects. Resolve the don't-cares the same way
    # the SYNTCOMP sweep does (see impl.tex / hoa_canonicalize).
    tmp = os.path.join(INST_DIR, f"_full{n}.hoa")
    os.makedirs(INST_DIR, exist_ok=True)
    with open(tmp, "w") as fh:
        fh.write("[inputs]\n" + " ".join(f'"r{i}"' for i in range(n)) + "\n"
                 "[system]\n" + hoa)
    inputs, aps, start, states = converter.parse_hoa(tmp)
    os.remove(tmp)
    states = hoa_canonicalize.canonicalize(inputs, aps, states)

    # ltlsynt is free to order the APs as it likes; put them back in
    # r0..r_{n-1} g0..g_{n-1} order so the labels below and the [lasso] agree.
    want = _aps(n)
    perm = [aps.index(a) for a in want]         # want[k] is old index perm[k]
    inv = {old: new for new, old in enumerate(perm)}
    states = {q: [(_reindex(c, inv), t) for c, t in es] for q, es in states.items()}

    prefix, loop = _simulate(n, want, states, start)
    body = []
    order = sorted(states)
    ren = {q: i for i, q in enumerate([start] + [q for q in order if q != start])}
    for q in sorted(states, key=lambda q: ren[q]):
        body.append(f"State: {ren[q]}")
        for cond, tgt in states[q]:
            body.append(f"[({cond})] {ren[tgt]}")
    prefix = [s for s in prefix]
    return _hoa(n, body, 0), prefix, loop


def _reindex(cond, inv):
    import re
    return re.sub(r'\b\d+\b', lambda m: str(inv[int(m.group(0))]), cond)


def _simulate(n, aps, states, start):
    """Run the machine with every r_i true forever; return (prefix, loop)."""
    ins = [f"r{i}" for i in range(n)]
    outs = [f"g{i}" for i in range(n)]
    i_vals = tuple([True] * n)
    o_combos = list(itertools.product([False, True], repeat=n))

    seen, seq, q = {}, [], start
    while q not in seen:
        seen[q] = len(seq)
        hit = None
        for cond, tgt in states[q]:
            for o in o_combos:
                val = converter.get_val_tuple(ins, outs, aps, i_vals, o)
                if converter.eval_cond(cond, val):
                    hit = (o, tgt)
                    break
            if hit:
                break
        if hit is None:
            raise RuntimeError(f"state {q} has no edge for all-requests")
        o, q = hit
        seq.append(sorted(set(range(n)) | {n + j for j in range(n) if o[j]}))
    k = seen[q]
    return seq[:k], seq[k:]


def _hoa(n, body, start):
    aps = _aps(n)
    head = [f"HOA: v1", f"Start: {start}",
            "AP: %d %s" % (len(aps), " ".join(f'"{a}"' for a in aps)),
            "Acceptance: 0 t", "--BODY--"]
    return "\n".join(head + body + ["--END--"])


def _sets(seq):
    return " ".join("{" + " ".join(str(i) for i in s) + "}" for s in seq)


def write_instance(path, info, n, effect, hoa, prefix, loop):
    aps = _aps(n)
    with open(path, "w") as f:
        f.write(f"[info]\n{info}\n\n")
        f.write("[inputs]\n" + " ".join(f'"r{i}"' for i in range(n)) + "\n\n")
        f.write(f"[effect]\n{effect}\n\n")
        f.write("[lasso]\n")
        f.write("AP: " + " ".join(f'"{a}"' for a in aps) + "\n")
        f.write("Prefix: " + _sets(prefix) + "\n")
        f.write("Loop: " + _sets(loop) + "\n\n")
        f.write("[system]\n" + hoa + "\n")


#: (key, family, n, effect, effect in LaTeX) -- CORP Table 1, row for row.
def catalogue():
    rows = []
    for n in (1, 2, 3, 4):
        rows.append((f"spurious_{n}", "Spurious", n, 'F "g0"',
                     r"$\LTLeventually g_0$"))
    for n in (2, 3, 4):
        rows.append((f"unfair_{n}", "Unfair", n, 'G !"g0"',
                     r"$\LTLglobally \neg g_0$"))
    for n in (1, 2, 3, 4):
        rows.append((f"full_{n}_F", "Full", n, 'F "g0"',
                     r"$\LTLeventually g_0$"))
        rows.append((f"full_{n}_GF", "Full", n, 'G F "g0"',
                     r"$\LTLglobally\LTLeventually g_0$"))
    return rows


def gen():
    os.makedirs(INST_DIR, exist_ok=True)
    built = {}
    for key, fam, n, effect, _tex in catalogue():
        if fam == "Spurious":
            sysdef = built.setdefault(("S", n), spurious(n))
        elif fam == "Unfair":
            sysdef = built.setdefault(("U", n), unfair(n))
        else:
            sysdef = built.setdefault(("F", n), full(n))
        hoa, prefix, loop = sysdef
        path = os.path.join(INST_DIR, key + ".txt")
        write_instance(path, f"{fam} {n}-arbiter, CORP (CAV'24) Table 1",
                       n, effect, hoa, prefix, loop)
        ns = hoa.count("State:")
        print(f"  {key:<14} |T|={ns:<3} |IP|={n}  effect {effect}")
    print(f"\nwrote {len(catalogue())} instances to {INST_DIR}")


# --------------------------------------------------------------------------- #
# CORP's ORIGINAL systems (2026-10-05)
# --------------------------------------------------------------------------- #
#: CORP's artifact (Zenodo 10946309, artifact.zip) ships the arbiter benchmark as
#: sources/benchmarks/synthesis/arbiter/input.csv: system (HOA, or AIGER for
#: Full n), trace (a spot word), effect and the hand-written phi_C ("hypothesis").
#: Only that 14 KB file was pulled out of the 2.4 GB zip, by HTTP range requests
#: on the zip's central directory.  With it, |T| is CORP's own 1/4/11/46 for
#: Full n, instead of our rebuilt 1/3/10/29.
ORIG_CSV = os.path.join(INST_DIR, "corp_artifact_arbiter_input.csv")
ORIG_KEY = {"spurious": "spurious_{n}", "unfair": "unfair_{n}",
            "full": "full_{n}_F", "fullGF": "full_{n}_GF"}


def _orig_key(name):
    import re
    m = re.match(r"([a-z]+?)(\d)(GF)?$", name)
    fam, n, gf = m.group(1), int(m.group(2)), m.group(3) or ""
    return ORIG_KEY[fam + gf].format(n=n), fam.capitalize(), n


def gen_orig(path=ORIG_CSV):
    import re
    import spot
    rows = list(csv.DictReader(open(path, newline="")))
    os.makedirs(INST_DIR, exist_ok=True)
    for r in rows:
        key, fam, n = _orig_key(r["name"])
        s = r["system"].replace("\\n", "\n") + "\n"
        aut = (spot.aiger_circuit(s).as_automaton() if s.startswith("aag")
               else spot.automaton(s))
        hoa = aut.to_str("hoa")
        # the HOA printer's AP order, so [lasso] and [system] index alike
        aps = re.findall(r'"([^"]+)"', re.search(r"^AP: .*$", hoa, re.M).group(0))
        outs = {str(a) for a in spot.get_synthesis_output_aps(aut)}
        ins = [a for a in aps if a not in outs]
        # the trace is a spot word: "a; b; cycle{c; d}", every letter a full cube
        word = spot.parse_word(r["trace"], aut.get_dict())

        def letter(bdd):
            f = spot.bdd_to_formula(bdd, aut.get_dict())
            pos = {str(x) for x in spot.atomic_prop_collect(f)
                   if spot.formula_Not(x) not in _lits(f) and x in _lits(f)}
            return sorted(aps.index(a) for a in pos)
        prefix = [letter(b) for b in word.prefix]
        loop = [letter(b) for b in word.cycle]
        eff = re.sub(r'\b([rg]_\w+)', r'"\1"', r["effect"])
        p = os.path.join(INST_DIR, key + ".txt")
        with open(p, "w") as f:
            f.write(f"[info]\n{fam} {n}-arbiter, CORP (CAV'24) Table 1 -- "
                    f"the system of CORP's artifact ({r['name']}), "
                    f"phi_C = {r['hypothesis']}\n\n")
            f.write("[inputs]\n" + " ".join(f'"{a}"' for a in ins) + "\n\n")
            f.write(f"[effect]\n{eff}\n\n")
            f.write("[lasso]\n")
            f.write("AP: " + " ".join(f'"{a}"' for a in aps) + "\n")
            f.write("Prefix: " + _sets(prefix) + "\n")
            f.write("Loop: " + _sets(loop) + "\n\n")
            f.write("[system]\n" + hoa.strip() + "\n")
        print(f"  {key:<14} |T|={aut.num_states():<3} |IP|={len(ins)}  "
              f"effect {eff}  phi_C {r['hypothesis']}")


def _lits(f):
    """The literals of a cube formula (a conjunction of APs and negated APs)."""
    import spot
    if f.kind() == spot.op_And:
        return [c for c in f]
    return [f]


def corp_hypotheses(path=ORIG_CSV):
    """key -> (phi_C as CORP's artifact writes it, expected |A| subset/full)."""
    out = {}
    for r in csv.DictReader(open(path, newline="")):
        key, _fam, _n = _orig_key(r["name"])
        out[key] = (r["hypothesis"], r["expected a_subset"], r["expected a_full"])
    return out


# --------------------------------------------------------------------------- #
# measuring
# --------------------------------------------------------------------------- #
def mtsr_ltl_of(hoa_path, inputs):
    """Smallest LTL formula equivalent to the witness, or '' if none <= size 7.

    CORP's phi_C column is "guessed manually"; this one is searched and then
    confirmed with spot.are_equivalent, so it is exact in the direction it
    claims (never a formula that is not equivalent; possibly no formula where a
    larger one exists).
    """
    import spot
    A = spot.automaton(hoa_path)
    atoms = list(inputs)
    cands = ["1", "0"]
    for a in atoms:
        cands += [a, f"!{a}", f"F {a}", f"G {a}", f"F !{a}", f"G !{a}",
                  f"X {a}", f"X !{a}", f"G F {a}", f"F G {a}"]
    for a, b in itertools.permutations(atoms, 2):
        cands += [f"{a} & {b}", f"{a} | {b}", f"G({a} | {b})", f"G({a} & {b})",
                  f"F({a} & {b})", f"{a} U {b}",
                  f"!{a} W G {b}", f"(!{a} & !{b}) W G {b}"]
    for c in cands:
        try:
            if spot.are_equivalent(A, spot.translate(c)):
                return c
        except Exception:
            continue
    return ""


def run(timeout, only=None, with_msr=True):
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    work = os.path.join(paths.OUT, "work", "corp_curated")
    rows = []
    for key, fam, n, effect, tex in catalogue():
        if only and only not in key:
            continue
        path = os.path.join(INST_DIR, key + ".txt")
        data = CC.parse_instance(path)
        import re as _re
        inputs = _re.findall(r'"([^"]+)"', data["inputs"]) if data.get("inputs") \
            else [f"r{i}" for i in range(n)]
        wd = os.path.join(work, key)
        os.makedirs(wd, exist_ok=True)
        # a failed run must not leave the previous run's automaton behind for
        # the language comparison below to pick up
        for stale in ("mtsr.hoa", "result.hoa"):
            if os.path.exists(os.path.join(wd, stale)):
                os.remove(os.path.join(wd, stale))
        row = {"instance": key, "family": fam, "n": n, "inputs": n,
               "effect": effect, "effect_tex": tex,
               "sys_states": data["system"].count("State:")}
        print(f"[{key}] ", end="", flush=True)

        # --- CORP and MTSR: the same runners the 818-instance sweep used -----
        row.update(CC.run_corp(data, wd, timeout))
        print(f"corp={row['corp_status']}/{row.get('corp_states')}/"
              f"{row['corp_s']}s ", end="", flush=True)

        whoa = os.path.join(wd, "mtsr.hoa")
        row.update(CC.run_mtsr(path, timeout, save_hoa=whoa))
        print(f"mtsr={row['mtsr_status']}/{row.get('mtsr_states')}/"
              f"{row['mtsr_s']}s ", end="", flush=True)
        row["mtsr_det"] = int(bool(row.get("mtsr_det"))) if row.get("mtsr_status") == "OK" else ""

        ltl = ""
        if row.get("mtsr_status") == "OK" and os.path.exists(whoa):
            ltl = mtsr_ltl_of(whoa, inputs)
        row["mtsr_ltl"], row["mtsr_ltl_tex"] = ltl, ltl_tex(ltl)

        # --- same language? --------------------------------------------------
        corp_hoa = os.path.join(wd, "result.hoa")
        row["equiv"] = ""
        if os.path.exists(corp_hoa) and os.path.exists(whoa):
            _yes, verdict = CC.compare_languages(corp_hoa, whoa, timeout)
            row["equiv"] = {"EQUIVALENT": "=",
                            "CORP_SUBSET_OF_MTSR": r"$\subsetneq$",
                            "MTSR_SUBSET_OF_CORP": r"$\supsetneq$",
                            "INCOMPARABLE": r"$\bowtie$"}.get(verdict, "")

        # --- our automaton against CORP's own phi_C (their "hypothesis") -----
        row["corp_phi_c"], row["phi_c_equiv"] = "", ""
        hyp = corp_hypotheses().get(key) if os.path.exists(ORIG_CSV) else None
        if hyp and row.get("mtsr_status") == "OK" and os.path.exists(whoa):
            import spot
            row["corp_phi_c"] = hyp[0]
            row["phi_c_equiv"] = int(spot.are_equivalent(
                spot.automaton(whoa), spot.translate(hyp[0])))

        # --- MSR on CORP's own trace (dropped from the table 2026-10-05) -----
        if with_msr:
            row.update(_msr(path, timeout))
        else:
            row.update({"msr_status": "", "msr_size": "", "msr_s": "", "msr_ltl": ""})
        print(f"msr={row['msr_status']}/{row['msr_size']}/{row['msr_s']}s "
              f"equiv={row['equiv']} phi_C={row['phi_c_equiv']}")
        rows.append(row)

    if only and os.path.exists(OUT_CSV):       # --only updates rows in place
        done = {r["instance"]: r for r in rows}
        old = list(csv.DictReader(open(OUT_CSV, newline="")))
        rows = [done.pop(r["instance"], r) for r in old] + list(done.values())
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"\nwrote {OUT_CSV}")


def _msr(path, timeout):
    """|R| and its LTL rendering, through msr_cause.py.

    Run as a subprocess under `timeout --signal=TERM`, not in-process: the MSR
    pipeline starts AutoHyper in a session of its own, and only msr_cause's own
    SIGTERM handler unwinds through proc_guard's cleanup. SIGKILLing it here
    would orphan the solver at 100% CPU (see msr_cause.py).
    """
    cmd = [SPOT_PY, os.path.join(_HERE, "msr_cause.py"), os.path.abspath(path),
           "--timeout", str(timeout)]
    p, dt, killed = CC._sub_graceful(cmd, timeout)
    if killed:
        return {"msr_status": "TIMEOUT", "msr_size": "", "msr_s": round(dt, 3),
                "msr_ltl": ""}
    try:
        j = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception:                                        # noqa: BLE001
        return {"msr_status": "ERROR", "msr_size": "", "msr_s": round(dt, 3),
                "msr_ltl": ""}
    size = j.get("size", "")
    if j.get("status") not in ("SAT_MSR", "SAT_SR"):
        size = ""
    return {"msr_status": j.get("status", ""), "msr_size": size,
            "msr_s": round(dt, 3), "msr_ltl": j.get("ltl", "")}


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #
def ltl_tex(f):
    if not f:
        return "--"
    if f == "1":
        return r"$\mathit{true}$"
    if f == "0":
        return r"$\mathit{false}$"
    s = f
    for a, b in (("G F ", r"\LTLglobally\LTLeventually "),
                 ("F G ", r"\LTLeventually\LTLglobally "),
                 ("G ", r"\LTLglobally "), ("F ", r"\LTLeventually "),
                 ("X ", r"\LTLnext ")):
        s = s.replace(a, b)
    s = s.replace(" W ", r" \LTLw ").replace(" U ", r" \LTLu ")
    s = s.replace("!", r"\neg ").replace("&", r"\wedge").replace("|", r"\vee")
    import re
    s = re.sub(r'\b([rg])(\d+)\b', r'\1_{\2}', s)
    return "$" + s + "$"


def fmt(x, spec="{:.2f}"):
    if x in ("", None):
        return "--"
    try:
        return spec.format(float(x))
    except (TypeError, ValueError):
        return str(x)


def _tex_names(s):
    """r_m / g_m are CORP's prioritised client; its paper writes r_prio."""
    import re
    s = s.replace("r_m", r"r_{\mathit{prio}}").replace("g_m", r"g_{\mathit{prio}}")
    return re.sub(r"\b([rg])_(\d+)\b", r"\1_{\2}", s)


def table():
    """CORP Table 1, row for row, with both tools run here (Hadar, 2026-10-05:
    no MSR columns, |T| for the system, phi_E for the effect, `t (s)`, and the
    language column replaced by one sentence in the caption)."""
    with open(OUT_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    hyp = corp_hypotheses() if os.path.exists(ORIG_CSV) else {}

    lines, last_fam, last_name, differ = [], None, None, []
    for r in rows:
        if last_fam is not None and r["family"] != last_fam:
            lines.append(r"\midrule")
            last_name = None
        last_fam = r["family"]
        name = f"\\textsc{{{r['family'].lower()}}} {r['n']}"
        show, last_name = ("" if name == last_name else name), name
        phi_c = hyp.get(r["instance"], (r.get("corp_phi_c", ""),))[0]
        phi_tex = _tex_names(ltl_tex(phi_c.replace("G ", "G ").replace("F ", "F ")
                                     .replace("true", "1")))
        mark = ""
        if r.get("phi_c_equiv") == "0":
            mark, _ = r"$^{\dagger}$", differ.append(r)
        eff = _tex_names(ltl_tex(r["effect"].replace('"', "").replace("! ", "!")))
        lines.append(" & ".join([
            show, (r["sys_states"] if show else ""), eff,
            secs(r["corp_s"], r["corp_status"]), secs(r["mtsr_s"], r["mtsr_status"]),
            r["corp_states"] or "--", mtsr_size(r),
            phi_tex + mark,
        ]) + r" \\")
    note = ""
    for r in differ:
        note += (r" $^{\dagger}$\,On \textsc{" + r["family"].lower() + "}~" + r["n"] +
                 r", the language of $\MTSR$ is $" +
                 # \LTL... operators loop forever in a moving argument
                 _tex_names(ltl_tex(r["mtsr_ltl"]))[1:-1].replace("\\LTL", "\\protect\\LTL") +
                 r"$, which strictly contains that of \textsc{CORP}: a trace in "
                 r"which no client ever sends a request also never grants $g_0$.")
    full4 = next((r["corp_states"] for r in rows if r["instance"] == "full_4_GF"), "--")
    tex = CURATED_TEX % {"rows": "\n".join(lines), "note": note, "full4": full4}
    os.makedirs(os.path.dirname(TEX_OUT), exist_ok=True)
    with open(TEX_OUT, "w") as f:
        f.write(tex)
    print(f"wrote {TEX_OUT}")


def mtsr_size(r):
    """|A| of MTSR; when the SAT minimization ran out of memory (MO), the size
    of the deterministic automaton before minimization, marked * (Hadar,
    2026-10-14)."""
    if r.get("mtsr_status") == "OK":
        return r["mtsr_states"] or "--"
    if r.get("mtsr_status") == "ERROR" and r.get("mtsr_premin"):
        return r["mtsr_premin"] + r"$^{*}$"
    return "--"


def secs(x, status):
    """Time, or TO where the tool did not answer -- CORP Table 1's convention.

    The wall clock of a killed run is the budget, not a measurement, so
    printing it next to real times invites it to be read as one."""
    # ERROR here is only ever mtsr_min.py hitting its 6 GB cap in
    # `autfilt --sat-minimize` (full_4_GF, checked by hand 2026-10-08)
    if status == "ERROR":
        return "MO"
    return "TO" if status in ("TIMEOUT", "KILLED") else fmt(x)


CURATED_TEX = r"""%% generated by bench/paper/corp_curated.py -- do not edit
\begin{table}[t]
\centering
\footnotesize
\setlength{\tabcolsep}{4.5pt}
\caption{$\MTSR$ and \textsc{CORP} on the arbiters of Table~1
of~\cite{FFMS24}. $|T|$ is the number of states of the system,
$\varphi_{\mathsf{E}}$ the effect, $|A|$ the number of states of the automaton
each tool returns, and $\varphi_{\mathsf{C}}$ the cause given in~\cite{FFMS24}.
$^{\dagger}$\,Our cause is more general than that of \textsc{CORP}
(\S\ref{sec:compare}). TO: timeout; MO: out of memory while minimizing;
$^{*}$: number of states before minimization.}
%% caption shortened with Nadav, 2026-10-14; Hadar's comment (results before
%% minimization) answered by the * and removed (Nadav)
\label{tab:corpcurated}
\begin{tabular}{@{}lrl rr rr l@{}}
\toprule
& & & \multicolumn{2}{c}{$t$ (s)} & \multicolumn{2}{c}{$|A|$} & \\
\cmidrule(lr){4-5}\cmidrule(lr){6-7}
instance & $|T|$ & $\varphi_{\mathsf{E}}$ & \textsc{CORP} & $\MTSR$ & \textsc{CORP} & $\MTSR$ & $\varphi_{\mathsf{C}}$ \\
\midrule
%(rows)s
\bottomrule
\end{tabular}
\end{table}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", action="store_true",
                    help="rebuild the systems from CORP's prose (old, superseded)")
    ap.add_argument("--gen-orig", action="store_true",
                    help="write the instances from CORP's own artifact CSV")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--only")
    ap.add_argument("--no-msr", action="store_true",
                    help="skip the MSR column (Hadar, 2026-10-05: not needed here)")
    a = ap.parse_args()
    if a.gen:
        gen()
    if a.gen_orig:
        gen_orig()
    if a.run:
        # The table has no MSR column (Hadar, 2026-10-05), and msr_cause.py is
        # not shipped: the artifact always runs without it.
        run(a.timeout, a.only, with_msr=False)
    if a.table:
        table()
    if not (a.gen or a.gen_orig or a.run or a.table):
        ap.error("pick --gen, --run or --table")


if __name__ == "__main__":
    main()
