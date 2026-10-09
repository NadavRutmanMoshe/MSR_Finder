#!/usr/bin/env python3
import sys
import re
import itertools

# --- CONFIGURATION ---
# Default internal variables for the HyperLTL transformation.
# SINK_VAR (r): Represents the "cause" representation trace (Tau). If True, we are in the hypothetical "cause" space.
# LASSO_VAR (q): Represents the non-deterministic guess of when the loop cycle begins in the Pi trace.
SINK_VAR_DEFAULT = "__MSR_TAU__"
LASSO_VAR_DEFAULT = "__MSR_LASSO__"

def parse_hoa(filepath):
    """
    Parses the custom HOA text file format.
    Extracts inputs, atomic propositions (APs), the initial state, and the transition relation.
    """
    with open(filepath, 'r') as f:
        content = f.read()

    # 1. Extract Inputs
    # Looks for the [inputs] block and extracts quoted strings.
    m_inputs = re.search(r'\[inputs\]\s+((?:"[^"]+"\s*)+)', content)
    if m_inputs:
        inputs = re.findall(r'"([^"]+)"', m_inputs.group(1))
    else:
        inputs = []
        

    # 2. Extract Atomic Propositions (APs)
    m_ap = re.search(r'AP:\s*\d+\s+((?:"[^"]+"\s*)+)', content)
    if m_ap:
        aps = re.findall(r'"([^"]+)"', m_ap.group(1))
    else:
        aps = []
        
    # --- SAFETY FILTER ---
    # If SPOT (or a user) optimized away an input so it no longer appears in the AP list,
    # we mathematically discard it here to prevent key errors downstream.
    inputs = [inp for inp in inputs if inp in aps]
        
    # 3. Extract Start State
    m_start = re.search(r'Start:\s*(\d+)', content)
    start_state = int(m_start.group(1)) if m_start else 0
    
    # 4. Extract Body and Transitions
    states = {}
    m_body = re.search(r'--BODY--(.*?)--END--', content, re.DOTALL)
    if m_body:
        body_lines = m_body.group(1).strip().split('\n')
        curr_state = None
        for line in body_lines:
            line = line.strip()
            
            # Identify which state we are defining transitions for
            if line.startswith('State:'):
                m = re.match(r'State:\s*(\d+)', line)
                if m:
                    curr_state = int(m.group(1))
                    states[curr_state] = []
                    
            # Identify transition conditions and targets (e.g., [(0&!1)] 2)
            elif line.startswith('['):
                m = re.match(r'\[(.*?)\]\s*(\d+)', line)
                if m:
                    cond = m.group(1)
                    target = int(m.group(2))
                    states[curr_state].append((cond, target))

    return inputs, aps, start_state, states

def _eval_cond_slow(cond_str, val):
    """The original string-rewrite + eval() evaluator; kept as the fallback for
    a condition _compile_cond cannot parse (e.g. HOA aliases)."""
    if cond_str.strip() == "t": return True
    if cond_str.strip() == "f": return False

    # Convert HOA syntax to standard Python evaluation syntax
    s = cond_str.replace('!', ' not ').replace('&', ' and ').replace('|', ' or ')

    # Replace AP index numbers with their actual boolean values from the 'val' tuple
    s = re.sub(r'\b\d+\b', lambda m: str(val[int(m.group(0))]), s)
    return eval(s)


# --- Fast, exact condition evaluation -------------------------------------
# Unrolling an HOA used to test every edge against all 2^|AP| valuations, one
# regex + eval() each: on a 20-AP instance (4 inputs, 16 outputs) that is ~1M
# evals per edge and took ~3.7 h before the solver even started.  Conditions
# are now parsed once into a small AST, and the satisfying output valuations
# for fixed inputs are found by backtracking with three-valued evaluation, so
# a branch is cut as soon as the condition is decided.  The valuations come
# out in exactly the itertools.product order of the old loops, so every
# builder discovers states in the same order and emits identical systems.

_COND_CACHE = {}
_TOK = re.compile(r'\s*(\d+|[tf!&|()])')


