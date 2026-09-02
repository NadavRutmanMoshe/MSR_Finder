#!/usr/bin/env python3
"""
The MSR pipeline on HyperQB -- the `--hyperQB` backend of tracemsr.

Same question as msr_analyzer, same waterfall (MSR -> SR -> PI_VAL), same result
dictionary, so cli_viewer renders either backend unchanged.  What differs is the
solver and, because of the solver, three things:

  SYSTEMS.  AutoHyper gets explicit LTSs (system_builder / msr_systems);
  HyperQB gets the symbolic NuSMV modules of smv_builder.  The HOA is never
  unrolled into an exponential explicit transition system on our side.

  pi.  AutoHyper pins pi to the user's lasso *in the formula*.  HyperQB cannot:
  that pin is a positively-asserted `G`, and under bounded pessimistic semantics
  a positively-asserted `G` is satisfied only when a loop is found inside the
  unrolling bound.  It never is, so the formula-side layout returns `unsat` at
  every bound on every instance -- fast, and always wrong.  Here pi's *model* is
  the lasso (build_pi_smv is a counter with exactly one trace), which is an
  exact restatement rather than a bounded relaxation.

  WHAT A VERDICT MEANS.  HyperQB is a bounded model checker.  `sat` means a
  witness was found inside the bound, which is a proof.  `unsat` means only
  "no witness within k" -- raising k can turn it into `sat`, and on these
  queries it demonstrably does.  So this module reports:

      sat at MSR      -> SAT_MSR        (a real minimal sufficient reason)
      sat at SR       -> SAT_SR         (a real sufficient reason -- but read
                                         the next paragraph)
      anything else   -> UNKNOWN_BOUNDED

  SAT_SR MEANS SOMETHING WEAKER HERE than it does on the exact backend.  There,
  it is reached by PROVING the MSR query unsat, so it says "no minimal reason
  exists" -- the liveness case, where the sufficient reasons form an infinite
  descending chain.  Here it is reached by not finding an MSR witness inside
  the bound, which is not a proof of anything.  The reason reported is
  genuinely sufficient; whether a minimal one exists is simply open.
  cli_viewer prints the two cases differently for exactly this reason.

  It never reports UNSAT_NO_CAUSE or INVALID_PI, because a bounded `unsat`
  does not support either claim.  Use the default AutoHyper backend when you
  need a negative answer to mean something.

RAISING THE BOUND IS NOT A FREE WIN.  A larger k gives the witness more room
(so R comes back complete more often), but the MSR query is the expensive one
and it degrades first: measured on the shipped examples, k=11 turns
atva2022_arbiter, atva2022_arbiter_simple and example_6_odd from SAT_MSR into
SAT_SR, and example_6_globally into UNKNOWN_BOUNDED, because the MSR query stops
answering while the cheaper SR query still does.  Raise k to complete a
truncated reason, but re-read the status afterwards.

THE BOUND.  Cost explodes in k (measured on one arbiter: 0.17s at k=3, 1.18s at
k=6, 20.08s at k=8, timeout at k=12), so the default is the smallest bound that
can express the query at all -- |prefix| + |loop| + 2, enough to walk the whole
lasso and close its loop once.  `--k` overrides it.

THE WITNESS.  HyperQB's `-c` model dump contains VARs only, never DEFINEs.  In
this layout that means tau -- the cause, the thing we actually want -- decodes
directly (the clique module's inputs are real VARs), while pi's propositions do
not (pi's only VAR is its counter).  pi is not lost, though: it *is* the user's
lasso, and the dumped counter says which lasso position each step sits at, so
the trace beside the cause is reconstructed exactly rather than guessed.
"""
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import converter            # noqa: E402  (parse_hoa, determinism firewall)
import hyperqb_backend as hq  # noqa: E402  (syntax translation + the runner)
import smv_builder          # noqa: E402  (the NuSMV modules)
import smv_formulas         # noqa: E402  (the HyperLTL queries)
import trace_analyzer       # noqa: E402  (parse_txt_effect)

