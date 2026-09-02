#!/usr/bin/env python3
"""
tracemsr -- single entry point: an interactive checklist of the tool's analyses.

    python3 tracemsr.py <file.txt> [--timeout N] [--run 1,3]

Presents a multi-select checklist (arrow keys move, SPACE toggles, ENTER runs):

  1. MSR with the given PI        -- the classic flow; needs the [lasso] block.
  2. MSR for Phi (find PI)        -- only needs [inputs]/[effect]/[system];
                                     synthesizes a PI satisfying the effect.
  3. Model checking of Phi        -- exists pi . !Phi; PASSED, or counterexample
                                     + the MSR of why the violation happened.
  4. MTSR                         -- the Minimal Temporal Sufficient Reason
                                     automaton (colored ASCII + <base>_mtsr.png).

Any subset can be selected; the chosen analyses run in order, each reporting
its wall-clock time.  `--run 1,3` (or `--run all`) skips the menu -- useful
for scripting and non-TTY runs.

Options 1-3 print the compact result table: one trace column (the actual trace
Pi) with the positions pinned by the reason R in green.  `--with-tau`, or the
`t` key in the menu, adds the Tau witness back (extra column + Tau block).

Two switches change what the analyses do:

  --hyperQB   solve analysis 1 with HyperQB 2.0 instead of AutoHyper.  HyperQB
              is a BOUNDED model checker, so a `sat` (a reason was found) is a
              proof, while "nothing found" only means "not within the bound k"
              -- it is reported as UNDECIDED, never as "no cause exists".  It is
              often much faster, and on some instances it answers where
              AutoHyper does not.  `--k` sets the bound.

  --check     turn analysis 4 (MTSR) from a synthesizer into a CHECKER, the way
              CATS is one: synthesize the MTSR as usual, then ask spot whether
              it accepts the same input sequences as the instance's [cause]
              block.  Answers EQUIVALENT, or names which side is the bigger
              language.

"""
import argparse
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
for p in (_HERE, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import cli_viewer           # noqa: E402
import hyperqb_analyzer     # noqa: E402  (the same pipeline on HyperQB)
import msr_analyzer         # noqa: E402  (the MSR pipeline)
import pi_finder            # noqa: E402  (find / model-check flows)
import proc_guard           # noqa: E402  (group kill + orphan reaper)

SPOT_ENV_PY = os.environ.get(
    "SPOT_ENV_PY", os.path.expanduser("~/micromamba/envs/spot/bin/python3"))

_B = "\033[1m"; _D = "\033[2m"; _C = "\033[96m"; _G = "\033[92m"
_R = "\033[91m"; _Y = "\033[93m"; _0 = "\033[0m"

#: Position of the MTSR analysis in _menu_options -- the one --check acts on.
MTSR_INDEX = 3


def has_lasso(path):
    with open(path, "r") as f:
        content = f.read()
    return "[lasso]" in content and re.search(r'Loop:\s*\{', content) is not None


def has_cause(path):
    """Does the file carry a [cause] block for --check to verify?"""
    # A section header sits alone on its line; the [info] block is free prose
    # and may mention "[cause]" in a sentence.
    with open(path, "r", errors="replace") as f:
        return re.search(r'^[^\S\n]*\[cause\][^\S\n]*$', f.read(),
                         re.MULTILINE) is not None


class Options:
    """Everything the run switches carry, so adding one does not change every
    analysis signature."""
    def __init__(self, show_tau=False, hyperqb=False, k=None,
                 semantics="pes", check=False):
        self.show_tau = show_tau
        self.hyperqb = hyperqb
        self.k = k
        self.semantics = semantics
        self.check = check


# --------------------------------------------------------------------------- #
# The four analyses
# --------------------------------------------------------------------------- #
def _pipeline(opts):
    """The MSR backend this run selected."""
    if not opts.hyperqb:
        return msr_analyzer.MSR
    p = hyperqb_analyzer.MSR
    p.K, p.SEMANTICS = opts.k, opts.semantics
    return p


def run_msr_given_pi(path, timeout, opts):
    pipeline = _pipeline(opts)
    pipeline.TIMEOUT_SECONDS = timeout
    cli_viewer.print_cli_results(pipeline.analyze_trace(path),
                                 show_tau=opts.show_tau)


def _split_note(opts, step):
    """Analyses 2 and 3 are two steps, and --hyperQB only moves the second."""
    if opts.hyperqb:
        print(f"{_D}(--hyperQB: AutoHyper still {step} — a bounded model dump "
              f"reports NuSMV VARs only, and the system's outputs are DEFINEs, "
              f"so the trace cannot be read back out of it. The MSR of that "
              f"trace is then computed by HyperQB.){_0}")


def run_msr_find_pi(path, timeout, opts):
    _split_note(opts, "finds the trace")
    pi_finder.run_find(path, timeout, opts.show_tau, pipeline=_pipeline(opts))


def run_model_check(path, timeout, opts):
    # The verdict half stays exact for a second reason: this analysis reports
    # PASSED from an UNSAT, and a bounded UNSAT does not prove "every trace
    # satisfies Phi".
    _split_note(opts, "model-checks Phi and finds the counterexample")
    pi_finder.run_check(path, timeout, opts.show_tau, pipeline=_pipeline(opts))


def run_mtsr(path, timeout, opts):               # draws an automaton: no table
    if not os.path.exists(SPOT_ENV_PY):
        print(f"{_R}[!] spot environment python not found at {SPOT_ENV_PY} — "
              f"MTSR needs spot/autfilt/dot (see the README, Setup section).{_0}")
        return
    out_png = os.path.splitext(os.path.abspath(path))[0] + "_mtsr.png"
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(SPOT_ENV_PY) + os.pathsep + env.get("PATH", "")
    cmd = [SPOT_ENV_PY, os.path.join(_SCRIPTS, "MTSR_one_example.py"),
           os.path.abspath(path), out_png]
    if opts.check:
        cmd.append("--check")
    sys.stdout.flush()          # keep parent/child output ordered when piped
    try:
        subprocess.run(cmd, timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired:
        print(f"{_R}[!] MTSR TIMED OUT (> {timeout}s).{_0}")


# --------------------------------------------------------------------------- #
# The interactive checklist (multi-select, in-place redraw)
# --------------------------------------------------------------------------- #
def _menu_options(path):
    lasso_ok = has_lasso(path)
    note = "" if lasso_ok else "  (needs a [lasso] block — not in this file)"
    return [
        ("MSR with the given PI" + note,                       lasso_ok, run_msr_given_pi),
        ("MSR for Phi — find a PI automatically",              True,     run_msr_find_pi),
        ("Model checking of Phi (counterexample + why)",       True,     run_model_check),
        ("MTSR — Minimal Temporal Sufficient Reason",          True,     run_mtsr),
    ]


def checklist(path, options, opts):
    """Arrow-key multi-select. ENTER (or SPACE) toggles the highlighted option;
    a Start row at the bottom launches the run. Three switches ride along:
    `t` (result table: Pi only vs Tau next to Pi), `h` (solver: AutoHyper or
    HyperQB) and `c` (MTSR: synthesize, or also check the [cause] block).
    Returns the selected indices; `opts` is mutated in place."""
    import termios
    import tty

    cause_ok = has_cause(path)
    enabled = [i for i, (_l, ok, _f) in enumerate(options) if ok]
    START = len(options)                    # index of the Start row
    rows = enabled + [START]                # navigable rows
    cur = enabled[0]
    sel = {enabled[0]}                      # preselect the recommended first choice
    n_lines = len(options) + 6

    def draw(first=False):
        out = [] if first else [f"\x1b[{n_lines}A"]
        out.append(f"\r{_B}Select analyses for {os.path.basename(path)}{_0}\x1b[K\r\n")
        for i, (label, ok, _f) in enumerate(options):
            cursor = f"{_C}>{_0}" if i == cur else " "
            if not ok:
                box, label = f"{_D}[-]", label + _0
            else:
                box = f"{_G}[x]{_0}" if i in sel else "[ ]"
            out.append(f" {cursor} {box} {i + 1}. {label}\x1b[K\r\n")
        cursor = f"{_C}>{_0}" if cur == START else " "
        start = f"{_G}{_B}▶ Start{_0}" if sel else f"{_D}▶ Start (select at least one){_0}"
        out.append(f" {cursor}     {start}\x1b[K\r\n")
        mode = ("Pi + Tau (the witness R was read off)" if opts.show_tau
                else "Pi only, positions pinned by R in green")
        out.append(f"{_D}     table  [t]: {_0}{_C}{mode}{_0}\x1b[K\r\n")
        solver = ("HyperQB — bounded, fast; 'not found' means only "
                  "'not within k'" if opts.hyperqb
                  else "AutoHyper — exact")
        out.append(f"{_D}     solver [h]: {_0}{_C}{solver}{_0}\x1b[K\r\n")
        if not cause_ok:
            chk = f"{_D}(no [cause] block in this file){_0}"
        elif opts.check:
            chk = f"{_C}verify [cause] against the MTSR{_0}"
        else:
            chk = f"{_C}synthesize the MTSR only{_0}"
        out.append(f"{_D}     mtsr   [c]: {_0}{chk}\x1b[K\r\n")
        # Kept under ~80 columns on purpose: the menu redraws in place with a
        # fixed cursor-up count, and a line that wraps desynchronises it.
        out.append(f"{_D}ENTER/SPACE toggle · ↑/↓ move · a all · t/h/c switches"
                   f" · ENTER on Start runs · q quit{_0}\x1b[K\r\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def move(step):
        return rows[(rows.index(cur) + step) % len(rows)]

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSADRAIN)   # don't discard keys typed early
        draw(first=True)
        while True:
            ch = os.read(fd, 1)
            if ch == b"\x1b":                       # arrow keys: ESC [ A/B
                seq = os.read(fd, 2)
                if seq in (b"[A", b"OA"):
                    cur = move(-1)
                elif seq in (b"[B", b"OB"):
                    cur = move(+1)
            elif ch == b"k":
                cur = move(-1)
            elif ch == b"j":
                cur = move(+1)
            elif ch in (b"\r", b"\n", b" "):
                if cur == START:
                    if ch != b" " and sel:
                        return sel
                else:
                    sel.symmetric_difference_update({cur})
            elif ch in (b"a", b"A"):
                sel = set() if sel == set(enabled) else set(enabled)
            elif ch in (b"t", b"T"):
                opts.show_tau = not opts.show_tau
            elif ch in (b"h", b"H"):
                opts.hyperqb = not opts.hyperqb
            elif ch in (b"c", b"C"):
                if cause_ok:
                    opts.check = not opts.check
            elif ch in (b"1", b"2", b"3", b"4"):
                i = int(ch) - 1
                if i in enabled:
                    sel.symmetric_difference_update({i})
                    cur = i
            elif ch in (b"q", b"Q", b"\x03"):
                return set()
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def parse_run_arg(run_arg, options):
    if run_arg.strip().lower() == "all":
        return {i for i, (_l, ok, _f) in enumerate(options) if ok}
    sel = set()
    for tok in re.split(r"[,\s]+", run_arg.strip()):
        if not tok:
            continue
        i = int(tok) - 1
        if not (0 <= i < len(options)):
            raise SystemExit(f"--run: no option {tok} (valid: 1-{len(options)})")
        if not options[i][1]:
            raise SystemExit(f"--run: option {tok} is unavailable for this file "
                             f"({options[i][0]})")
        sel.add(i)
    if not sel:
        raise SystemExit("--run: nothing selected")
    return sel


def main():
    ap = argparse.ArgumentParser(description="TraceMSR — select which analyses to run.")
    ap.add_argument("file", help="input .txt")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per solver call")
    ap.add_argument("--run", help="skip the menu: comma-separated option numbers, or 'all'")
    ap.add_argument("--with-tau", action="store_true",
                    help="show the Tau witness too (extra table column + Tau "
                         "block); default is the compact Pi-only table")
    ap.add_argument("--hyperQB", dest="hyperqb", action="store_true",
                    help="solve analysis 1 with HyperQB 2.0 (bounded) instead "
                         "of AutoHyper (exact). A reason it finds is a proof; "
                         "finding none only means 'none within the bound'.")
    ap.add_argument("--k", type=int, default=None,
                    help="--hyperQB unrolling bound (default |prefix|+|loop|+2; "
                         "solve time grows steeply in k)")
    ap.add_argument("--hq-semantics", dest="hq_semantics", default="pes",
                    choices=("pes", "opt", "hpes", "hopt"),
                    help="--hyperQB bounded semantics (default pes: a sat is "
                         "sound)")
    ap.add_argument("--check", action="store_true",
                    help="analysis 4 verifies the instance's [cause] block "
                         "against the synthesized MTSR (language equivalence) "
                         "instead of only synthesizing it")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"no such file: {args.file}")
    options = _menu_options(args.file)

    opts = Options(show_tau=args.with_tau, hyperqb=args.hyperqb, k=args.k,
                   semantics=args.hq_semantics, check=args.check)
    if args.run:
        sel = parse_run_arg(args.run, options)
    elif sys.stdin.isatty():
        sel = checklist(args.file, options, opts)
        if not sel:
            print("nothing selected — bye.")
            return
    else:
        raise SystemExit("stdin is not a terminal; use --run (e.g. --run 2,3 or --run all)")

    # --check is a question about the MTSR, so it needs the MTSR. Adding it is
    # what the user asked for; refusing and making them retype --run would not
    # be.
    if opts.check and MTSR_INDEX not in sel:
        print(f"{_D}(--check verifies the MTSR, so analysis "
              f"{MTSR_INDEX + 1} was added to the run){_0}")
        sel = set(sel) | {MTSR_INDEX}
    if opts.check and not has_cause(args.file):
        print(f"{_Y}[!] --check needs a [cause] block in {os.path.basename(args.file)}, "
              f"and there is none — analysis {MTSR_INDEX + 1} will synthesize "
              f"the MTSR without checking it.{_0}")

    for i in sorted(sel):
        label, _ok, fn = options[i]
        print(f"\n{_B}{'=' * 78}\n [{i + 1}/{len(options)}] {label}\n{'=' * 78}{_0}")
        sys.stdout.flush()
        t0 = time.perf_counter()
        try:
            fn(args.file, args.timeout, opts)
        except ValueError as ve:                  # determinism firewall
            print(f"{_R}[!] {ve}{_0}")
        except subprocess.TimeoutExpired:
            print(f"{_R}[!] TIMED OUT (> {args.timeout}s).{_0}")
        except RuntimeError as err:
            print(f"{_R}[!] {err}{_0}")
        finally:
            # Safety net behind proc_guard's group kill: it cannot reach a
            # grandchild that put itself in a new process group. Only process
            # groups THIS run created are ever touched, so a tool running in
            # another terminal is invisible to it.
            leaked = proc_guard.reap_leaked_solvers()
            if leaked:
                print(f"{_D}(cleaned up {leaked} leftover solver process"
                      f"{'es' if leaked > 1 else ''}){_0}")
            print(f"{_D}time: {time.perf_counter() - t0:.1f}s{_0}")


if __name__ == "__main__":
    # Ctrl-C unwinds through proc_guard's `finally`, so the solver for the
    # analysis in flight is already dead by the time we get here. This catches
    # the rest: anything that escaped into its own process group, and the case
    # where the interrupt lands between two analyses.
    try:
        main()
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
    finally:
        proc_guard.reap_leaked_solvers()