def _compile_cond(cond_str):
    """AST of an HOA label, or None if it uses syntax we do not parse.
    Nodes: ('c', bool) | ('v', ap_index) | ('not', n) | ('and', [n..]) |
    ('or', [n..]).  Precedence ! > & > |, as in HOA (and in Python)."""
    if cond_str in _COND_CACHE:
        return _COND_CACHE[cond_str]
    toks, pos, s = [], 0, cond_str.rstrip()
    while pos < len(s):
        m = _TOK.match(s, pos)
        if not m:
            _COND_CACHE[cond_str] = None
            return None
        toks.append(m.group(1))
        pos = m.end()
    i = 0

    def atom():
        nonlocal i
        tk = toks[i]; i += 1
        if tk == '!':
            return ('not', atom())
        if tk == '(':
            n = disj()
            if toks[i] != ')':
                raise ValueError
            i += 1
            return n
        if tk in ('t', 'f'):
            return ('c', tk == 't')
        if tk.isdigit():
            return ('v', int(tk))
        raise ValueError

    def conj():
        nonlocal i
        parts = [atom()]
        while i < len(toks) and toks[i] == '&':
            i += 1
            parts.append(atom())
        return parts[0] if len(parts) == 1 else ('and', parts)

    def disj():
        nonlocal i
        parts = [conj()]
        while i < len(toks) and toks[i] == '|':
            i += 1
            parts.append(conj())
        return parts[0] if len(parts) == 1 else ('or', parts)

    try:
        ast = disj()
        if i != len(toks):
            raise ValueError
    except (ValueError, IndexError):
        ast = None
    _COND_CACHE[cond_str] = ast
    return ast


def _ev3(n, env):
    """Three-valued evaluation: env[i] is True, False or None (unassigned)."""
    k = n[0]
    if k == 'v':
        return env[n[1]]
    if k == 'c':
        return n[1]
    if k == 'not':
        r = _ev3(n[1], env)
        return None if r is None else not r
    if k == 'and':
        unknown = False
        for c in n[1]:
            r = _ev3(c, env)
            if r is False:
                return False
            if r is None:
                unknown = True
        return None if unknown else True
    unknown = False                                   # 'or'
    for c in n[1]:
        r = _ev3(c, env)
        if r is True:
            return True
        if r is None:
            unknown = True
    return None if unknown else False


def eval_cond(cond_str, val):
    """
    Evaluates an HOA boolean condition string (e.g., "0 & !1") against a specific truth assignment.
    val is a tuple of booleans representing the current truth value of all APs.
    """
    ast = _compile_cond(cond_str)
    if ast is None:
        return _eval_cond_slow(cond_str, val)
    return bool(_ev3(ast, val))


def _sat_walk(cond, inputs, outputs, aps, i_vals, on_true):
    """Backtrack over the outputs (in `outputs` order, False before True) with
    the inputs fixed to i_vals.  on_true(prefix, j) is called for each maximal
    partial assignment of outputs[:j] under which `cond` is already True."""
    ast = _compile_cond(cond)
    idx = {a: k for k, a in enumerate(aps)}
    env = [None] * len(aps)
    for name, v in zip(inputs, i_vals):
        env[idx[name]] = v
    opos = [idx[o] for o in outputs]
    n = len(outputs)
    cur = [False] * n

    def rec(j):
        r = _ev3(ast, env)
        if r is False:
            return
        if r is True:
            on_true(cur[:j], j)
            return
        if j == n:
            return
        p = opos[j]
        for b in (False, True):
            cur[j] = b
            env[p] = b
            rec(j + 1)
        env[p] = None

    rec(0)


def sat_outputs(cond, inputs, outputs, aps, i_vals):
    """The output valuations o_vals (tuples, `outputs` order) for which `cond`
    holds under input valuation i_vals -- the same list, in the same order, as
    filtering itertools.product([False, True], repeat=len(outputs)) with
    eval_cond, without visiting all 2^|outputs| of them."""
    if _compile_cond(cond) is None:
        return [o for o in itertools.product([False, True], repeat=len(outputs))
                if _eval_cond_slow(cond, get_val_tuple(inputs, outputs, aps, i_vals, o))]
    out = []
    n = len(outputs)

    def on_true(prefix, j):
        for rest in itertools.product([False, True], repeat=n - j):
            out.append(tuple(prefix) + rest)

    _sat_walk(cond, inputs, outputs, aps, i_vals, on_true)
    return out


