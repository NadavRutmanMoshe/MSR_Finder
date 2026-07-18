#!/usr/bin/env python3
"""
The MSR pipeline orchestrator.

Given a system (HOA), an LTL effect Phi and a concrete lasso trace pi, decide
whether a Minimal Sufficient Reason exists and produce it, via a waterfall of
HyperLTL queries solved by AutoHyper:

    MSR  ->  SR  ->  PI_VAL

AutoHyper resolves the i-th quantifier of a HyperLTL formula on the i-th input
system, so each quantifier gets a small purpose-built system instead of one
combined product:

  pi          CliqueQ -- a clique over the 2^IP input valuations, duplicated by
                         the __MSR_LASSO__ loop marker (2^IP * 2 states).  pi is
                         fixated to the user's lasso *in the formula*, over
                         inputs only (the effect is never evaluated on pi).
  tau, tau2   Clique  -- the plain 2^IP input clique: the candidate-cause space
                         (tau marks which inputs are pinned at each step).
  pi2, pi3    Real    -- the HOA unrolled to an explicit LTS over all APs; the
                         effect is evaluated here.  Building it runs the
                         input-determinism firewall.

  MSR:  exists pi . exists tau . forall pi2 . forall tau2 . exists pi3
        CliqueQ     Clique       Real         Clique        Real
  SR :  exists pi . exists tau . forall pi2      ->  CliqueQ Clique Real

PI_VAL (is the user's lasso a real system trace on which the effect holds?)
needs pi together with the outputs, so it runs on the legacy combined system
(converter.generate_lts + hyperLTL.generate_hq); it is only reached when both
MSR and SR are UNSAT, to tell "valid trace, no cause" from "invalid trace".
"""
import os
import re
import sys
import time
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import converter            # noqa: E402
import hyperLTL             # noqa: E402  (PI_VAL fallback)
import trace_analyzer       # noqa: E402  (witness/state-map helpers)
import system_builder       # noqa: E402  (Real + Clique builders)
import msr_systems          # noqa: E402  (CliqueQ builder)
import msr_formulas         # noqa: E402  (MSR / SR formulas)

REL_TOOL_PATH = "../external_tools/AutoHyper/app/AutoHyper"

# AutoHyper is a .NET apphost; if the runtime is a user-local ~/.dotnet install
# (no system dotnet), DOTNET_ROOT must point at it for the binary to start.
# Subprocesses inherit os.environ, so setting it here covers every caller.
if "DOTNET_ROOT" not in os.environ:
    _dn = os.path.expanduser("~/.dotnet")
    if os.path.exists(os.path.join(_dn, "dotnet")):
        os.environ["DOTNET_ROOT"] = _dn


def _exe():
    e = os.path.abspath(os.path.join(_HERE, REL_TOOL_PATH))
    if not os.path.exists(e) and os.path.exists(e + ".exe"):
        e += ".exe"
    return e


