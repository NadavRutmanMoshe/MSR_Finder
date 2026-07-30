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
import msr_analyzer         # noqa: E402  (the MSR pipeline)
import pi_finder            # noqa: E402  (find / model-check flows)

SPOT_ENV_PY = os.environ.get(
    "SPOT_ENV_PY", os.path.expanduser("~/micromamba/envs/spot/bin/python3"))

_B = "\033[1m"; _D = "\033[2m"; _C = "\033[96m"; _G = "\033[92m"; _R = "\033[91m"; _0 = "\033[0m"


def has_lasso(path):
    with open(path, "r") as f:
        content = f.read()
    return "[lasso]" in content and re.search(r'Loop:\s*\{', content) is not None


# --------------------------------------------------------------------------- #
# The four analyses
# --------------------------------------------------------------------------- #
def run_msr_given_pi(path, timeout):
    msr_analyzer.MSR.TIMEOUT_SECONDS = timeout
    cli_viewer.print_cli_results(msr_analyzer.MSR.analyze_trace(path))


def run_msr_find_pi(path, timeout):
    pi_finder.run_find(path, timeout)


def run_model_check(path, timeout):
    pi_finder.run_check(path, timeout)


def run_mtsr(path, timeout):
    if not os.path.exists(SPOT_ENV_PY):
        print(f"{_R}[!] spot environment python not found at {SPOT_ENV_PY} — "
              f"MTSR needs spot/autfilt/dot (see the README, Setup section).{_0}")
        return
    out_png = os.path.splitext(os.path.abspath(path))[0] + "_mtsr.png"
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(SPOT_ENV_PY) + os.pathsep + env.get("PATH", "")
    sys.stdout.flush()          # keep parent/child output ordered when piped
    try:
        subprocess.run([SPOT_ENV_PY, os.path.join(_SCRIPTS, "MTSR_one_example.py"),
                        os.path.abspath(path), out_png],
                       timeout=timeout, env=env, check=False)
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


def checklist(path, options):
    """Arrow-key multi-select. ENTER (or SPACE) toggles the highlighted option;
    a Start row at the bottom launches the run. Returns the chosen indices."""
    import termios
    import tty

    enabled = [i for i, (_l, ok, _f) in enumerate(options) if ok]
    START = len(options)                    # index of the Start row
    rows = enabled + [START]                # navigable rows
    cur = enabled[0]
    sel = {enabled[0]}                      # preselect the recommended first choice
    n_lines = len(options) + 3

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
        out.append(f"{_D}ENTER/SPACE toggle · ↑/↓ move · a all · "
                   f"ENTER on Start runs · q quit{_0}\x1b[K\r\n")
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
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"no such file: {args.file}")
    options = _menu_options(args.file)

    if args.run:
        sel = parse_run_arg(args.run, options)
    elif sys.stdin.isatty():
        sel = checklist(args.file, options)
        if not sel:
            print("nothing selected — bye.")
            return
    else:
        raise SystemExit("stdin is not a terminal; use --run (e.g. --run 2,3 or --run all)")

    for i in sorted(sel):
        label, _ok, fn = options[i]
        print(f"\n{_B}{'=' * 78}\n [{i + 1}/{len(options)}] {label}\n{'=' * 78}{_0}")
        sys.stdout.flush()
        t0 = time.perf_counter()
        try:
            fn(args.file, args.timeout)
        except ValueError as ve:                  # determinism firewall
            print(f"{_R}[!] {ve}{_0}")
        except subprocess.TimeoutExpired:
            print(f"{_R}[!] TIMED OUT (> {args.timeout}s).{_0}")
        except RuntimeError as err:
            print(f"{_R}[!] {err}{_0}")
        finally:
            print(f"{_D}time: {time.perf_counter() - t0:.1f}s{_0}")


if __name__ == "__main__":
    main()
