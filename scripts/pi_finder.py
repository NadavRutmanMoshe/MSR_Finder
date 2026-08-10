#!/usr/bin/env python3
"""
pi_finder -- run TraceMSR *without* a user-supplied PI, plus LTL model checking.

The classic pipeline requires the user to write the actual trace (the [lasso]
block) into the .txt by hand.  This module removes that requirement:

  FIND (default)   exists pi . Phi        on the plain real system (--witness).
                   SAT   -> AutoHyper hands us a lasso that satisfies the
                            effect; we display it and run the MSR pipeline on
                            it (via a temporary synthesized .txt, deleted when
                            the run finishes).
                   UNSAT -> no trace of the system satisfies the effect at all
                            (equivalently: the system MODELS !Phi).

  CHECK            exists pi . !(Phi)     -- classic LTL model checking of Phi.
                   UNSAT -> every trace satisfies Phi: model checking PASSED.
                            (Causality is then degenerate: the empty cause is
                            sufficient on every trace.)
                   SAT   -> model checking FAILED; the witness is a
                            counterexample.  We then run the *same* process
                            with not(Phi) as the effect and ask for the MSR of
                            the violation -- "why did Phi break here?"

Both modes need only [inputs]/[effect]/[system]; a [lasso] block in the input
is ignored.

"""
import argparse
import os
import re
import sys
import time
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
for p in (_HERE, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import hyperLTL             # noqa: E402  (rename_effect)
import trace_analyzer       # noqa: E402  (parse_txt_effect, parse_system_string)
import cli_viewer           # noqa: E402  (print_cli_results)
import system_builder       # noqa: E402  (build_real_system)
import msr_analyzer         # noqa: E402  (the MSR pipeline)
import proc_guard           # noqa: E402  (subprocess.run that kills the group)


# --------------------------------------------------------------------------- #
# The synthesis / model-checking query:  exists pi . [!]( effect )
# --------------------------------------------------------------------------- #
def synthesis_formula(effect, negate=False):
    body = hyperLTL.rename_effect(effect, "pi")
    return f"exists pi.\n({'!' if negate else ''}({body}))"


def _decode_witness(out, state_map):
    """AutoHyper's `pi: (p ids) (c ids)` -> (prefix_steps, loop_steps) as lists
    of true-AP name-sets.  Tolerates an empty prefix group."""
    m = re.search(r'pi:\s+\(([\d\s]*)\)\s*\(([\d\s]*)\)', out)
    if not m:
        raise RuntimeError("SAT but no parsable pi witness in AutoHyper output.")
    to_steps = lambda g: [state_map.get(int(x), set()) - trace_analyzer.INTERNAL_VARS
                          for x in g.split()]
    return to_steps(m.group(1)), to_steps(m.group(2))


def format_lasso(prefix_steps, loop_steps):
    """Human-readable  {a,b}{} ({c})^w  string from AP name-set steps."""
    fmt = lambda steps: "".join(
        "{" + ",".join(sorted(s)) + "}" for s in steps)
    return f"{fmt(prefix_steps)} ({fmt(loop_steps)})^ω"


def find_trace(path, negate, timeout):
    """Ask AutoHyper for one lasso of the real system satisfying (the negated)
    effect.  Returns a dict: sat, prefix_steps, loop_steps, pi_string, seconds.
    Raises on solver error / timeout; ValueError on a non-deterministic HOA."""
    real, aps = system_builder.build_real_system(path)     # determinism firewall inside
    effect = trace_analyzer.parse_txt_effect(path)
    hq = synthesis_formula(effect, negate)

    work = os.path.dirname(os.path.abspath(path))
    p_sys = os.path.join(work, "temp_find_real.txt")
    p_hq = os.path.join(work, "temp_find.hq")
    try:
        with open(p_sys, "w") as f:
            f.write(real)
        with open(p_hq, "w") as f:
            f.write(hq)
        t0 = time.perf_counter()
        # proc_guard.run, not subprocess.run: killing AutoHyper on timeout leaves
        # its Spot children (autfilt/ltl2tgba) orphaned and still burning CPU.
        res = proc_guard.run(
            [msr_analyzer._exe(), "--explicit", p_sys, p_hq, "--witness"],
            timeout=timeout)
        secs = time.perf_counter() - t0
        out, err = res.stdout, res.stderr
        if msr_analyzer._is_solver_error(out, err):
            raise RuntimeError(f"AutoHyper solver error:\n{(out or err)[:300]}")
        if "SAT" not in out:
            raise RuntimeError(f"AutoHyper produced no verdict.\nSTDERR: {err.strip()[:300]}")
        if "UNSAT" in out:
            return {"sat": False, "seconds": secs, "aps": aps, "effect": effect}
        prefix_steps, loop_steps = _decode_witness(out, trace_analyzer.parse_system_string(real))
        return {"sat": True, "seconds": secs, "aps": aps, "effect": effect,
                "prefix_steps": prefix_steps, "loop_steps": loop_steps,
                "pi_string": format_lasso(prefix_steps, loop_steps)}
    finally:
        for tf in (p_sys, p_hq, os.path.join(work, "aut1.hoa"),
                   os.path.join(work, "autRes.hoa"), "aut1.hoa", "autRes.hoa"):
            if os.path.exists(tf):
                try:
                    os.remove(tf)
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
# Synthesizing a complete .txt (found lasso + optionally negated effect)
# --------------------------------------------------------------------------- #
def lasso_block(aps, prefix_steps, loop_steps):
    """Render the found lasso as a [lasso] block over the system AP order --
    the same t=0 convention as the real system, so it plugs straight into the
    pi fixation."""
    idx = lambda step: "{" + " ".join(
        str(i) for i, ap in enumerate(aps) if ap in step) + "}"
    ap_line = " ".join('"%s"' % a for a in aps)
    return ("[lasso]\n"
            f"AP: {ap_line}\n"
            f"Prefix: {' '.join(idx(s) for s in prefix_steps)}\n"
            f"Loop: {' '.join(idx(s) for s in loop_steps)}\n")


def synthesize_txt(path, out_path, aps, prefix_steps, loop_steps, effect_override=None):
    """Copy the input .txt, replacing (or inserting) the [lasso] block with the
    found trace, and optionally overriding the [effect] line (check mode)."""
    with open(path, "r") as f:
        content = f.read()
    block = lasso_block(aps, prefix_steps, loop_steps)
    if "[lasso]" in content:
        content = re.sub(r'\[lasso\].*?(?=\[system\])', block + "\n",
                         content, flags=re.DOTALL)
    else:
        content = content.replace("[system]", block + "\n[system]", 1)
    if effect_override is not None:
        content = re.sub(r'\[effect\]\s*\n?[^\n]*',
                         lambda _m: f"[effect]\n{effect_override}", content, count=1)
    with open(out_path, "w") as f:
        f.write(content)
    return out_path


# --------------------------------------------------------------------------- #
# The two user-facing flows
# --------------------------------------------------------------------------- #
_G = "\033[92m"; _Y = "\033[93m"; _R = "\033[91m"; _C = "\033[96m"; _0 = "\033[0m"


def _explain(synth_path, timeout, pi_override=None):
    """Run the MSR pipeline on a synthesized file and render it
    with the standard CLI viewer.

    `pi_override` is the trace we found ourselves, over the real system and
    carrying its outputs. The PI the MSR pipeline hands back is the same trace
    projected onto the INPUTS and re-unrolled, so it is strictly less
    informative -- we show ours instead of printing both."""
    msr_analyzer.MSR.TIMEOUT_SECONDS = timeout
    result = msr_analyzer.MSR.analyze_trace(synth_path)
    cli_viewer.print_cli_results(result, pi_override=pi_override)
    return result


def run_find(path, timeout):
    base = os.path.splitext(path)[0]
    found = find_trace(path, negate=False, timeout=timeout)
    if not found["sat"]:
        print(f"\n{_Y}UNSAT ({found['seconds']:.1f}s) — NO trace of the system "
              f"satisfies the effect.{_0}")
        print("  There is nothing to explain: the effect never happens on this system.")
        print("  (Equivalently: the system MODELS !Phi — this is a model-checking result.)")
        return None
    # A temporary .txt with the found PI as its [lasso]; removed after the run.
    synth = synthesize_txt(path, base + "_found_pi.txt", found["aps"],
                           found["prefix_steps"], found["loop_steps"])
    try:
        return _explain(synth, timeout, pi_override=found["pi_string"])
    finally:
        if os.path.exists(synth):
            os.remove(synth)


def run_check(path, timeout):
    base = os.path.splitext(path)[0]
    print(f"MODEL CHECKING Phi : {trace_analyzer.parse_txt_effect(path)}")
    found = find_trace(path, negate=True, timeout=timeout)
    if not found["sat"]:
        print(f"\n{_G}PASSED ({found['seconds']:.1f}s) — every trace of the system "
              f"satisfies Phi.{_0}")
        print("  Causality note: since Phi holds no matter what the inputs do, the")
        print("  EMPTY cause is sufficient on every trace (the MSR is trivially {}).")
        return None
    print(f"\n{_R}FAILED ({found['seconds']:.1f}s) — counterexample found "
          f"(looking for minimal sufficient reason):{_0}")

    # A temporary .txt: the counterexample as [lasso], effect := !Phi; removed
    # after the run.
    neg_effect = f"!({found['effect']})"
    synth = synthesize_txt(path, base + "_counterexample.txt", found["aps"],
                           found["prefix_steps"], found["loop_steps"],
                           effect_override=neg_effect)
    try:
        return _explain(synth, timeout, pi_override=found["pi_string"])
    finally:
        if os.path.exists(synth):
            os.remove(synth)


def main():
    ap = argparse.ArgumentParser(description="Find PI automatically / model-check the effect.")
    ap.add_argument("file", help="input .txt ([lasso] not required; ignored if present)")
    ap.add_argument("--check", action="store_true",
                    help="model-check Phi (exists pi . !Phi) instead of finding a PI for Phi")
    ap.add_argument("--both", action="store_true", help="run find, then check")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per AutoHyper call")
    args = ap.parse_args()

    try:
        if args.both:
            run_find(args.file, args.timeout)
            print()
            run_check(args.file, args.timeout)
        elif args.check:
            run_check(args.file, args.timeout)
        else:
            run_find(args.file, args.timeout)
    except ValueError as ve:                      # determinism firewall
        print(f"{_R}[!] {ve}{_0}")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"{_R}[!] AutoHyper TIMED OUT (> {args.timeout}s).{_0}")
        sys.exit(1)
    except RuntimeError as re_:
        print(f"{_R}[!] {re_}{_0}")
        sys.exit(1)


if __name__ == "__main__":
    main()
