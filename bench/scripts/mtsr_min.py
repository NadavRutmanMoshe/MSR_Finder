#!/usr/bin/env python3
"""
mtsr_min -- the benchmark's entry point to analysis 4 (MTSR) of the tool.

Run under the spot environment python (~/micromamba/envs/spot/bin/python3).

    mtsr_min.py <instance.txt> [--json] [--save-hoa PATH] [--cause TEXT]
                [--report-dba]

THE TOOL AS SHIPPED (2026-10-11)
--------------------------------
This file used to be its own copy of the MTSR construction, written when the
tool's version still failed on `F G` effects.  The tool was fixed with the same
algorithm later, and the two copies drifted (the tool gained the
input-determinism firewall and the ap_name() projection fix; this copy did
not).  The paper must measure the tool we submit, so this is now a thin
wrapper: the automaton comes from MSR_Finder/scripts/MTSR_one_example.synthesize,
the same function `tracemsr.py` runs, and `--cause` is decided by the tool's own
cause_check.check (is the given cause THE MTSR: MTSR / NOT_MTSR).  What the
wrapper leaves out is only the tool's printing and the PNG it draws.

The JSON keys are the ones the drivers have always read.  `dba_exists` is
report-only and not used by the paper; it costs a Rabin-to-Buchi check, so it
is computed only with --report-dba and is null otherwise.

claude_scripts/mtsr_min.py and MSR_Finder/bench/scripts/mtsr_min.py are the
same file.
"""
import argparse
import json
import os
import signal
import subprocess
import sys

import spot

_HERE = os.path.dirname(os.path.abspath(__file__))
# demo_tool/bench/scripts, MSR_Finder/bench/scripts, TraceMSR/claude_scripts
for _rel in (("..", "..", "MSR_Finder", "scripts"), ("..", "..", "scripts"),
             ("..", "..", "..", "demo_tool", "MSR_Finder", "scripts")):
    _p = os.path.abspath(os.path.join(_HERE, *_rel))
    if os.path.isfile(os.path.join(_p, "MTSR_one_example.py")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break

import MTSR_one_example as TOOL   # noqa: E402  (analysis 4 of the tool)
import cause_check                # noqa: E402  (the tool's --check)


def _die_cleanly(_sig, _frm):
    """Unwind on SIGTERM instead of vanishing.

    Python's default SIGTERM disposition ends the interpreter WITHOUT running
    `finally` blocks, so proc_guard's group kill -- the thing that stops the
    autfilt the tool launched -- never happens, and a timed-out run leaves
    `autfilt --sat-minimize` burning a core. Raising turns SIGTERM into an
    ordinary unwind, so callers that terminate this script get its children
    cleaned up whether or not they know to send SIGINT."""
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, _die_cleanly)

SAT_TIMEOUT = TOOL.SAT_TIMEOUT
SAT_MEM = TOOL.SAT_MEM
SAT_MINIMIZE_MAX_STATES = TOOL.SAT_MINIMIZE_MAX_STATES


def parse_instance(path):
    """[inputs], [effect] and the HOA system of a TraceMSR .txt (for the
    drivers that build automata of their own around the MTSR)."""
    import re
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


def run(path, cause=None, report_dba=False):
    """The tool's MTSR for one instance, plus the tool's check of `cause`.

    Raises RuntimeError when there is no exact result (state-count guard,
    autfilt timeout, the 6 GB cap), as the drivers expect."""
    with open(path, 'r', errors='replace') as f:
        content = f.read()
    try:
        res = TOOL.synthesize(content)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"autfilt timed out after {SAT_TIMEOUT}s")
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        # picosat, the SAT backend autfilt uses, reports the RLIMIT_AS hit as
        # "*** picosat: out of memory in 'new'".
        if ("bad_alloc" in stderr or "Cannot allocate memory" in stderr
                or "out of memory" in stderr):
            raise RuntimeError(f"autfilt --sat-minimize exhausted its "
                               f"{SAT_MEM // 1024**3} GB address-space cap")
        raise RuntimeError(stderr[:200])
    if not res["language_preserved"]:
        raise RuntimeError("minimized witness is NOT language-equivalent")

    witness = spot.automaton(res["final_hoa"])
    out = {
        'effect': res["effect"],
        'product_states': res["product_states"],
        'projected_deterministic': res["projected_deterministic"],
        'mode': ('sat-minimal (input already deterministic)'
                 if res["projected_deterministic"] else
                 'sat-minimal (determinized via generic acceptance)'),
        'dba_exists': (bool(spot.rabin_is_buchi_realizable(
                           spot.automaton(res["projected_hoa"])))
                       if report_dba else None),
        'states': witness.num_states(),
        'aps': len(witness.ap()),
        'deterministic': witness.is_deterministic(),
        'hoa': res["final_hoa"],
    }
    if cause is not None:
        try:
            sys_in = cause_check.system_input_language(path)
        except Exception:
            sys_in = None
        chk = cause_check.check(cause, res["final_hoa"], res["inputs"], sys_in)
        out['cause_verdict'] = chk["verdict"]
        out['cause_detail'] = chk["detail"]
    return out


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
    ap.add_argument('--cause', metavar='LTL',
                    help="also run the tool's check: is this cause the MTSR?")
    ap.add_argument('--report-dba', action='store_true',
                    help='also report whether a deterministic Buchi automaton '
                         'exists (report-only, not timed by the paper)')
    args = ap.parse_args()

    res = run(args.instance, args.cause, args.report_dba)
    if args.save_hoa:
        with open(args.save_hoa, 'w') as f:
            f.write(res['hoa'])
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != 'hoa'}))
        return
    print(f"effect      : {res['effect']}")
    print(f"projected   : deterministic={res['projected_deterministic']}")
    print(f"mode        : {res['mode']}")
    print(f"MTSR        : {res['states']} states, {res['aps']} APs, "
          f"deterministic={res['deterministic']}")
    if 'cause_verdict' in res:
        print(f"cause       : {res['cause_verdict']}")
    if not args.quiet:
        print(res['hoa'])


if __name__ == '__main__':
    main()
