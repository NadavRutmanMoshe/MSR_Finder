import argparse
import itertools
import spot
import buddy
import re
import subprocess
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc_guard   # subprocess.run that kills the whole process group
import cause_check  # --check: is [cause] the same language as the MTSR?

GREEN = "\033[92m"
DIM = "\033[2m"
RESET = "\033[0m"

#: Wall-clock ceiling for a single autfilt call, matching the 120s used by the
#: benchmark harness so demo and benchmark time out on the same instances.
SAT_TIMEOUT = 120

#: Address-space ceiling per autfilt call. RLIMIT_AS caps VIRTUAL size, which
#: runs ~1.5x ahead of resident, so 6 GB here is roughly a 4 GB resident
#: ceiling -- deliberately well under the WSL VM's 10 GB budget.
SAT_MEM = 6 * 1024**3

#: Above this many states, exact SAT-based minimization is not worth starting.
#: It encodes "does an equivalent k-state automaton exist?" as SAT, so cost is
#: exponential and it is meant for tens of states. An unguarded 21,840-state
#: input once ran 21 minutes and exhausted a 10 GB VM without finishing.
SAT_MINIMIZE_MAX_STATES = 500


def input_nondeterminism(aut, inputs):
    """None when `aut` is input-deterministic, else a sentence naming a state
    and an input valuation with more than one (outputs, successor) choice.

    MTSR projects the outputs away EXISTENTIALLY: an input word is kept when
    SOME run of the system on it satisfies Phi.  That is "the inputs force Phi"
    only when each input word has one run, which is the firewall the other
    analyses enforce (converter.check_determinism) and this one used to skip --
    a nondeterministic system got a confident, wrong MTSR.

    Decided on spot's BDDs rather than by enumerating output valuations: per
    state and input valuation, count the (output valuation, successor) pairs
    the edges allow.  Enumeration is 2^|outputs| per edge, and 12 outputs
    already cost converter's version hours."""
    ins = [ap for ap in aut.ap() if ap.ap_name() in inputs]
    in_vars = [aut.register_ap(ap) for ap in ins]
    out_set = buddy.bddtrue
    for ap in aut.ap():
        if ap.ap_name() not in inputs:
            out_set &= buddy.bdd_ithvar(aut.register_ap(ap))
    for s in range(aut.num_states()):
        by_dst = {}
        for e in aut.out(s):
            by_dst[e.dst] = by_dst.get(e.dst, buddy.bddfalse) | e.cond
        for bits in itertools.product([False, True], repeat=len(in_vars)):
            cube = buddy.bddtrue
            for v, b in zip(in_vars, bits):
                cube &= buddy.bdd_ithvar(v) if b else buddy.bdd_nithvar(v)
            choices = sum(buddy.bdd_satcountset(buddy.bdd_restrict(c, cube), out_set)
                          for c in by_dst.values())
            if choices > 1:
                vals = ", ".join(f"{ap.ap_name()}={'T' if b else 'F'}"
                                 for ap, b in zip(ins, bits))
                return (f"state {s} has {choices:g} possible (outputs, successor) "
                        f"choices when inputs are [{vals}]")
    return None


def _all_accepting(final_hoa):
    """True when the acceptance condition is trivial ('t') — every run is
    accepting, so every state of the automaton is an accepting state."""
    m = re.search(r'^Acceptance:\s*(.+)$', final_hoa, re.MULTILINE)
    return bool(m) and m.group(1).strip() in ("0 t", "t")


def print_hoa_colored(final_hoa):
    """
    Display the minimized HOA with the ACCEPTING parts in green:
      * a 'State:' line is green if the state is accepting — it carries an
        acceptance mark {..}, or one of its outgoing edges does;
      * an edge line is green if it carries an acceptance mark;
      * if the acceptance condition is 't' (all runs accepting), the whole
        body is green — every state is accepting.
    """
    all_acc = _all_accepting(final_hoa)
    lines = final_hoa.splitlines()
    try:
        body_start = lines.index("--BODY--") + 1
        body_end = lines.index("--END--")
    except ValueError:
        print(final_hoa)
        return

    # Pre-pass: group the body into state blocks to decide which lines are green.
    green = set()
    blocks = []                       # (header_line_idx, [edge_line_idx, ...])
    cur = None
    for i in range(body_start, body_end):
        s = lines[i].strip()
        if s.startswith("State:"):
            cur = (i, [])
            blocks.append(cur)
        elif cur is not None and s:
            cur[1].append(i)
    for hdr, edges in blocks:
        marked_edges = [i for i in edges if "{" in lines[i]]
        state_marked = "{" in lines[hdr]
        if all_acc or state_marked or marked_edges:
            green.add(hdr)            # the state is accepting
        green.update(edges if all_acc else marked_edges)

    for i, line in enumerate(lines):
        print(f"{GREEN}{line}{RESET}" if i in green else line)


