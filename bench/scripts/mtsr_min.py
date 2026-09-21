#!/usr/bin/env python3
"""
MTSR witness minimization that survives non-DBA-recognizable languages.

Run under the spot environment python (~/micromamba/envs/spot/bin/python3).

THE PROBLEM
-----------
scripts/MTSR_one_example.py minimizes with a single call:

    autfilt --sat-minimize -B --remove-ap=<each output>

`--sat-minimize` requires a DETERMINISTIC input, and two steps destroy
determinism:
  (a) the effect automaton: `F G x` translates to a nondeterministic Buchi
      automaton (the system side is fine -- our canonicalized HOA is
      deterministic);
  (b) `--remove-ap` is an existential projection over the outputs, which can
      introduce nondeterminism even from a deterministic product.
Result: `autfilt: SAT-based minimization only works with deterministic automata`
on 217/978 instances (100% of the `F G` class).

WHY WE CANNOT JUST DETERMINIZE
------------------------------
Nondeterministic Buchi automata are strictly more expressive than deterministic
ones. `F G a` is the textbook witness: no DBA recognizes it. Confirmed with spot:

    postprocess(F G a, 'BA',      'deterministic') -> det=False  (impossible)
    postprocess(F G a, 'generic', 'deterministic') -> det=True, acc=Fin(0)

So determinization is always possible with a RICHER acceptance condition
(co-Buchi/parity), but never with Buchi.

THE FIX
-------
The key measurement: `--sat-minimize`'s precondition is a DETERMINISTIC INPUT,
not a DBA-recognizable language, and its OUTPUT may be nondeterministic. On the
F G instance it returns a nondeterministic 3-state Buchi automaton, verified
language-equivalent to the input. So no heuristic fallback is needed:

    projected deterministic?  -> sat-minimize -B                (unchanged path)
    otherwise                 -> determinize with 'generic'
                                 acceptance, then sat-minimize -B

Both paths stay exact, so all 217 previously-failing instances get MINIMAL
witnesses rather than heuristically-reduced ones.

`spot.rabin_is_buchi_realizable` (verified: F G a -> False, G F a -> True) is
still computed, but only as a REPORTED FLAG: it explains why a witness came back
nondeterministic. It must not be used to gate minimization.

Every result is checked with spot.are_equivalent against the pre-minimization
automaton, so a determinization that changed the language would raise instead of
silently producing a wrong witness size.
"""
import argparse
import os
import re
import signal
import subprocess
import sys

import spot

# One reaper, shared with the demo tool, rather than another copy: the
# solvers we shell out to are not leaf processes, so a plain
# subprocess.run timeout leaves their children running at ~100% CPU.
# bench/ ships inside the tool repo (MSR_Finder/bench); the recorded runs
# were taken with it beside the tool (demo_tool/bench). Accept both, and let
# $MSR_FINDER_SCRIPTS override.
def _msr_finder_scripts():
    _here = os.path.dirname(os.path.abspath(__file__))
    _c = [os.environ.get("MSR_FINDER_SCRIPTS"),
          os.path.join(_here, "..", "..", "scripts"),
          os.path.join(_here, "..", "..", "MSR_Finder", "scripts")]
    for _p in _c:
        if _p and os.path.exists(os.path.join(_p, "proc_guard.py")):
            return os.path.abspath(_p)
    return os.path.abspath(_c[-1])


_PG_DEMO = _msr_finder_scripts()
if _PG_DEMO not in sys.path:
    sys.path.insert(0, _PG_DEMO)

import proc_guard  # noqa: E402  (group kill + group-scoped reaper)


def _die_cleanly(_sig, _frm):
    """Unwind on SIGTERM instead of vanishing.

    Python's default SIGTERM disposition ends the interpreter WITHOUT running
    `finally` blocks, so proc_guard's group kill -- the thing that stops the
    autfilt this script launched -- never happens, and a timed-out run leaves
    `autfilt --sat-minimize` burning a core. Raising turns SIGTERM into an
    ordinary unwind, so callers that terminate this script get its children
    cleaned up whether or not they know to send SIGINT."""
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, _die_cleanly)

#: Kept in step with MTSR_one_example.py -- the demo and this comparison must
#: time out on the same instances, or their state counts are not comparable.
SAT_TIMEOUT = 120
SAT_MEM = 6 * 1024**3          # RLIMIT_AS: virtual, ~1.5x the resident ceiling
SAT_MINIMIZE_MAX_STATES = 500  # exact SAT minimization is exponential


def parse_instance(path):
    """Pull [inputs], [effect] and the HOA system out of a TraceMSR .txt."""
    with open(path, 'r', errors='replace') as f:
        content = f.read()
    m_in = re.search(r'\[inputs\]\s*(.*?)\s*\[', content, re.DOTALL)
    inputs = set(re.findall(r'"([^"]+)"', m_in.group(1))) if m_in else set()
    m_eff = re.search(r'\[effect\]\s*\n(.+)', content)
    if not m_eff:
        raise ValueError("no [effect] block")
    m_hoa = re.search(r'(HOA:.*--END--)', content, re.DOTALL)
    if not m_hoa:
        raise ValueError("no HOA system")
    return inputs, m_eff.group(1).strip(), m_hoa.group(1)


