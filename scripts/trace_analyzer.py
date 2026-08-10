import os
import re
import time
import subprocess
import converter
import hyperLTL
import proc_guard           # subprocess.run that kills the whole process group

# --- CONFIGURATION ---
# CAUSALITY_VAR marks the hypothetical alternative trace.
CAUSALITY_VAR = "__MSR_TAU__"
INTERNAL_VARS = {"__MSR_TAU__", "__MSR_LASSO__"}
REL_TOOL_PATH = "../external_tools/AutoHyper/app/AutoHyper"
# 100 seconds is very generous but necessary for 2EXPTIME LTL synthesis tasks.
TIMEOUT_SECONDS = 100

def parse_system_string(lts_content):
    """
    Parses the generated LTS file to map integer state IDs to their assigned Boolean variables.
    This is necessary to translate AutoHyper's numerical witness output back into human-readable APs.
    """
    state_map = {}
    state_pattern = re.compile(r'State:\s+(\d+)\s+\{(.*?)\}')
    for state_id, assignments in state_pattern.findall(lts_content):
        true_vars = set()
        for var_name, val in re.compile(r'\("(\w+)"\s+(true|false)\)').findall(assignments):
            if val == 'true': true_vars.add(var_name)
        state_map[int(state_id)] = true_vars
    return state_map

def get_cause_vars_from_system(state_map):
    """
    Scans the system to determine which physical variables exist.
    It filters out our internal marker variables (__MSR_TAU__, __MSR_LASSO__) 
    so they don't clutter the final CLI tables.
    """
    cause_vars = set()
    for _, vars_set in state_map.items():
        cause_vars.update(vars_set - INTERNAL_VARS)
    return sorted(list(cause_vars))

def parse_txt_effect(input_txt_path):
    """Safely extracts the [effect] formula string from the user's input file."""
    try:
        with open(input_txt_path, 'r') as f:
            m_effect = re.search(r'\[effect\]\s+(.*)', f.read())
            return m_effect.group(1).strip() if m_effect else "Unknown"
    except Exception: return "Unknown"

def format_trace_string(path_str, state_map):
    """
    Translates AutoHyper's raw witness trace (e.g., "(0 1) (2 3)") into 
    the mathematically standard AP set format (e.g., "{o1} ({i1,o2})^ω").
    """
    match = re.match(r'\(([\d\s]+)\)\s*\(([\d\s]+)\)', path_str.strip())
    if not match: return "Error parsing witness"
    
    prefix_ids = [int(x) for x in match.group(1).split()]
    cycle_ids = [int(x) for x in match.group(2).split()]

    def ids_to_str(ids):
        res = []
        for sid in ids:
            if sid not in state_map:
                res.append("{?}")
                continue
            # Remove our internal markers before displaying
            vset = state_map[sid].copy() - INTERNAL_VARS
            if not vset: res.append("{}")
            else: res.append("{" + ",".join(sorted(vset)) + "}")
        return "".join(res)
    return f"{ids_to_str(prefix_ids)} ({ids_to_str(cycle_ids)})^ω"

def get_trace_steps_split(path_str, state_map):
    """
    Splits the trace into a list of prefix sets and a list of cycle sets.
    This structure is passed to the CLI Viewer to render the comparative table columns.
    """
    match = re.match(r'\(([\d\s]+)\)\s*\(([\d\s]+)\)', path_str.strip())
    if not match: return [], []
    prefix_ids = [int(x) for x in match.group(1).split()]
    cycle_ids = [int(x) for x in match.group(2).split()]
    
    return [state_map.get(sid, set()) - INTERNAL_VARS for sid in prefix_ids], \
           [state_map.get(sid, set()) - INTERNAL_VARS for sid in cycle_ids]

def _run_autohyper(lts_content, hq_content, temp_sys, temp_hq, executable):
    """
    Helper function to safely invoke the AutoHyper binary with timeouts.
    Uses temporary files rather than standard input for robust C++ execution.
    """
    with open(temp_sys, 'w') as f: f.write(lts_content)
    with open(temp_hq, 'w') as f: f.write(hq_content)
    # proc_guard.run, not subprocess.run: killing AutoHyper on timeout leaves
    # its Spot children (autfilt/ltl2tgba) orphaned and still burning CPU.
    res = proc_guard.run([executable, "--explicit", temp_sys, temp_hq, "--witness"],
                         timeout=TIMEOUT_SECONDS)
    return res.stdout, res.stderr