class MTSRRefused(Exception):
    """An instance MTSR must not answer, because the answer would be wrong
    without any error: `kind` is "unknown_ap" or "nondeterministic"."""

    def __init__(self, kind, msg):
        super().__init__(msg)
        self.kind = kind


def synthesize(content, timings=None):
    """The MTSR of one instance (the text of its .txt file): the computation
    alone, with no printing and no picture.

    This is the one implementation of analysis 4.  run_workflow below wraps it
    for the user, and the benchmark (bench/scripts/mtsr_min.py) calls it
    directly, so the paper measures the tool as shipped.

    Returns {"inputs", "effect", "final_hoa", "projected_hoa",
    "product_states", "projected_deterministic", "language_preserved"}, and
    fills `timings` (translate / product / minimize) when one is given.
    Raises ValueError for a malformed file, MTSRRefused, and whatever the
    autfilt calls raise: subprocess.TimeoutExpired, CalledProcessError (a
    bad_alloc there is the memory cap), RuntimeError (the state-count guard).
    """
    if timings is None:
        timings = {}

    # ==========================================
    # 1. Extract Inputs
    # ==========================================
    inputs_match = re.search(r'\[inputs\]\s*(.*?)\s*\[', content, re.DOTALL)
    if not inputs_match:
        raise ValueError("Could not find [inputs] section")
    # Parse "r0" "r1" into a set {'r0', 'r1'}
    input_aps = set(re.findall(r'"([^"]+)"', inputs_match.group(1)))

    # ==========================================
    # 2. Extract and Process LTL Effect
    # ==========================================
    effect_match = re.search(r'\[effect\]\s*(.*?)\s*\[', content, re.DOTALL)
    if not effect_match:
        raise ValueError("Could not find [effect] section")

    # Clean up the string (remove newlines/padding)
    ltl_formula = effect_match.group(1).strip()

    # Translate LTL to Automaton
    # using spot.translate ensures we get a compatible automaton
    try:
        t0 = time.perf_counter()
        effect_formula = spot.formula(ltl_formula)
        aut_effect = spot.translate(effect_formula)
        timings["translate"] = time.perf_counter() - t0
    except Exception as e:
        raise ValueError(f"translating LTL: {e}")

    # ==========================================
    # 3. Extract and Process HOA System
    # ==========================================
    hoa_match = re.search(r'(HOA:.*--END--)', content, re.DOTALL)
    if not hoa_match:
        raise ValueError("Could not find HOA content")

    hoa_content = hoa_match.group(1)
    aut_system = spot.automaton(hoa_content)

    if aut_system is None:
        raise ValueError("parsing HOA system")

    # Two inputs the product would otherwise accept and answer WRONGLY, with
    # no error: an effect over a proposition the system does not have (spot
    # treats it as a free letter, the projection below then drops it
    # existentially, and the MTSR comes out as "every input word"), and a
    # nondeterministic system (see input_nondeterminism).
    sys_aps = {ap.ap_name() for ap in aut_system.ap()}
    unknown = sorted({ap.ap_name() for ap in spot.atomic_prop_collect(effect_formula)}
                     - sys_aps)
    if unknown:
        raise MTSRRefused(
            "unknown_ap",
            f"The [effect] mentions {unknown}, which the [system] does not "
            f"declare (its APs: {sorted(sys_aps)}).")
    why = input_nondeterminism(aut_system, input_aps)
    if why:
        raise MTSRRefused("nondeterministic", why)

    # ==========================================
    # 4. Intersection (Product)
    # ==========================================
    t0 = time.perf_counter()
    aut_intersection = spot.product(aut_system, aut_effect)
    timings["product"] = time.perf_counter() - t0

    # Convert intersection to string so we can pass it to autfilt
    # (This bypasses the Python binding issues you had with remove_ap/minimization)
    intersection_hoa = aut_intersection.to_str("hoa")

    # ==========================================
    # 5. Identify Outputs to Remove
    # ==========================================
    # We look at all APs in the intersection and remove those that are NOT inputs.
    # Compare by ap_name(): str(ap) is spot's PRINTED form, which quotes names
    # such as "Go", "Fa" or "i-1" -- '"Go"' is not in {'Go', ...}, so such an
    # input used to be projected away as if it were an output. str(ap) is still
    # what goes to --remove-ap, since that option parses its argument.
    aps_to_remove = [str(ap) for ap in aut_intersection.ap()
                     if ap.ap_name() not in input_aps]

    # ==========================================
    # 6. Project & Minimize (using autfilt)
    # ==========================================
    # can be changed here to --small for faster results.
    # -B forces a Buchi acceptance condition.  Spot >= 2.15 simplifies the
    # acceptance of these products to 't' ("all runs accepting"), which renders
    # without the double-circle accepting states of the published figures;
    # -B restores that presentation so the demo matches the paper under the
    # current Spot.  The recognized language and the minimal state count are
    # unchanged -- this is presentation only.
    proj_cmd = ["autfilt"] + [f"--remove-ap={ap}" for ap in aps_to_remove]

    t0 = time.perf_counter()

    # Step 1: project the outputs away (the cause lives over the inputs).
    # proc_guard.run everywhere below: these are leaves with no children of
    # their own, but `--sat-minimize` can run for a long time and Ctrl-C
    # through a plain subprocess.run leaves it orphaned and still running.
    projected = proc_guard.run(
        proj_cmd,
        input=intersection_hoa,
        timeout=SAT_TIMEOUT,
        mem_limit=SAT_MEM,
        check=True
    ).stdout

    # Step 2: `--sat-minimize` REQUIRES a deterministic input. Two things
    # here can destroy determinism:
    #   (a) the effect automaton -- `F G x` translates to a nondeterministic
    #       Buchi automaton, and no deterministic Buchi automaton exists for
    #       it (NBA are strictly more expressive than DBA);
    #   (b) --remove-ap, an existential projection, which can introduce
    #       nondeterminism even from a deterministic product.
    # Without this step autfilt aborts with "SAT-based minimization only
    # works with deterministic automata".
    #
    # Determinizing to *Buchi* is therefore impossible in general, but
    # determinizing at all is always possible with a richer acceptance
    # condition, so we ask for 'generic' (spot picks co-Buchi/parity). The
    # language is preserved, and --sat-minimize is then free to return a
    # minimal -- possibly nondeterministic -- Buchi automaton.
    aut_projected = spot.automaton(projected)
    projected_deterministic = aut_projected.is_deterministic()
    if not projected_deterministic:
        projected = spot.postprocess(
            aut_projected, 'generic', 'deterministic').to_str('hoa')

    # Step 3: exact SAT-based minimization.
    #
    # Guard first: SAT minimization is exponential, so past a few hundred
    # states it does not finish and only burns memory until something dies.
    # Refusing here is strictly better than a 120s timeout, because the
    # timeout still pays 120s and 6 GB to learn what the state count
    # already told us.
    n_states = spot.automaton(projected).num_states()
    if n_states > SAT_MINIMIZE_MAX_STATES:
        raise RuntimeError(
            f"{n_states} states exceeds SAT_MINIMIZE_MAX_STATES "
            f"({SAT_MINIMIZE_MAX_STATES}); exact minimization would not "
            f"terminate. Use spot.postprocess(aut, 'small') for a "
            f"heuristic result.")

    result = proc_guard.run(
        ["autfilt", "--sat-minimize", "-B"],
        input=projected,
        timeout=SAT_TIMEOUT,
        mem_limit=SAT_MEM,
        check=True
    )
    timings["minimize"] = time.perf_counter() - t0

    final_hoa = result.stdout

    # Determinization + SAT minimization must preserve the language
    # exactly. Cheap at these sizes, and it turns a silent wrong answer
    # into a loud one -- which matters more now that --check reports a
    # verdict computed against this automaton.
    equivalent = spot.are_equivalent(spot.automaton(final_hoa),
                                     spot.automaton(projected))
    timings["language_preserved"] = equivalent

    return {"inputs": input_aps, "effect": ltl_formula,
            "final_hoa": final_hoa, "projected_hoa": projected,
            "product_states": aut_intersection.num_states(),
            "projected_deterministic": projected_deterministic,
            "language_preserved": equivalent}


