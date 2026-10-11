#!/usr/bin/env python3
"""
msr_certify -- decide, exactly, whether a candidate reason really is one.

WHY THIS EXISTS
---------------
The HyperQB backend is a BOUNDED model checker, and its `sat` verdict on our
queries is NOT a proof.  Both the SR and the MSR query have a universal trace
quantifier (`forall pi2`, `forall tau2`), and HyperQB unrolls `G` and `F` with
no semantics-dependent tail at all -- hltlunroller/src/lib.rs:

    UnaryOperator::Globally   => Bool::and(  [ psi_i for i in k..=bound ] )
    UnaryOperator::Eventually => Bool::or (  [ psi_i for i in k..=bound ] )

Only `X`, `U` and `R` consult the pes/opt/hpes/hopt flag.  So `G psi` is TRUE
whenever psi survives to the bound -- an OPTIMISTIC reading, in all four
semantics -- and a `forall`-quantified sufficiency claim is only ever checked as
far as the unrolling reaches.  Minimal reproducer, on the SYNTCOMP instance
ltl2dba_alpha7__e06's REAL module (9 states, `acc` is a sink at state 0 reached
after 8 steps):

    Forall pi . (G (~ acc[pi]))      k=6  -> sat   (WRONG: state 0 is reachable)
                                     k=8  -> unsat (right)

with the same answer under -s pes, opt, hpes and hopt.  The consequence for the
pipeline is that HyperQB happily returns `sat` for MSR with an R that is not
sufficient (the counterexample lives past the bound) or not minimal (the
witness for "no proper subset works" lives past the bound too).  Measured on
the SYNTCOMP benchmark: 37 of 818 recorded rows.

WHAT WE DO ABOUT IT
-------------------
HyperQB stays the SEARCH engine; this module is the CHECKER.  A candidate R is
cheap to check even though it was expensive to find, and the check is exact --
plain automaton emptiness, no bound:

  SUFFICIENCY      L(system) & L(pins(R, pi)) & L(!Phi)  =  empty
  MINIMALITY       no proper subset of R is sufficient:

      Bad  = { tau2 : SOME system trace agreeing with pi on tau2 violates Phi }
           = project_tau2 ( L(system) & L(agree(tau2, pi)) & L(!Phi) )
      Good = complement(Bad)                    -- the sufficient reasons
      R is minimal   <=>   Good & { tau2 : tau2 (=  R, tau2 != R }  =  empty

`pins`/`agree` are AUTOMATA, not chains of `X`s, so a pin inside the loop is
handled as the infinite modulo-periodic set of positions it really is -- and
the subset automaton may drop a SINGLE OCCURRENCE of such a pin, which is what
distinguishes a real MSR from the liveness case where the sufficient reasons
form an infinite descending chain and no minimal one exists.

Verified against the benchmark's independent AutoHyper checker
(bench/scripts/verify_msr.py): same verdict on every row, ~50x faster.

RUNNING WITHOUT spot IN-PROCESS
-------------------------------
tracemsr runs on the system python; spot usually lives in a micromamba
environment.  `certify()` shells out to $SPOT_ENV_PY when the bindings are not
importable here, the same trick smv_formulas.normalize_effect uses.  If neither
is available it returns UNAVAILABLE, and the caller must not upgrade a bounded
verdict on its own.

That subprocess is the ONLY process this module can start, it is a leaf (spot
runs in-process there; converter and smv_builder shell out to nothing), and it
goes through proc_guard so a timeout or a Ctrl-C takes it down with everything
else -- see _certify_via_spot_env.  Under the benchmark, which already runs on
the spot interpreter, the branch is never taken and no process is started at
all.
"""
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import converter            # noqa: E402
import proc_guard           # noqa: E402  (subprocess.run that kills the group)
import smv_builder          # noqa: E402  (parse_lasso)

SPOT_ENV_PY = os.environ.get(
    "SPOT_ENV_PY", os.path.expanduser("~/micromamba/envs/spot/bin/python3"))

#: A pin proposition is the input's name plus this suffix, in the joint
#: alphabet the minimality construction runs over.  `.` is legal in a HOA AP
#: name and cannot collide with an input name (converter rejects those).
PIN_SUFFIX = ".pin"

#: Complementation is worst-case exponential.  Above this many states in `Bad`
#: we decline to decide minimality rather than hang; the caller then reports a
#: sufficient reason with minimality open instead of claiming an MSR.
MAX_COMPLEMENT_STATES = 400