def analyze_trace(input_txt_path):
    """
    THE ORCHESTRATOR.
    This executes the core 'Waterfall Verification Logic' required for Causality:
    1. Parse and validate determinism.
    2. Try to prove a Minimal Sufficient Reason (MSR) exists.
    3. If MSR fails, try to prove ANY Sufficient Reason (SR) exists.
    4. If both fail, verify the Pi trace is even a legal path.
    """
    result = {
        "status": "ERROR", "effect": "Unknown", "pi_string": "",
        "tau_string": "", "table_data": None, "cause_vars": [], "error_msg": "",
        "timings": {}
    }
    timings = result["timings"]
    t_start = time.perf_counter()

    # --- PHASE 0: Parsing and the Math Firewall ---
    try:
        t0 = time.perf_counter()
        inputs, aps, start_state, states = converter.parse_hoa(input_txt_path)
        # generate_lts intrinsically checks determinism before building the graph
        lts_content = converter.generate_lts(inputs, aps, start_state, states)
        timings["build"] = time.perf_counter() - t0
    except ValueError as ve:
        # If the check fails, catch the specific exception and route to the UI warning
        if "non-deterministic" in str(ve):
            result["status"] = "NON_DETERMINISTIC"
            result["error_msg"] = str(ve)
            return result
        else:
            result["error_msg"] = f"Validation Error: {ve}"
            return result
    except Exception as e:
        result["error_msg"] = f"Failed to generate pipeline files: {e}"
        return result

    state_map = parse_system_string(lts_content)
    result["effect"] = parse_txt_effect(input_txt_path)
    result["cause_vars"] = get_cause_vars_from_system(state_map)

    # Prepare temporary files for AutoHyper
    temp_sys = os.path.join(os.path.dirname(input_txt_path), "temp_sys.txt")
    temp_hq = os.path.join(os.path.dirname(input_txt_path), "temp_hq.hq")
    
    # Path resolution for the external C++ binary
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tool_executable = os.path.abspath(os.path.join(script_dir, REL_TOOL_PATH))
    if not os.path.exists(tool_executable) and os.path.exists(tool_executable + ".exe"):
        tool_executable += ".exe"

    if not os.path.exists(tool_executable):
        result["error_msg"] = "AutoHyper executable not found."
        return result

    # --- THE WATERFALL ---
    try:
        # STEP 1: Try Minimal Sufficient Reason (MSR)
        hq_msr = hyperLTL.generate_hq(input_txt_path, mode="MSR")
        t0 = time.perf_counter()
        out, err = _run_autohyper(lts_content, hq_msr, temp_sys, temp_hq, tool_executable)
        timings["MSR"] = time.perf_counter() - t0

        if "SAT" in out and "UNSAT" not in out:
            result["status"] = "SAT_MSR"
            _parse_witness(out, state_map, result)
            return result

        # STEP 2: Try Sufficient Reason (SR)
        # Executed only if MSR fails (e.g., infinite chain of decreasing SRs)
        hq_sr = hyperLTL.generate_hq(input_txt_path, mode="SR")
        t0 = time.perf_counter()
        out, err = _run_autohyper(lts_content, hq_sr, temp_sys, temp_hq, tool_executable)
        timings["SR"] = time.perf_counter() - t0

        if "SAT" in out and "UNSAT" not in out:
            result["status"] = "SAT_SR"
            _parse_witness(out, state_map, result)
            return result

        # STEP 3: Validate the Pi Trace
        # If no cause can be found, we must verify if the original trace was even physically possible.
        hq_pival = hyperLTL.generate_hq(input_txt_path, mode="PI_VAL")
        t0 = time.perf_counter()
        out, err = _run_autohyper(lts_content, hq_pival, temp_sys, temp_hq, tool_executable)
        timings["PI_VAL"] = time.perf_counter() - t0

        if "SAT" in out and "UNSAT" not in out:
            result["status"] = "UNSAT_NO_CAUSE" # Pi is a real path, but no causality exists
        elif "UNSAT" in out:
            result["status"] = "INVALID_PI" # The trace provided by the user violates HOA transition rules
        else:
            result["status"] = "ERROR"
            result["error_msg"] = f"Solver crashed.\nSTDERR: {err.strip()}"

    except subprocess.TimeoutExpired:
        # Failsafe for mathematically intensive formulas that cause state-space explosion
        result["status"] = "ERROR"
        result["error_msg"] = f"AutoHyper TIMED OUT (> {TIMEOUT_SECONDS}s)."
    finally:
        timings["total"] = time.perf_counter() - t_start
        # Cleanup temporary files
        for tf in [temp_sys, temp_hq, "aut1.hoa", "autRes.hoa"]:
            if os.path.exists(tf):
                try: os.remove(tf)
                except OSError: pass

    return result

def _parse_witness(output, state_map, result_dict):
    """
    Helper function to locate the 'pi:' and 'tau:' witness strings from AutoHyper
    and route them through the formatters so the UI can build the comparison tables.
    """
    pi_match = re.search(r'pi:\s+(.*)', output)
    tau_match = re.search(r'tau:\s+(.*)', output)
    
    if pi_match:
        result_dict["pi_string"] = format_trace_string(pi_match.group(1), state_map)
        pi_p, pi_c = get_trace_steps_split(pi_match.group(1), state_map)
    else: pi_p, pi_c = [], []

    if tau_match:
        result_dict["tau_string"] = format_trace_string(tau_match.group(1), state_map)
        tau_p, tau_c = get_trace_steps_split(tau_match.group(1), state_map)
    else: tau_p, tau_c = [], []

    result_dict["table_data"] = {
        "pi_prefix": pi_p, "pi_cycle": pi_c, "tau_prefix": tau_p, "tau_cycle": tau_c
    }