def run_workflow(filename, output_image="final_result.png", check=False):
    """Synthesize the MTSR; with `check`, additionally verify the instance's
    [cause] block against it (see cause_check)."""
    timings = {}
    t_start = time.perf_counter()

    try:
        with open(filename, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        sys.exit(1)

    try:
        try:
            res = synthesize(content, timings)
        except ValueError as e:
            print(f"Error: {e}")
            return
        except MTSRRefused as e:
            if e.kind == "nondeterministic":
                print(f"\033[95m[!] AMBIGUOUS SYSTEM (non-deterministic): {e}.\033[0m")
                print("    MTSR keeps an input word when SOME run on it satisfies the "
                      "effect; that means\n    the inputs FORCE it only when every "
                      "input word has exactly one run.")
            else:
                print(f"\033[91m[!] {e}\033[0m")
            return
        final_hoa = res["final_hoa"]
        equivalent = res["language_preserved"]
        if not equivalent:
            print("\033[91m[!] BUG: the minimized automaton is NOT "
                  "language-equivalent to the projected product. The MTSR "
                  "below is wrong; do not trust it.\033[0m")

        # ==========================================
        # 7. Display: the minimized HOA, accepting states in green
        # ==========================================
        all_acc = _all_accepting(final_hoa)
        legend = ("every state is accepting" if all_acc
                  else "green = accepting states/edges")
        print(f"{GREEN}found MTSR:{RESET}  {DIM}({legend}){RESET}")
        print_hoa_colored(final_hoa)

        # ==========================================
        # 8. Render to PNG
        # ==========================================
        # Load back to Spot to get DOT format
        final_aut = spot.automaton(final_hoa)
        dot_string = final_aut.to_str("dot")

        t0 = time.perf_counter()
        proc_guard.run(
            ["dot", "-Tpng", "-o", output_image],
            input=dot_string,
            check=True
        )
        timings["render"] = time.perf_counter() - t0
        print(f"saved: {output_image}")

        # ==========================================
        # 9. Optional: verify the given [cause] against what we synthesized
        # ==========================================
        # Deliberately AFTER the image: --check is an extra question about the
        # MTSR, not a different pipeline, so a failed check still leaves the
        # user with the automaton to look at.
        if check and not equivalent:
            print("\033[91m[!] skipping --check: a verdict against a wrong "
                  "MTSR would be meaningless.\033[0m")
        elif check:
            t0 = time.perf_counter()
            res = cause_check.run(filename, final_hoa)
            timings["check"] = time.perf_counter() - t0
            timings["check_verdict"] = res["verdict"] if res else "NO_CAUSE_BLOCK"

        timings["total"] = time.perf_counter() - t_start

    except subprocess.TimeoutExpired as e:
        # proc_guard has already killed the whole process group by here.
        print(f"TIMEOUT: {e.cmd[0]} exceeded {SAT_TIMEOUT}s and was killed.")
    except RuntimeError as e:
        # The state-count guard: refused before starting, nothing was spawned.
        print(f"SKIPPED: {e}")
    except subprocess.CalledProcessError as e:
        # A non-zero exit after a mem_limit hit is std::bad_alloc, not a bug in
        # the tool -- report it as resource exhaustion so it is not mistaken
        # for a crash when these runs are tabulated.
        stderr = e.stderr or ""
        if "bad_alloc" in stderr or "Cannot allocate memory" in stderr:
            print(f"RESOURCE-EXHAUSTED: {e.cmd[0]} hit the "
                  f"{SAT_MEM // 1024**3} GB address-space cap.")
        else:
            print("Error during autfilt/dot processing:")
            print(stderr)
    except FileNotFoundError as e:
        print(f"Error: Tool not found ({e.filename}). Make sure Spot (autfilt) and Graphviz (dot) are installed.")

    return timings

# --- Run the Script ---
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="MTSR: the minimal temporal sufficient reason, as an "
                    "automaton over the inputs.")
    ap.add_argument("input", help="the instance .txt")
    ap.add_argument("output_image", nargs="?", default="final_result.png")
    ap.add_argument("--check", action="store_true",
                    help="also verify the instance's [cause] block against the "
                         "synthesized MTSR (language equivalence, via spot)")
    args = ap.parse_args()
    # This process drives autfilt, which proc_guard starts in a process group
    # of its own; only an unwind through proc_guard's `finally` reaches it.
    # tracemsr stops this process with SIGTERM on a timeout, so SIGTERM has to
    # unwind like Ctrl-C does rather than end the interpreter in place.
    proc_guard.install_termination_handlers()
    try:
        run_workflow(args.input, args.output_image, check=args.check)
    except KeyboardInterrupt:
        sys.exit(130)