UNAVAILABLE = "spot-unavailable"


# --------------------------------------------------------------------------- #
# The frame: one finite window of pi, then pi's loop forever
# --------------------------------------------------------------------------- #
def frame(pi_prefix, pi_loop, tau_prefix, tau_cycle):
    """(chain_pi, chain_tau, loop_start) -- the lasso both R and pi live on.

    Two candidate shapes have to be certified, and this puts them on one frame:

      PERIODIC R (`reason_complete`): tau_prefix/tau_cycle already line up with
      pi's own prefix and loop, so the frame IS pi's lasso.

      FINITE R (a truncated bounded witness): the pins are a finite list that
      may run past pi's loop start.  pi is then unrolled far enough to cover
      them AND to land back on the loop start, and the periodic tail pins
      nothing.  Certifying that shape is not a formality: a finite R that turns
      out to be sufficient is a real reason, so a witness the solver could only
      print a prefix of is sometimes rescued rather than discarded."""
    P, L = len(pi_prefix), len(pi_loop)
    if not L:
        raise ValueError("pi has no loop -- not a lasso")
    if tau_cycle:
        if len(tau_prefix) != P or len(tau_cycle) != L:
            raise ValueError("periodic R does not line up with pi's lasso")
        return list(pi_prefix) + list(pi_loop), \
            [set(s) for s in tau_prefix] + [set(s) for s in tau_cycle], P
    n = P
    while n < len(tau_prefix):
        n += L
    chain_pi = [pi_prefix[t] if t < P else pi_loop[(t - P) % L] for t in range(n)]
    chain_tau = [set(tau_prefix[t]) if t < len(tau_prefix) else set()
                 for t in range(n)]
    return chain_pi + list(pi_loop), chain_tau + [set() for _ in pi_loop], n


# --------------------------------------------------------------------------- #
# Automata (built as HOA text: no spot API churn, and they are readable)
# --------------------------------------------------------------------------- #
def _head(aps, n, acc):
    return ["HOA: v1", f"States: {n}", "Start: 0",
            f"AP: {len(aps)} " + " ".join(f'"{a}"' for a in aps)] + acc + \
           ["--BODY--"]


def _lit(i, positive):
    return f"{i}" if positive else f"!{i}"


def pins_hoa(chain_pi, chain_tau, loop_start, inputs):
    """L(pins(R, pi)): the words that agree with pi wherever R pins."""
    n, idx = len(chain_pi), {a: i for i, a in enumerate(inputs)}
    lines = _head(inputs, n, ["acc-name: all", "Acceptance: 0 t"])
    for t in range(n):
        lits = [_lit(idx[a], a in chain_pi[t])
                for a in inputs if a in chain_tau[t]]
        lines += [f"State: {t}",
                  f"[{'&'.join(lits) or 't'}] {t + 1 if t + 1 < n else loop_start}"]
    return "\n".join(lines + ["--END--"])


def agree_hoa(chain_pi, loop_start, inputs, sysaps):
    """Over the JOINT alphabet: `a.pin -> (a <-> pi_t(a))`, at every position.

    Unlike pins_hoa the pin set is not fixed -- it is read off the word -- which
    is what lets the minimality query quantify over all tau2 at once."""
    n = len(chain_pi)
    aps = list(sysaps) + [a + PIN_SUFFIX for a in inputs]
    idx = {a: i for i, a in enumerate(aps)}
    lines = _head(aps, n, ["acc-name: all", "Acceptance: 0 t"])
    for t in range(n):
        cs = [f"(!{idx[a + PIN_SUFFIX]} | {_lit(idx[a], a in chain_pi[t])})"
              for a in inputs]
        lines += [f"State: {t}",
                  f"[{'&'.join(cs) or 't'}] {t + 1 if t + 1 < n else loop_start}"]
    return "\n".join(lines + ["--END--"])


def word_hoa(chain_tau, loop_start, inputs):
    """The single pin-word R, as an automaton over the pin alphabet."""
    n = len(chain_tau)
    aps = [a + PIN_SUFFIX for a in inputs]
    idx = {a: i for i, a in enumerate(aps)}
    lines = _head(aps, n, ["acc-name: all", "Acceptance: 0 t"])
    for t in range(n):
        lits = [_lit(idx[a + PIN_SUFFIX], a in chain_tau[t]) for a in inputs]
        lines += [f"State: {t}",
                  f"[{'&'.join(lits) or 't'}] {t + 1 if t + 1 < n else loop_start}"]
    return "\n".join(lines + ["--END--"])