def count_sat_outputs(cond, inputs, outputs, aps, i_vals):
    """len(sat_outputs(...)), without materialising the valuations."""
    if _compile_cond(cond) is None:
        return len(sat_outputs(cond, inputs, outputs, aps, i_vals))
    total = [0]
    n = len(outputs)

    def on_true(_prefix, j):
        total[0] += 1 << (n - j)

    _sat_walk(cond, inputs, outputs, aps, i_vals, on_true)
    return total[0]

def get_val_tuple(inputs, outputs, aps, i_vals, o_vals):
    """
    Constructs a complete boolean tuple for all Atomic Propositions (APs) in their exact 
    original order, combining a specific set of input values and output values.
    """
    ap_idx = {name: idx for idx, name in enumerate(aps)}
    val = [False] * len(aps)
    
    for idx, name in enumerate(inputs):
        val[ap_idx[name]] = i_vals[idx]
    for idx, name in enumerate(outputs):
        val[ap_idx[name]] = o_vals[idx]
        
    return tuple(val)

def check_determinism(inputs, outputs, aps, states):
    """
    THE MATH FIREWALL: Validates that the system is input-deterministic.
    Causality fundamentally requires that the environment (inputs) fully dictates the system state.
    If the same inputs can yield two different output states, the system is ambiguous,
    and we cannot definitively prove what "caused" an effect.
    """
    num_inputs = len(inputs)
    num_outputs = len(outputs)
    input_combos = list(itertools.product([False, True], repeat=num_inputs))

    for state, transitions in states.items():
        for i_vals in input_combos:
            valid_paths = 0
            for cond, target in transitions:
                valid_paths += count_sat_outputs(cond, inputs, outputs, aps, i_vals)
            
            if valid_paths > 1:
                # Format the inputs that caused the ambiguity for the error message
                input_str = ", ".join([f"{inputs[k]}={'T' if i_vals[k] else 'F'}" for k in range(num_inputs)])
                raise ValueError(f"System is non-deterministic!\nState {state} has {valid_paths} possible paths when inputs are [{input_str}].")

