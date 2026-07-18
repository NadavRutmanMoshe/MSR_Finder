#!/usr/bin/env python3

import sys
import re

# --- CONFIGURATION ---
# These variables act as markers in the resulting HyperLTL formula
SINK_VAR_DEFAULT = "__MSR_TAU__"    # Marker for the Cause/Reason (Tau)
LASSO_VAR_DEFAULT = "__MSR_LASSO__" # Marker for the loop state in the trace (Pi)

def parse_sets(line):
    """
    Converts a string representation of sets (e.g., '{0 1}') into a list of Python sets.
    This parses the 'Prefix' and 'Loop' definitions from your .txt files.
    """
    sets = []
    matches = re.findall(r'\{([^}]*)\}', line)
    for m in matches:
        if m.strip() == "": sets.append(set())
        else: sets.append(set(int(x) for x in m.split()))
    return sets

def build_state(active_indices, q_val, trace, aps, lasso_var):
    """
    Translates a single state in your trace into an LTL formula.
    It forces APs present in the state to be True, and absent ones to be False.
    """
    parts = []
    for i, ap in enumerate(aps):
        if i in active_indices: parts.append(f'"{ap}"_{trace}')
        else: parts.append(f'!"{ap}"_{trace}')
            
    # Add the lasso/loop marker logic to the formula
    if q_val is True: parts.append(f'"{lasso_var}"_{trace}')
    elif q_val is False: parts.append(f'!"{lasso_var}"_{trace}')
        
    return " & ".join(parts)

def rename_effect(effect_str, trace_name):
    """
    Appends the trace suffix (e.g., _pi2, _pi3) to variables inside the 
    effect formula so AutoHyper knows which trace it's evaluating.
    """
    return re.sub(r'"([^"]+)"', r'{"\1"_' + trace_name + '}', effect_str)