#: Quantifier order -> module, mirroring msr_analyzer._layout.  HyperQB, like
#: AutoHyper, resolves the i-th quantifier on the i-th system.
ORDERS = {
    "PI_VAL": ["pival"],
    "SR":     ["pi", "clique", "real"],
    "MSR":    ["pi", "clique", "real", "clique", "real"],
}


def default_bound(path):
    """The smallest bound that can express the query: walk the whole lasso and
    close its loop once, plus two steps of slack for the effect to occur.

    Deliberately not `2 * (|prefix| + |loop|) + 4`: solve time explodes in k, so
    a generous default turns instances that answer in a second into timeouts."""
    _aps, prefix, loop = smv_builder.parse_lasso(path)
    return max(3, len(prefix) + len(loop) + 2)


# --------------------------------------------------------------------------- #
# Systems
# --------------------------------------------------------------------------- #
def _build(path):
    """The four NuSMV modules, with any HyperQB-illegal AP names renamed.

    The rename is applied to the models AND to the formula, so the two stay in
    step; `back` (in _decode_tau) undoes it for display."""
    inputs, aps, _s, _st = converter.parse_hoa(path)
    rename = hq.rename_map(list(aps))
    real, rmeta = smv_builder.build_real_smv(path)      # determinism firewall
    pi, pmeta = smv_builder.build_pi_smv(path)
    clique, _ = smv_builder.build_cause_smv(path)
    pival, vmeta = smv_builder.build_pival_smv(path)
    models = {k: hq.sanitize_model(v, rename) for k, v in
              {"real": real, "pi": pi, "clique": clique, "pival": pival}.items()}
    return models, rename, inputs, rmeta, pmeta, vmeta


# --------------------------------------------------------------------------- #
# Witness decoding
# --------------------------------------------------------------------------- #
def _decode_tau(states, inputs, rename):
    """The `-c` dump -> one set of pinned inputs per time step."""
    back = {v: k for k, v in rename.items()}
    steps = []
    for t in sorted(states):
        pinned = set()
        for var, val in states[t].items():
            m = re.match(r'^(.*)_(\d+)_([A-Za-z][A-Za-z0-9_]*)$', var)
            if not m or m.group(3) != "tau" or int(m.group(2)) != t:
                continue
            name = back.get(m.group(1), m.group(1))
            if name in inputs and val.lower() == "true":
                pinned.add(name)
        steps.append(pinned)
    return steps


def _pi_steps(pmeta, n):
    """The user's lasso, projected onto the inputs and unrolled to n steps.

    pi's model is that lasso, so this is not a reconstruction from the witness
    -- it is the same object the solver ran on."""
    chain, start = pmeta["chain"], pmeta["loop_start"]
    period = len(chain) - start
    out = []
    for t in range(n):
        pos = t if t < start else start + (t - start) % period
        out.append(set(chain[pos]))
    return out


def _fmt_lasso(prefix_steps, loop_steps):
    """`{a}{b} ({c})^w`, or `{a}{b}…` when there is no loop to name.

    An empty `()^w` would read as "and then nothing forever", which is a claim.
    The flat shape means the opposite: the witness stops here and says nothing
    about what follows."""
    fmt = lambda steps: "".join("{" + ",".join(sorted(s)) + "}" for s in steps)
    if not loop_steps:
        return f"{fmt(prefix_steps)}…"
    return f"{fmt(prefix_steps)} ({fmt(loop_steps)})^ω"