def generate_lts(inputs, aps, start_state, states):
    """
    Converts the HOA into a fully unrolled Labeled Transition System (LTS) suitable for AutoHyper.
    Crucially, it geometrically expands the state space to introduce the non-deterministic `q` variable 
    (for lasso cycle guessing) and the `r` variable (for trace separation).
    """
    outputs = [ap for ap in aps if ap not in inputs]
    num_inputs = len(inputs)
    num_outputs = len(outputs)

    # --- NEW: Check Determinism before unrolling ---
    check_determinism(inputs, outputs, aps, states)

    # Generate all possible boolean combinations for inputs and outputs (2^N)
    input_combos = list(itertools.product([False, True], repeat=num_inputs))

    # To avoid infinite loops and duplicate processing, track states we've built.
    # A state is uniquely defined by: (original HOA state, boolean values, current q value)
    discovered_states = {} 
    state_details = {}     
    next_id = 0
    queue = []
    
    init_ids = set()
    
    # --- STEP 1: Process Init Transitions ---
    # We evaluate all possible input/output combinations against the starting state's conditions.
    for cond, target in states.get(start_state, []):
        for i_vals in input_combos:
            for o_vals in sat_outputs(cond, inputs, outputs, aps, i_vals):
                val = get_val_tuple(inputs, outputs, aps, i_vals, o_vals)
                if eval_cond(cond, val):
                    
                    # DUPLICATION LOGIC: Every valid state is duplicated. 
                    # One version where q=False (not in cycle yet) and q=True (cycle has started).
                    # This allows AutoHyper to non-deterministically guess the start of the loop.
                    for q_val in [False, True]:
                        key = (target, val, q_val)
                        if key not in discovered_states:
                            discovered_states[key] = next_id
                            # Note: r is ALWAYS False in the primary system representation
                            state_details[next_id] = {'r': False, 'q': q_val, 'val': val, 'hoa_target': target, 'transitions': set()}
                            queue.append(next_id)
                            next_id += 1
                        init_ids.add(discovered_states[key])
                        
    # --- STEP 2: BFS State Exploration (The Primary System where r=false) ---
    # Unroll the graph transition by transition to build a flat LTS structure.
    while queue:
        curr_id = queue.pop(0)
        curr_hoa = state_details[curr_id]['hoa_target']
        
        for cond, target in states.get(curr_hoa, []):
            for i_vals in input_combos:
                for o_vals in sat_outputs(cond, inputs, outputs, aps, i_vals):
                    val = get_val_tuple(inputs, outputs, aps, i_vals, o_vals)
                    if eval_cond(cond, val):
                        
                        # At every step, the system transitions to both a q=False and q=True branch
                        for next_q_val in [False, True]:
                            key = (target, val, next_q_val)
                            if key not in discovered_states:
                                discovered_states[key] = next_id
                                state_details[next_id] = {'r': False, 'q': next_q_val, 'val': val, 'hoa_target': target, 'transitions': set()}
                                queue.append(next_id)
                                next_id += 1
                            # Link the current state to the newly discovered target state
                            state_details[curr_id]['transitions'].add(discovered_states[key])
                            
    # --- STEP 3: Create the Sink Component (The Hypothetical "Tau" System where r=true) ---
    # This is vital for causality. We generate a completely disconnected sub-graph. 
    # Because it is fully disconnected, the system's actual transition rules don't apply to it.
    # AutoHyper uses this independent structure to construct the hypothetical Tau trace.
    sink_ids = []
    for i_vals in input_combos:
        sink_id = next_id
        next_id += 1
        
        # In the hypothetical sink graph, outputs are irrelevant (always False).
        val = get_val_tuple(inputs, outputs, aps, i_vals, tuple([False]*num_outputs))
        
        # Sinks strictly have r=True and q=False
        state_details[sink_id] = {'r': True, 'q': False, 'val': val, 'transitions': set()}
        sink_ids.append(sink_id)
        
        # Because we need AutoHyper to freely pick any input configuration for Tau,
        # all sink states are valid starting states.
        init_ids.add(sink_id)
        
    # Connect every sink state to every other sink state (a fully connected clique).
    # This allows the hypothetical Tau trace to freely change inputs at any timestep 
    # without being restricted by the original system's HOA rules.
    for sid in sink_ids:
        state_details[sid]['transitions'] = set(sink_ids)
        
    lines = []
    
    # Declare variables (sink and lasso are prepended)
    var_list = [f'("{SINK_VAR_DEFAULT}" Bool)', f'("{LASSO_VAR_DEFAULT}" Bool)'] + [f'("{ap}" Bool)' for ap in aps]
    lines.append(f"Variables: {' '.join(var_list)}")
    
    # Declare valid starting states (Includes valid primary states + ALL sink states)
    lines.append(f"Init: {' '.join(map(str, sorted(list(init_ids))))}")
    lines.append("--BODY--")
    
    # Write out each state and its variable assignments
    for i in range(next_id):
        r_val = "true" if state_details[i]['r'] else "false"
        q_val = "true" if state_details[i]['q'] else "false"
        assign_strs = [f'("{SINK_VAR_DEFAULT}" {r_val})', f'("{LASSO_VAR_DEFAULT}" {q_val})']        
    
        for ap_idx, ap_name in enumerate(aps):
            v = "true" if state_details[i]['val'][ap_idx] else "false"
            assign_strs.append(f'("{ap_name}" {v})')
            
        lines.append(f"State: {i} {{{' '.join(assign_strs)}}}")
        
        # Write out transitions from this state
        trans = sorted(list(state_details[i]['transitions']))
        lines.append(" ".join(map(str, trans)))
        
    lines.append("--END--")
    return "\n".join(lines)

# def main():
#     if len(sys.argv) < 3:
#         print("Usage: python converter.py <input_hoa.txt> <output_lts.txt>")
#         sys.exit(1)

#     in_file = sys.argv[1]
#     out_file = sys.argv[2]

#     inputs, aps, start_state, states = parse_hoa(in_file)
#     lts_output = generate_lts(inputs, aps, start_state, states)

#     with open(out_file, 'w') as f:
#         f.write(lts_output + "\n")

# if __name__ == "__main__":
#     main()