def project_out_outputs(product, inputs):
    """Existentially remove every non-input AP -- the cause lives over inputs."""
    to_remove = [str(ap) for ap in product.ap() if str(ap) not in inputs]
    if not to_remove:
        return product, []
    cmd = ["autfilt"] + [f"--remove-ap={ap}" for ap in to_remove]
    r = proc_guard.run(cmd, input=product.to_str("hoa"),
                       capture_output=True, text=True, check=True,
                       timeout=SAT_TIMEOUT, mem_limit=SAT_MEM)
    return spot.automaton(r.stdout), to_remove


def sat_minimize_buchi(aut):
    """autfilt --sat-minimize -B on an automaton we know is deterministic.

    Raises RuntimeError if the input is too large to attempt, if the 120s
    timeout expires, or if autfilt exhausts its address-space cap -- all three
    are "no exact result for this instance", which is what the caller records.
    """
    if aut.num_states() > SAT_MINIMIZE_MAX_STATES:
        raise RuntimeError(
            f"{aut.num_states()} states > SAT_MINIMIZE_MAX_STATES "
            f"({SAT_MINIMIZE_MAX_STATES}): exact minimization is exponential "
            f"and would not terminate")
    try:
        r = proc_guard.run(["autfilt", "--sat-minimize", "-B"],
                           input=aut.to_str("hoa"), capture_output=True,
                           text=True, timeout=SAT_TIMEOUT, mem_limit=SAT_MEM)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"autfilt --sat-minimize timed out after "
                           f"{SAT_TIMEOUT}s")
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        # picosat, the SAT backend autfilt uses, reports the RLIMIT_AS hit as
        # "*** picosat: out of memory in 'new'" -- neither of the spot-side
        # spellings. Without this it is filed as a tool crash instead of as
        # RESOURCE-EXHAUSTED, which is the difference between "our minimizer is
        # broken" and "this instance needs more than 6 GB".
        if ("bad_alloc" in stderr or "Cannot allocate memory" in stderr
                or "out of memory" in stderr):
            raise RuntimeError(f"autfilt --sat-minimize exhausted its "
                               f"{SAT_MEM // 1024**3} GB address-space cap")
        raise RuntimeError(stderr[:200])
    return spot.automaton(r.stdout)


def minimize_witness(projected):
    """Return (automaton, mode, dba_exists).

    `--sat-minimize`'s real precondition is a DETERMINISTIC INPUT -- it does not
    require the language to be DBA-recognizable, and its *output* is free to be
    nondeterministic. So we never have to fall back to a heuristic: determinizing
    with a richer acceptance is always possible, and minimization stays exact.
    """
    if projected.is_deterministic():
        # Path 1: exactly what the demo tool does today.
        det, mode = projected, 'sat-minimal (input already deterministic)'
    else:
        # Path 2: Buchi cannot always be determinized (F G a is the textbook
        # witness), but 'generic' lets spot pick co-Buchi/parity, which always
        # can. This preserves the language exactly.
        det = spot.postprocess(projected, 'generic', 'deterministic')
        mode = 'sat-minimal (determinized via generic acceptance)'

    # Report-only: does an equivalent DETERMINISTIC Buchi automaton exist? This
    # never gates the minimization; it explains why a result may come back
    # nondeterministic, and is worth recording alongside the size.
    dba_exists = bool(spot.rabin_is_buchi_realizable(det))

    return sat_minimize_buchi(det), mode, dba_exists


def run(path):
    inputs, effect, hoa = parse_instance(path)
    system = spot.automaton(hoa)
    product = spot.product(system, spot.translate(effect))
    projected, removed = project_out_outputs(product, inputs)
    witness, mode, dba_exists = minimize_witness(projected)

    # Determinization + SAT minimization must preserve the language exactly.
    # This is cheap at these sizes and turns a silent wrong answer into a loud
    # one, so it stays in rather than being a debug-only check.
    if not spot.are_equivalent(witness, projected):
        raise RuntimeError("minimized witness is NOT language-equivalent")

    return {
        'effect': effect,
        'removed_aps': removed,
        'product_states': product.num_states(),
        'projected_deterministic': projected.is_deterministic(),
        'mode': mode,
        'dba_exists': dba_exists,
        'states': witness.num_states(),
        'aps': len(witness.ap()),
        'deterministic': witness.is_deterministic(),
        'hoa': witness.to_str('hoa'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('instance')
    ap.add_argument('--quiet', action='store_true', help='suppress the HOA dump')
    ap.add_argument('--json', action='store_true',
                    help='machine-readable output for the benchmark runner')
    ap.add_argument('--save-hoa', metavar='PATH',
                    help='also write the minimized witness as HOA. Needed by '
                         'corp_compare.py --check-mtsr, which asks whether CORP '
                         'considers this automaton equivalent to its own cause.')
    args = ap.parse_args()

    res = run(args.instance)
    if args.save_hoa:
        with open(args.save_hoa, 'w') as f:
            f.write(res['hoa'])
    if args.json:
        import json
        print(json.dumps({k: v for k, v in res.items() if k != 'hoa'}))
        return
    print(f"effect      : {res['effect']}")
    print(f"projected   : deterministic={res['projected_deterministic']}")
    print(f"mode        : {res['mode']}")
    print(f"DBA exists  : {res['dba_exists']}")
    print(f"MTSR        : {res['states']} states, {res['aps']} APs, "
          f"deterministic={res['deterministic']}")
    if not args.quiet:
        print(res['hoa'])


if __name__ == '__main__':
    main()
