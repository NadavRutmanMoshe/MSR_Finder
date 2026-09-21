#!/usr/bin/env python3
"""
cats_util.py -- shared plumbing for the CATS comparisons.

CATS (Beutner, Finkbeiner, Frenkel, Metzger, "Checking and Sketching Causality
in Reactive Systems") is a *checker*: `--check` decides whether a given cause
formula is an actual cause of a given effect on a given lasso, and `--sketch`
enumerates the fillings of a cause template and returns the first that checks
out.  Neither synthesizes a cause from the system, which is why our comparisons
against it are runtime comparisons except on the next-only sketch benchmark,
where the sketch happens to span exactly the same search space as MSR.

Build (once):
    git clone https://github.com/cats-tool/cats && cd cats
    git submodule update --init
    cd src/CATS && dotnet build -c release -o ../../app
    # then point app/paths.json at spot's autfilt and ltl2tgba

This module expects the result at `external_tools/cats/app/CATS`
(override with $CATS_DIR).  CATS targets net7.0, so DOTNET_ROOT must point at a
.NET 7 install -- exported here defensively, the same way variant_analyzer does
it for AutoHyper.
"""
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

CATS_DIR = os.environ.get("CATS_DIR", os.path.join(_ROOT, "tools", "CATS"))
CATS_BIN = os.path.join(CATS_DIR, "app", "CATS")

os.environ.setdefault("DOTNET_ROOT", os.path.expanduser("~/.dotnet"))
os.environ.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
os.environ.setdefault("DOTNET_NOLOGO", "1")

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


_PG = _msr_finder_scripts()
if _PG not in sys.path:
    sys.path.insert(0, _PG)
try:
    import proc_guard          # noqa: E402
except ImportError:
    proc_guard = None


# --------------------------------------------------------------------------- #
# TraceMSR .txt instances
# --------------------------------------------------------------------------- #
_HEADERS = ('info', 'inputs', 'cause', 'effect', 'lasso', 'system')


def parse_instance(path):
    data, cur, buf = {}, None, []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith('[') and s.endswith(']') and s[1:-1] in _HEADERS:
                if cur:
                    data[cur] = "".join(buf).strip()
                cur, buf = s[1:-1], []
            elif cur:
                buf.append(line)
    if cur:
        data[cur] = "".join(buf).strip()
    return data


def parse_lasso(text):
    aps, prefix, loop = [], [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("AP:"):
            aps = re.findall(r'"([^"]+)"', line)
        elif line.startswith("Prefix:"):
            prefix = re.findall(r'\{([^}]*)\}', line)
        elif line.startswith("Loop:"):
            loop = re.findall(r'\{([^}]*)\}', line)
    return aps, prefix, loop


def system_aps(system_text):
    m = re.search(r'AP:\s*\d+\s*(.*)', system_text)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def system_states(system_text):
    return len(re.findall(r'^State:', system_text, re.M))


# --------------------------------------------------------------------------- #
# writing a CATS instance
# --------------------------------------------------------------------------- #
def write_instance(path, info, inputs, cause, effect, lasso_text, system_text):
    """One CATS instance file.  `cause` is a QPTL formula (--check) or a sketch
    with `?{...}` holes (--sketch); CATS's parser is the same up to the holes."""
    with open(path, 'w') as f:
        f.write(f"[info]\n{info}\n\n")
        f.write("[inputs]\n" + " ".join(f'"{i}"' for i in inputs) + "\n\n")
        f.write(f"[cause]\n{cause}\n\n")
        f.write(f"[effect]\n{effect}\n\n")
        f.write("[lasso]\n" + lasso_text.strip() + "\n\n")
        f.write("[system]\n" + system_text.strip() + "\n")


# --------------------------------------------------------------------------- #
# running CATS
# --------------------------------------------------------------------------- #
_RE_STAGE = re.compile(r'Checking (PC\d+(?:\.\d+)?)[^.]*\.+\s*(holds|does not hold)')
_RE_FOUND = re.compile(r'Found Cause:\s*(.*)')


def run(mode, path, timeout, contingencies=False, log=True):
    """mode is 'check' or 'sketch'.  Returns a dict:

        status   IS_CAUSE / NO_CAUSE / FOUND / NOT_FOUND / TIMEOUT / ERROR
        cause    the formula CATS returned (sketch mode)
        stages   [('PC1','holds'), ('PC4','does not hold')] when --log is on
        failed   the first stage that did not hold, or ''
        wall_s   wall clock of the whole process
    """
    if not os.path.exists(CATS_BIN):
        return {"status": "ERROR", "err": f"CATS not built at {CATS_BIN}",
                "wall_s": 0.0}
    cmd = [CATS_BIN, f"--{mode}", os.path.abspath(path)]
    if contingencies:
        cmd.append("--contingencies")
    if log:
        cmd.append("--log")

    t0 = time.monotonic()
    try:
        runner = proc_guard.run if proc_guard is not None else subprocess.run
        p = runner(cmd, capture_output=True, text=True, timeout=timeout,
                   cwd=CATS_DIR)
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "wall_s": round(time.monotonic() - t0, 3)}
    dt = time.monotonic() - t0
    out = (p.stdout or "") + (p.stderr or "")

    stages = _RE_STAGE.findall(out)
    failed = next((s for s, v in stages if v == "does not hold"), "")
    row = {"stages": ";".join(f"{s}={v}" for s, v in stages), "failed": failed,
           "wall_s": round(dt, 3)}

    if "Is Cause" in out:
        row["status"] = "IS_CAUSE"
    elif "No Cause" in out:
        row["status"] = "NO_CAUSE"
    elif "Could not find cause" in out:
        row["status"] = "NOT_FOUND"
    else:
        m = _RE_FOUND.search(out)
        if m:
            row["status"] = "FOUND"
            row["cause"] = m.group(1).strip()
        else:
            row["status"] = "ERROR"
            row["err"] = next((ln for ln in out.strip().splitlines()[::-1]
                               if ln.strip()), "")[:200]
    return row