def strict_subsets_hoa(chain_tau, loop_start, inputs):
    """Every tau2 with tau2 (= R pointwise and tau2 != R at some position.

    State = (position, "has dropped something yet").  The dropped flag is
    monotone, so "differs somewhere" is a reachability question and Buchi
    acceptance on the flag expresses it exactly.  Note what the cycling frame
    buys: at each PASS through a loop position the word may drop that pin or
    not, independently -- so a single occurrence of a periodic pin is a subset
    this automaton contains, and the answer distinguishes a genuine MSR from
    the liveness case where dropping one occurrence still suffices."""
    n = len(chain_tau)
    aps = [a + PIN_SUFFIX for a in inputs]
    idx = {a: i for i, a in enumerate(aps)}
    lines = _head(aps, 2 * n, ["acc-name: Buchi", "Acceptance: 1 Inf(0)"])
    for t in range(n):
        nxt = t + 1 if t + 1 < n else loop_start
        off = [f"!{idx[a + PIN_SUFFIX]}" for a in inputs if a not in chain_tau[t]]
        on = [f"{idx[a + PIN_SUFFIX]}" for a in inputs if a in chain_tau[t]]
        keep = "&".join(off + on) or "t"
        drop = "&".join(off + [f"!({'&'.join(on)})"]) if on else None
        for d in (0, 1):
            lines.append(f"State: {2 * t + d}" + (" {0}" if d else ""))
            lines.append(f"[{keep}] {2 * nxt + d}")
            if drop:
                lines.append(f"[{drop}] {2 * nxt + 1}")
    return "\n".join(lines + ["--END--"])


# --------------------------------------------------------------------------- #
# In-process (spot) implementation
# --------------------------------------------------------------------------- #
def pi_lasso(path):
    """pi's (prefix, loop) projected onto the inputs.  No spot needed, so a
    caller without the bindings can still put a certified R on pi's frame."""
    inputs, _aps, _s, _st = converter.parse_hoa(path)
    lasso_aps, prefix, loop = smv_builder.parse_lasso(path)

    def true_inputs(idx):
        return {ip for ip in inputs
                if ip in lasso_aps and lasso_aps.index(ip) in idx}

    return [true_inputs(s) for s in prefix], [true_inputs(s) for s in loop]


def _instance(path):
    """(system automaton, !Phi automaton, sorted inputs, pi's lasso)."""
    import spot
    with open(path, errors="replace") as f:
        c = f.read()
    eff = re.search(r'\[effect\]\s*\n\s*(.+)', c)
    hoa = re.search(r'(HOA:.*--END--)', c, re.DOTALL)
    if not (eff and hoa):
        raise ValueError(f"{path}: no [effect] or no HOA system block")
    inputs, _aps, _s, _st = converter.parse_hoa(path)
    return (spot.automaton(hoa.group(1)),
            spot.translate(spot.formula("!(%s)" % eff.group(1).strip())),
            sorted(inputs), pi_lasso(path))


def _certify_here(path, tau_prefix, tau_cycle, want_minimal=True):
    import spot
    system, not_phi, inputs, (pi_p, pi_c) = _instance(path)
    chain_pi, chain_tau, start = frame(pi_p, pi_c, tau_prefix, tau_cycle)

    pins = spot.automaton(pins_hoa(chain_pi, chain_tau, start, inputs))
    sufficient = spot.product(spot.product(system, pins), not_phi).is_empty()
    out = {"sufficient": sufficient, "minimal": None, "note": ""}
    if not sufficient:
        out["note"] = ("a system trace agrees with pi everywhere R pins and "
                       "still violates the effect")
        return out
    if not want_minimal:
        return out

    # ap_name(), not str(): str() is spot's printed form and quotes names like
    # "Go" -- written into a HOA AP list that came out as `""Go""`, and compared
    # against `keep` it never matched.
    agree = spot.automaton(agree_hoa(chain_pi, start, inputs,
                                     [a.ap_name() for a in system.ap()]))
    bad = spot.product(spot.product(system, agree), not_phi)
    keep = {a + PIN_SUFFIX for a in inputs}
    strip = spot.remove_ap()
    for a in bad.ap():
        if a.ap_name() not in keep:
            strip.add_ap(str(a))
    bad = strip.strip(bad).postprocess("Small")
    if bad.num_states() > MAX_COMPLEMENT_STATES:
        out["note"] = (f"minimality not decided: complementing a "
                       f"{bad.num_states()}-state automaton was declined "
                       f"(cap {MAX_COMPLEMENT_STATES})")
        return out
    good = spot.complement(bad)
    subsets = spot.automaton(strict_subsets_hoa(chain_tau, start, inputs))
    out["minimal"] = spot.product(good, subsets).is_empty()
    if not out["minimal"]:
        out["note"] = "a proper subset of R is also sufficient"
    return out