def _shape_witness(tau_steps, pmeta, result):
    """Fill result['table_data'] and the pi/tau strings from the decoded tau.

    Two shapes, and which one we get is a fact about the witness, not a choice:

      LASSO.  If tau repeats with the lasso's own period over the part of the
      bound we can see, the cause is periodic and is shown the way the AutoHyper
      backend shows it -- prefix rows, then a repeating block.  R is then an
      infinite set and prints as one.

      FLAT.  Otherwise the bounded witness simply does not say what tau does
      after step k, so every decoded step is printed as a prefix row and the
      repeating block is left empty.  What is reported is then a PREFIX OF R,
      not R: `reason_complete` is set False, and no reader -- the CLI table, a
      benchmark row -- may treat those pins as the whole reason.
      They generally are not sufficient on their own; a `G`-shaped effect needs
      pins that recur forever, and none of that is inside the bound.

    Guessing a period from a repeated valuation (the usual BMC presentation
    trick) would state more than the witness supports, so we do not.

    Note what is NOT in doubt either way: the verdict.  `sat` says an MSR
    exists and the solver held a witness for it.  Only our ability to PRINT all
    of that witness is bounded.
    """
    start, period = pmeta["loop_start"], len(pmeta["chain"]) - pmeta["loop_start"]
    n = len(tau_steps)
    pi_steps = _pi_steps(pmeta, n)

    periodic = n >= start + 2 * period and all(
        tau_steps[t] == tau_steps[t + period]
        for t in range(start, n - period))

    result["reason_complete"] = periodic
    if periodic:
        pi_p, pi_c = pi_steps[:start], pi_steps[start:start + period]
        tau_p, tau_c = tau_steps[:start], tau_steps[start:start + period]
    else:
        pi_p, pi_c = pi_steps, []
        tau_p, tau_c = tau_steps, []
        result["bounded_note"] = (
            f"R BELOW IS TRUNCATED, not the whole reason. The witness is a "
            f"bounded prefix and is not periodic in the lasso's period, so what "
            f"the cause does past the bound is unknown. The pins shown are part "
            f"of a real minimal sufficient reason (the SAT verdict is a proof "
            f"that one exists), but on their own they need not be sufficient. "
            f"To see all of R: raise the bound in steps of 2*|loop| "
            f"(--k {len(pi_steps) - 1 + 2 * period} next), or re-run without "
            f"--hyperQB.")

    result["table_data"] = {"pi_prefix": pi_p, "pi_cycle": pi_c,
                            "tau_prefix": tau_p, "tau_cycle": tau_c}
    result["pi_string"] = _fmt_lasso(pi_p, pi_c)
    result["tau_string"] = _fmt_lasso(tau_p, tau_c)