def generate_hq(filepath, mode="MSR"):
    """
    Generates the final HyperLTL formula string.
    - 'PI_VAL': Just verifies the path.
    - 'SR': Verifies existence of a Sufficient Reason.
    - 'MSR': Verifies existence of a Minimal Sufficient Reason (the most complex).
    """
    with open(filepath, 'r') as f:
        content = f.read()

    # Parse sections using Regex
    m_inputs = re.search(r'\[inputs\]\s+((?:"[^"]+"\s*)+)', content)
    inputs = re.findall(r'"([^"]+)"', m_inputs.group(1)) if m_inputs else []

    m_effect = re.search(r'\[effect\]\s+(.*)', content)
    effect = m_effect.group(1).strip() if m_effect else ""

    # Parse APs (Atomic Propositions)
    m_ap = re.search(r'\[lasso\][^A]*AP:\s*((?:"[^"]+"\s*)+)', content)
    if m_ap: aps = re.findall(r'"([^"]+)"', m_ap.group(1))
    else:
        m_sys_ap = re.search(r'AP:\s*\d+\s+((?:"[^"]+"\s*)+)', content)
        aps = re.findall(r'"([^"]+)"', m_sys_ap.group(1)) if m_sys_ap else []
    
    # Filter out inputs that were optimized out by SPOT
    inputs = [inp for inp in inputs if inp in aps]

    m_prefix = re.search(r'Prefix:\s*(.*)', content)
    prefix_sets = parse_sets(m_prefix.group(1)) if m_prefix else []

    m_loop = re.search(r'Loop:\s*(.*)', content)
    loop_sets = parse_sets(m_loop.group(1)) if m_loop else []

    # --- BUILD THE LASSO/LOOP CONSTRAINT ---
    # This block forces the system to stay within the infinite loop defined in your trace
    lasso_block = f'"{LASSO_VAR_DEFAULT}"_pi\n    & G (\n'
    for i, l_state in enumerate(loop_sets):
        state_cond = build_state(l_state, q_val=None, trace="pi", aps=aps, lasso_var=LASSO_VAR_DEFAULT)
        
        if i == 0: step = f'      ("{LASSO_VAR_DEFAULT}"_pi -> ({state_cond}))'
        elif i == 1: step = f'      ("{LASSO_VAR_DEFAULT}"_pi -> X(!"{LASSO_VAR_DEFAULT}"_pi & {state_cond}))'
        else:
            xs = "X" * i
            step = f'      ("{LASSO_VAR_DEFAULT}"_pi -> {xs}(!"{LASSO_VAR_DEFAULT}"_pi & {state_cond}))'
            
        lasso_block += step + '\n      &\n'
    
    xs_loop = "X" * len(loop_sets)
    lasso_block += f'      ("{LASSO_VAR_DEFAULT}"_pi -> {xs_loop}("{LASSO_VAR_DEFAULT}"_pi))\n    )'

    # Connect prefix to the loop
    full_fixation = lasso_block
    for p_state in reversed(prefix_sets):
        state_cond = build_state(p_state, q_val=False, trace="pi", aps=aps, lasso_var=LASSO_VAR_DEFAULT)
        full_fixation = f'{state_cond}\n  &\n  X(\n    {full_fixation}\n  )'

    # --- PREPARE INPUT QUANTIFICATION FORMULAS ---
    # Logic to ensure the reason (Tau) and trace (Pi) inputs are kept consistent across quantifiers
    if inputs:
        ip_pi2 = "\n      & \n      ".join([f'({{"{ip}"_tau}} -> ({{"{ip}"_pi}} <-> {{"{ip}"_pi2}}))' for ip in inputs])
        ip_pi3 = "\n          & \n          ".join([f'({{"{ip}"_tau2}} -> ({{"{ip}"_pi}} <-> {{"{ip}"_pi3}}))' for ip in inputs])
        ip_eq  = "\n      & \n      ".join([f'("{ip}"_tau <-> "{ip}"_tau2)' for ip in inputs])
        ip_imp = "\n        & \n        ".join([f'("{ip}"_tau2 -> ("{ip}"_tau))' for ip in inputs])
    else:
        ip_pi2, ip_pi3, ip_eq, ip_imp = "true", "true", "true", "true"

    # --- MODE SWITCHING (The HyperLTL Quantifiers) ---
    if mode == "PI_VAL":
        # Verify the trace is a valid path AND that the effect actually occurs on it.
        return f"""exists pi.
(
  !{{"{SINK_VAR_DEFAULT}"_pi}}
  &
  {full_fixation}
  &
  ({rename_effect(effect, 'pi')})
)"""

    if mode == "SR":
        # Check: Exists a reason (Tau) such that for all pi2, if pi2 matches Tau, effect holds.
        return f"""exists pi. exists tau. forall pi2.
(
  G ("{SINK_VAR_DEFAULT}"_tau)
)
&
(
  !{{"{SINK_VAR_DEFAULT}"_pi}}
  &
  {full_fixation}
)
&
(
  (
    !{{"{SINK_VAR_DEFAULT}"_pi2}} 
    & 
    G (
       {ip_pi2}
    )
  )
  -> 
  ({rename_effect(effect, 'pi2')})
)"""

    # MSR Logic: Checks that no smaller sufficient reason exists (The Minimal check)
    return f"""exists pi. exists tau. forall pi2. forall tau2. exists pi3.
(
  G ("{SINK_VAR_DEFAULT}"_tau)
)
&
(
  !{{"{SINK_VAR_DEFAULT}"_pi}}
  &
  {full_fixation}
)
&
(
  (
    !{{"{SINK_VAR_DEFAULT}"_pi2}} 
    & 
    G (
       {ip_pi2}
    )
  )
  -> 
  ({rename_effect(effect, 'pi2')})
)
&
(
  "{SINK_VAR_DEFAULT}"_tau2 ->(
    !
    (
      (
        !{{"{SINK_VAR_DEFAULT}"_pi3}}
        & 
        G (
          {ip_pi3}
        )
      )
      -> 
      ({rename_effect(effect, 'pi3')})
    )
    |
    G 
    (
      {ip_eq}
    )
    |
    !
    (
      G 
      (
        {ip_imp}
      )
    )
  )
)"""

import os  # Make sure to add this import if it isn't at the top of your file

if __name__ == "__main__":
    # Ensure a file path was provided
    if len(sys.argv) < 2:
        print("Usage: python script.py <input_file.txt> [mode]")
        print("Available modes: PI_VAL, SR, MSR (default: MSR)")
        sys.exit(1)

    filepath = sys.argv[1]
    
    # Check if a specific mode was provided; otherwise, default to MSR
    mode = sys.argv[2].upper() if len(sys.argv) > 2 else "MSR"

    # Validate the mode
    if mode not in ["PI_VAL", "SR", "MSR"]:
        print(f"Error: Invalid mode '{mode}'. Choose from PI_VAL, SR, or MSR.")
        sys.exit(1)

    try:
        # Generate the HyperLTL formula
        formula = generate_hq(filepath, mode)
        
        # Extract the base name without the extension (e.g., "trace1" from "trace1.txt")
        base_name = os.path.splitext(os.path.basename(filepath))[0]
        
        # Create the new filename
        output_filename = f"{base_name}_logic.hq"
        
        # Write the formula to the new file
        with open(output_filename, 'w') as out_file:
            out_file.write(formula)
            
        print(f"Success! Formula generated and saved to: {output_filename}")
        
    except FileNotFoundError:
        print(f"Error: The file '{filepath}' could not be found.")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)