def sufficient_here(path, tau_prefix, tau_cycle):
    """Sufficiency only -- the cheap half, no complementation."""
    return _certify_here(path, tau_prefix, tau_cycle, False)["sufficient"]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def available():
    try:
        import spot                                        # noqa: F401
        return True
    except ImportError:
        return os.path.exists(SPOT_ENV_PY)


def certify(path, tau_prefix, tau_cycle, want_minimal=True, timeout=120):
    """{'sufficient': bool, 'minimal': bool|None, 'note': str} or UNAVAILABLE.

    `minimal` is None when it was not asked for or could not be decided; it is
    never guessed.  R is given the way the pipeline holds it: `tau_prefix` a
    list of pinned-input sets and `tau_cycle` the repeating block (empty for a
    finite R)."""
    try:
        import spot                                        # noqa: F401
    except ImportError:
        return _certify_via_spot_env(path, tau_prefix, tau_cycle,
                                     want_minimal, timeout)
    return _certify_here(path, tau_prefix, tau_cycle, want_minimal)


def _certify_via_spot_env(path, tau_prefix, tau_cycle, want_minimal, timeout):
    if not os.path.exists(SPOT_ENV_PY):
        return UNAVAILABLE
    payload = json.dumps({"path": path,
                          "tau_prefix": [sorted(s) for s in tau_prefix],
                          "tau_cycle": [sorted(s) for s in tau_cycle],
                          "want_minimal": bool(want_minimal)})
    code = ("import sys, json; sys.path.insert(0, %r); import msr_certify as m; "
            "a = json.loads(sys.argv[1]); "
            "print(json.dumps(m._certify_here(a['path'], "
            "[set(s) for s in a['tau_prefix']], [set(s) for s in a['tau_cycle']], "
            "a['want_minimal'])))" % _HERE)
    try:
        # proc_guard.run, not subprocess.run: this is the only process this
        # module starts, and it must die with the run. It is a LEAF -- spot is
        # in-process there and neither converter nor smv_builder spawns
        # anything -- so the group kill has nothing to chase; it is used
        # anyway, because every other spawn in the tool goes through it and a
        # lone exception is how the next leak gets written. `complement` is the
        # part that can run long, which is exactly when an interrupt lands.
        r = proc_guard.run([SPOT_ENV_PY, "-c", code, payload], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return UNAVAILABLE
    if r.returncode != 0 or not r.stdout.strip():
        return UNAVAILABLE
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except ValueError:
        return UNAVAILABLE


# --------------------------------------------------------------------------- #
def shrink(path, tau_prefix, tau_cycle):
    """Drop pins from a SUFFICIENT R while it stays sufficient.

    Needs spot in-process (it is |R| emptiness checks in a loop, which is not
    worth a subprocess each); returns R unchanged when the bindings are not
    importable here.

    Greedy, one whole pin at a time (a loop pin is one pin: it recurs), so it
    terminates in |R| checks and the result is still a lasso the pipeline can
    print.  It cannot rescue every non-minimal R -- dropping a single OCCURRENCE
    of a loop pin is also a proper subset, and where that is what suffices no
    minimal reason exists at all -- so the caller must still certify the result
    rather than assume it is minimal."""
    try:
        import spot                                        # noqa: F401
    except ImportError:
        return [set(s) for s in tau_prefix], [set(s) for s in tau_cycle]
    tp = [set(s) for s in tau_prefix]
    tc = [set(s) for s in tau_cycle]
    for block, t, ap in [("p", t, a) for t, s in enumerate(tp) for a in sorted(s)] + \
                        [("c", t, a) for t, s in enumerate(tc) for a in sorted(s)]:
        target = (tp if block == "p" else tc)[t]
        target.discard(ap)
        if not sufficient_here(path, tp, tc):
            target.add(ap)
    return tp, tc


# --------------------------------------------------------------------------- #
def self_test(path):
    """Check the checker, on any instance, with no solver in the loop.

    Three facts hold for every instance and are independent of what any solver
    would answer, so a disagreement is a bug HERE:

      1. R = every input pinned at every position is sufficient whenever pi
         itself satisfies the effect (the only trace that can agree with pi
         everywhere is pi -- the REAL system is input-deterministic).  If it is
         not sufficient there is nothing to certify on this instance and the
         test says so rather than passing vacuously.
      2. shrink() of a sufficient R is still sufficient, and dropping any one
         further pin from it is NOT -- that is what shrink means.
      3. A strict SUPERSET of a sufficient R is sufficient and NOT minimal.
    """
    pi_p, pi_c = pi_lasso(path)
    inputs, _aps, _s, _st = converter.parse_hoa(path)
    inputs = sorted(inputs)
    full_p = [set(inputs) for _ in pi_p]
    full_c = [set(inputs) for _ in pi_c]
    bad = 0

    def show(label, got, want):
        nonlocal bad
        ok = got is want
        bad += 0 if ok else 1
        print(f"  {label:<52} {str(got):<6} "
              f"{'as expected' if ok else '*** WRONG ***'}")

    print(f"self-test on {os.path.basename(path)}  (inputs: "
          f"{', '.join(inputs) or 'none'})\n")
    if not sufficient_here(path, full_p, full_c):
        print("  pi does not satisfy the effect on this instance -- pinning "
              "everything is not\n  sufficient, so there is no reason to "
              "certify here. Pick another instance.")
        return 1
    show("pin everything -- sufficient?", True, True)

    tp, tc = shrink(path, full_p, full_c)
    cert = _certify_here(path, tp, tc)
    show("shrunk R -- sufficient?", cert["sufficient"], True)
    pins = [("p", t, a) for t, st in enumerate(tp) for a in sorted(st)] + \
           [("c", t, a) for t, st in enumerate(tc) for a in sorted(st)]
    if pins:
        blk, t, a = pins[0]
        dp, dc = [set(x) for x in tp], [set(x) for x in tc]
        (dp if blk == "p" else dc)[t].discard(a)
        show(f"shrunk R without {a}@{t}{'*' if blk == 'c' else ''} -- sufficient?",
             _certify_here(path, dp, dc, False)["sufficient"], False)
    else:
        print("  shrunk R is empty (the effect holds on every trace); no pin "
              "to drop")

    missing = next(((blk, t, a) for blk, chain in (("p", tp), ("c", tc))
                    for t, st in enumerate(chain) for a in inputs
                    if a not in st), None)
    if missing:
        blk, t, a = missing
        ap_, ac = [set(x) for x in tp], [set(x) for x in tc]
        (ap_ if blk == "p" else ac)[t].add(a)
        sup = _certify_here(path, ap_, ac)
        show(f"shrunk R plus {a}@{t}{'*' if blk == 'c' else ''} -- sufficient?",
             sup["sufficient"], True)
        show(f"shrunk R plus {a}@{t}{'*' if blk == 'c' else ''} -- minimal?",
             sup["minimal"], False)
    else:
        print("  shrunk R already pins everything; no superset to build")

    print("\n" + ("self-test PASSED" if not bad else "self-test FAILED"))
    return 1 if bad else 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Certify a candidate reason: exact, unbounded, spot only.")
    ap.add_argument("file")
    ap.add_argument("--self-test", action="store_true",
                    help="check the checker on this instance (no solver needed)")
    ap.add_argument("--reason", default="",
                    help="comma-separated <input>@<t>, a `*` marking a loop pin "
                         "(the benchmark CSV's `reason` column)")
    ap.add_argument("--no-minimality", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test(args.file))

    _sys, _np, _in, (pi_p, pi_c) = _instance(args.file)
    tp = [set() for _ in pi_p]
    tc = [set() for _ in pi_c]
    for item in filter(None, args.reason.split(",")):
        name, _, pos = item.rpartition("@")
        t = int(pos.rstrip("*"))
        if pos.endswith("*") or t >= len(tp):
            tc[t - len(tp)].add(name)
        else:
            tp[t].add(name)
    print(json.dumps(certify(args.file, tp, tc, not args.no_minimality),
                     indent=2))