# --------------------------------------------------------------------------- #
# The waterfall
# --------------------------------------------------------------------------- #
def analyze(path, timeout, k=None, semantics="pes"):
    result = {"status": "ERROR", "effect": "Unknown", "pi_string": "",
              "tau_string": "", "table_data": None, "cause_vars": [],
              "error_msg": "", "timings": {}, "solver": "HyperQB",
              "bounded_note": ""}
    timings = result["timings"]
    t_start = time.perf_counter()
    work_dir = os.path.dirname(os.path.abspath(path))

    if not hq.available():
        result["error_msg"] = (
            f"HyperQB binary not found at {hq.binary()}.\n"
            f"Build it (cargo build --release in a HyperRUSTY checkout) and put "
            f"it there, or point $HYPERQB_BIN at it. See the README, "
            f"'Optional: the HyperQB backend'.")
        return result

    # PHASE 0: the systems (the determinism firewall lives in build_real_smv).
    try:
        t0 = time.perf_counter()
        models, rename, inputs, rmeta, pmeta, vmeta = _build(path)
        timings["build"] = time.perf_counter() - t0
    except ValueError as ve:
        if "non-deterministic" in str(ve):
            result["status"] = "NON_DETERMINISTIC"
        result["error_msg"] = str(ve)
        return result
    except Exception as e:
        result["error_msg"] = f"Failed to build systems: {e}"
        return result

    result["effect"] = trace_analyzer.parse_txt_effect(path)
    result["cause_vars"] = sorted(inputs)
    k = k or default_bound(path)
    result["k"] = k
    result["semantics"] = semantics

    paths = {}
    for label, content in models.items():
        p = os.path.join(work_dir, f"temp_hq_{label}.smv")
        with open(p, "w") as f:
            f.write(content)
        paths[label] = p
    # HyperRUSTY drops a NuSMV command script called `commands.txt` into the
    # CURRENT DIRECTORY (not the work dir), so a run from scripts/ litters the
    # repository. Remember whether one was already there, so cleanup can tell
    # its own droppings from a file the user owns.
    leftover = os.path.abspath("commands.txt")
    pre_existing = os.path.exists(leftover)

    def query(mode):
        """One HyperQB call. Returns (verdict, states, seconds)."""
        # The REAL module's `valid` guard is dropped when the HOA is
        # input-complete: every guard atom doubles the alphabet, for nothing.
        meta = vmeta if mode == "PI_VAL" else rmeta
        valid = None if meta["valid_const"] else meta["valid_var"]
        ours = smv_formulas.generate_hq_smv(path, mode, valid_var=valid)
        order = [paths[l] for l in ORDERS[mode]]
        t0 = time.perf_counter()
        try:
            r = hq.run(order, hq.dualize(ours, rename), k=k, semantics=semantics,
                       witness=True, timeout=timeout, work_dir=work_dir)
        except subprocess.TimeoutExpired:
            return "timeout", {}, time.perf_counter() - t0
        return r["verdict"], r["states"], r["seconds"]

    try:
        # STEP 1: MSR.  Only `sat` is a result; see the module docstring.
        verdict, states, secs = query("MSR")
        timings["MSR"] = secs
        if verdict == "sat":
            result["status"] = "SAT_MSR"
            _shape_witness(_decode_tau(states, inputs, rename), pmeta, result)
            return result
        msr_verdict = verdict

        # STEP 2: SR.
        verdict, states, secs = query("SR")
        timings["SR"] = secs
        if verdict == "sat":
            result["status"] = "SAT_SR"
            _shape_witness(_decode_tau(states, inputs, rename), pmeta, result)
            return result
        sr_verdict = verdict

        # STEP 3: PI_VAL -- not a tiebreaker here, only extra information.  A
        # `sat` proves the lasso is a real system trace on which Phi holds, so
        # "no cause found" is about the bound and not about the trace; a
        # non-sat says nothing either way.
        verdict, _states, secs = query("PI_VAL")
        timings["PI_VAL"] = secs
        result["status"] = "UNKNOWN_BOUNDED"
        if verdict == "sat":
            result["bounded_note"] = (
                f"Pi IS a valid system trace satisfying the effect (PI_VAL sat, "
                f"a proof). No sufficient reason was found within the bound "
                f"k={k}: MSR {msr_verdict}, SR {sr_verdict}.")
        else:
            result["bounded_note"] = (
                f"Nothing was decided within the bound k={k}: MSR "
                f"{msr_verdict}, SR {sr_verdict}, PI_VAL {verdict}. Whether Pi "
                f"is even a valid trace of the system is therefore open too.")

    except subprocess.TimeoutExpired:
        result["status"] = "ERROR"
        result["error_msg"] = f"HyperQB TIMED OUT (> {timeout}s)."
    except RuntimeError as e:
        result["status"] = "ERROR"
        result["error_msg"] = str(e)
    finally:
        timings["total"] = time.perf_counter() - t_start
        for p in paths.values():
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        _clean_solver_droppings(leftover, pre_existing, paths.values())
    return result


def _clean_solver_droppings(path, pre_existing, model_paths):
    """Remove HyperRUSTY's `commands.txt` -- but only if it is HyperRUSTY's.

    Two guards, because the name is generic enough that someone could have their
    own: it must not have existed before this run, and its contents must name a
    model file WE wrote."""
    if pre_existing or not os.path.exists(path):
        return
    try:
        with open(path, "r", errors="replace") as f:
            body = f.read()
        if not body.startswith("read_model -i "):
            return
        if not any(os.path.basename(m) in body for m in model_paths):
            return
        os.remove(path)
    except OSError:
        pass


class Pipeline:
    """Same shape as msr_analyzer.MSR, so tracemsr can swap one for the other."""
    def __init__(self):
        self.TIMEOUT_SECONDS = 600
        self.K = None                  # None -> default_bound(path)
        self.SEMANTICS = "pes"

    def analyze_trace(self, path):
        return analyze(path, self.TIMEOUT_SECONDS, self.K, self.SEMANTICS)


MSR = Pipeline()


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="MSR on the HyperQB backend.")
    ap.add_argument("file")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--k", type=int, default=None,
                    help="unrolling bound (default |prefix|+|loop|+2)")
    ap.add_argument("--semantics", default="pes", choices=hq.SEMANTICS)
    args = ap.parse_args()
    res = analyze(args.file, args.timeout, args.k, args.semantics)
    print(json.dumps({k: v for k, v in res.items() if k != "table_data"},
                     indent=2))