def _run(system_paths, hq_path, timeout):
    cmd = [_exe(), "--explicit", *system_paths, hq_path, "--witness"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return res.stdout, res.stderr


def _is_solver_error(out, err):
    """AutoHyper prints an ERROR block (e.g. 'Unexpected exit code by spot') to
    stdout while still exiting 0; treat that as a crash, not a verdict."""
    blob = (out or "") + "\n" + (err or "")
    return ("Unexpected exit code" in blob) or ("=== ERROR" in blob) or ("ERROR ==" in blob)


def _parse_witness(out, pi_map, tau_map, result):
    pi_m = re.search(r'pi:\s+(.*)', out)
    tau_m = re.search(r'tau:\s+(.*)', out)
    if pi_m:
        result["pi_string"] = trace_analyzer.format_trace_string(pi_m.group(1), pi_map)
        pi_p, pi_c = trace_analyzer.get_trace_steps_split(pi_m.group(1), pi_map)
    else:
        pi_p, pi_c = [], []
    if tau_m:
        result["tau_string"] = trace_analyzer.format_trace_string(tau_m.group(1), tau_map)
        tau_p, tau_c = trace_analyzer.get_trace_steps_split(tau_m.group(1), tau_map)
    else:
        tau_p, tau_c = [], []
    result["table_data"] = {"pi_prefix": pi_p, "pi_cycle": pi_c,
                            "tau_prefix": tau_p, "tau_cycle": tau_c}


def _layout(path):
    """Build the three systems and the per-quantifier orders."""
    cliqueq, _ = msr_systems.build_cause_system_with_q(path)
    clique, _ = system_builder.build_cause_system(path)
    real, _ = system_builder.build_real_system(path)
    sysmap = {"cliqueq": cliqueq, "clique": clique, "real": real}
    msr_order = ["cliqueq", "clique", "real", "clique", "real"]
    sr_order = ["cliqueq", "clique", "real"]
    return sysmap, msr_order, sr_order


def analyze(path, timeout):
    result = {"status": "ERROR", "effect": "Unknown", "pi_string": "",
              "tau_string": "", "table_data": None, "cause_vars": [],
              "error_msg": "", "timings": {}}
    timings = result["timings"]
    t_start = time.perf_counter()
    work_dir = os.path.dirname(os.path.abspath(path))

    # PHASE 0: build the systems (determinism firewall lives in the real-system
    # builder).
    try:
        t0 = time.perf_counter()
        sysmap, msr_order, sr_order = _layout(path)
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
    inputs, _, _, _ = converter.parse_hoa(path)
    result["cause_vars"] = sorted(inputs)
    pi_map = trace_analyzer.parse_system_string(sysmap["cliqueq"])
    tau_map = trace_analyzer.parse_system_string(sysmap["clique"])

    if not os.path.exists(_exe()):
        result["error_msg"] = "AutoHyper executable not found."
        return result

    # write each distinct system once
    sys_paths = {}
    for label, content in sysmap.items():
        p = os.path.join(work_dir, f"temp_msr_{label}.txt")
        with open(p, "w") as f:
            f.write(content)
        sys_paths[label] = p
    p_hq = os.path.join(work_dir, "temp_msr.hq")
    p_legacy_sys = os.path.join(work_dir, "temp_msr_legacy.txt")
    p_legacy_hq = os.path.join(work_dir, "temp_msr_legacy.hq")
    temp_files = list(sys_paths.values()) + [p_hq, p_legacy_sys, p_legacy_hq,
                                             "aut1.hoa", "autRes.hoa"]

    try:
        # STEP 1: MSR
        with open(p_hq, "w") as f:
            f.write(msr_formulas.generate_hq_variant(path, "MSR", "inputs"))
        order = [sys_paths[l] for l in msr_order]
        t0 = time.perf_counter()
        out, err = _run(order, p_hq, timeout)
        timings["MSR"] = time.perf_counter() - t0
        if _is_solver_error(out, err):
            result["status"] = "ERROR"
            result["error_msg"] = f"AutoHyper solver error at MSR.\n{(out or err)[:300]}"
            return result
        if "SAT" in out and "UNSAT" not in out:
            result["status"] = "SAT_MSR"
            _parse_witness(out, pi_map, tau_map, result)
            return result

        # STEP 2: SR
        with open(p_hq, "w") as f:
            f.write(msr_formulas.generate_hq_variant(path, "SR", "inputs"))
        order = [sys_paths[l] for l in sr_order]
        t0 = time.perf_counter()
        out, err = _run(order, p_hq, timeout)
        timings["SR"] = time.perf_counter() - t0
        if _is_solver_error(out, err):
            result["status"] = "ERROR"
            result["error_msg"] = f"AutoHyper solver error at SR.\n{(out or err)[:300]}"
            return result
        if "SAT" in out and "UNSAT" not in out:
            result["status"] = "SAT_SR"
            _parse_witness(out, pi_map, tau_map, result)
            return result

        # STEP 3: PI_VAL on the legacy combined system
        l_in, l_aps, l_start, l_states = converter.parse_hoa(path)
        with open(p_legacy_sys, "w") as f:
            f.write(converter.generate_lts(l_in, l_aps, l_start, l_states))
        with open(p_legacy_hq, "w") as f:
            f.write(hyperLTL.generate_hq(path, mode="PI_VAL"))
        t0 = time.perf_counter()
        out, err = _run([p_legacy_sys], p_legacy_hq, timeout)
        timings["PI_VAL"] = time.perf_counter() - t0
        if "SAT" in out and "UNSAT" not in out:
            result["status"] = "UNSAT_NO_CAUSE"
        elif "UNSAT" in out:
            result["status"] = "INVALID_PI"
        else:
            result["status"] = "ERROR"
            result["error_msg"] = f"Solver crashed.\nSTDERR: {err.strip()}"

    except subprocess.TimeoutExpired:
        result["status"] = "ERROR"
        result["error_msg"] = f"AutoHyper TIMED OUT (> {timeout}s)."
    finally:
        timings["total"] = time.perf_counter() - t_start
        for tf in temp_files:
            p = tf if os.path.isabs(tf) else os.path.join(work_dir, tf)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return result


class Pipeline:
    """Pipeline object: .analyze_trace(path) + a settable .TIMEOUT_SECONDS."""
    def __init__(self):
        self.TIMEOUT_SECONDS = 600

    def analyze_trace(self, path):
        return analyze(path, self.TIMEOUT_SECONDS)


MSR = Pipeline()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 msr_analyzer.py <file.txt> [timeout]")
        sys.exit(1)
    path = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    res = analyze(path, timeout)
    import json
    print(json.dumps({k: v for k, v in res.items() if k != "table_data"}, indent=2))
