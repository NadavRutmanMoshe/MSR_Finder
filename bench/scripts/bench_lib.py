#!/usr/bin/env python3
"""
bench_lib -- the plumbing every benchmark driver shares.

The whole suite is designed around one property: **it can be stopped with Ctrl-C
at any moment and resumed by re-running the same command.** Everything here
exists to make that true rather than nearly true.

  * Rows are appended and flushed ONE AT A TIME. A run that is killed keeps
    every instance it finished.
  * On resume, the CSV is read back and the instances already in it are skipped.
  * A file killed mid-write can end with half a line. `resume` truncates that
    partial line before appending, so the CSV never grows a corrupt row. This
    is not hypothetical -- it is what a SIGKILL between `write` and `flush`
    leaves behind.
  * A driver never rewrites a row. If a measurement needs to change, delete the
    CSV (or point `-o` somewhere new); silently mixing two constructions in one
    file has cost this project a result before.

TIMING. `wall_s` is whole-process wall clock for the tool being measured, taken
around the subprocess call and nothing else. Verification passes are separate
scripts and their time is never added to a tool's row.
"""
import csv
import os
import signal
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# bench/ ships inside the tool repo (MSR_Finder/bench); the recorded runs
# were taken with it beside the tool (demo_tool/bench). Accept both, and let
# $MSR_FINDER_SCRIPTS override.
def _msr_finder_scripts():
    _here = _HERE
    _c = [os.environ.get("MSR_FINDER_SCRIPTS"),
          os.path.join(_here, "..", "..", "scripts"),
          os.path.join(_here, "..", "..", "MSR_Finder", "scripts")]
    for _p in _c:
        if _p and os.path.exists(os.path.join(_p, "proc_guard.py")):
            return os.path.abspath(_p)
    return os.path.abspath(_c[-1])


_DEMO_SCRIPTS = _msr_finder_scripts()
for _p in (_HERE, _DEMO_SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import proc_guard  # noqa: E402  (group kill + orphan reaper)

BENCH = os.path.abspath(os.path.join(_HERE, ".."))
TOOLS = os.path.join(BENCH, "tools")
RESULTS = os.path.join(BENCH, "results")
SYNTCOMP = os.path.join(BENCH, "SYNTCOMP")
NEXTS = os.path.join(BENCH, "nexts")

#: Interpreters. Each tool has its own environment and they are not
#: interchangeable: spot's Python bindings live in one, ExplainHyper's
#: py-aiger/pysat/satispy stack in another.
SPOT_PY = os.environ.get("SPOT_ENV_PY",
                         os.path.expanduser("~/micromamba/envs/spot/bin/python3"))
EXPLAIN_PY = os.environ.get(
    "EXPLAINHYPER_ENV_PY",
    os.path.expanduser("~/micromamba/envs/explainhyper/bin/python3"))

#: AutoHyper and CATS are .NET apphosts; with a user-local runtime they will not
#: start unless DOTNET_ROOT points at it.
if "DOTNET_ROOT" not in os.environ:
    _dn = os.path.expanduser("~/.dotnet")
    if os.path.exists(os.path.join(_dn, "dotnet")):
        os.environ["DOTNET_ROOT"] = _dn
os.environ.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
os.environ.setdefault("DOTNET_NOLOGO", "1")

_G = "\033[92m"; _R = "\033[91m"; _Y = "\033[93m"; _D = "\033[2m"
_B = "\033[1m"; _0 = "\033[0m"


# --------------------------------------------------------------------------- #
# Resumable CSV
# --------------------------------------------------------------------------- #
def _truncate_partial_line(path):
    """Drop a trailing half-written line, if any.

    A row is written then flushed; a kill between the two leaves a line with no
    newline. Appending after it would glue two rows together into one corrupt
    record, which no later reader can detect."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, "rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) == b"\n":
            return
        f.seek(0)
        data = f.read()
        cut = data.rfind(b"\n")
        f.seek(0)
        f.truncate(cut + 1 if cut >= 0 else 0)
    print(f"{_Y}[resume] dropped a partial last line from "
          f"{os.path.basename(path)}{_0}")


class Rows:
    """An append-only, resumable CSV keyed by one column."""

    def __init__(self, path, fields, key="instance"):
        self.path, self.fields, self.key = path, list(fields), key
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _truncate_partial_line(path)
        self.done = self._read_done()
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        if not new:
            self._check_header()
        self._fh = open(path, "a", newline="")
        self._w = csv.DictWriter(self._fh, fieldnames=self.fields,
                                 extrasaction="ignore")
        if new:
            self._w.writeheader()
            self._fh.flush()

    def _check_header(self):
        """Refuse to append rows shaped differently from the file's header.

        Resuming is the normal case and has to stay cheap, but a column added
        or removed since the file was written would be appended SILENTLY out of
        alignment -- every later row shifted by one, and nothing to see in the
        output. So the schema is compared once, on open, and a mismatch stops
        the stage with the fix spelled out."""
        with open(self.path, newline="") as f:
            head = next(csv.reader(f), None)
        if head is None or head == self.fields:
            return
        gone = [c for c in head if c not in self.fields]
        added = [c for c in self.fields if c not in head]
        raise SystemExit(
            f"\n{_R}{self.path} was written with a different set of columns"
            f"{_0}\n"
            + (f"  added since:   {', '.join(added)}\n" if added else "")
            + (f"  no longer written: {', '.join(gone)}\n" if gone else "")
            + f"\nAppending to it would shift every new row against the header."
              f" The rows already\nin it were produced by the older code, so"
              f" they are not comparable with new ones\neither: move the file"
              f" aside and let this stage rebuild it.\n\n"
              f"    mv {self.path} {self.path}.old\n")

    def _read_done(self):
        if not os.path.exists(self.path):
            return set()
        with open(self.path, newline="") as f:
            try:
                return {r[self.key] for r in csv.DictReader(f) if r.get(self.key)}
            except csv.Error:
                return set()

    def write(self, row):
        self._w.writerow(row)
        self._fh.flush()
        os.fsync(self._fh.fileno())      # survive a hard kill, not just an exit
        self.done.add(row[self.key])

    def close(self):
        self._fh.close()


class PairRows:
    """Two CSVs written from ONE measurement.

    The MTSR comparisons produce a row about our tool and a row about the
    baseline in the same pass, and the results layout asks for them in separate
    files. Splitting afterwards would risk the two drifting out of alignment
    (a resumed run, a re-measured instance); writing both here cannot, because
    an instance is only marked done once BOTH files have it."""

    def __init__(self, dirpath, ours_name, ours_fields,
                 theirs_name, theirs_fields, key="instance"):
        self.ours = Rows(os.path.join(dirpath, ours_name), ours_fields, key)
        self.theirs = Rows(os.path.join(dirpath, theirs_name), theirs_fields, key)
        self.key = key

    @property
    def done(self):
        return self.ours.done & self.theirs.done

    def write(self, row):
        self.ours.write(row)
        self.theirs.write(row)

    def close(self):
        self.ours.close()
        self.theirs.close()


# --------------------------------------------------------------------------- #
# Running a tool
# --------------------------------------------------------------------------- #
class Timeout(Exception):
    pass


#: How long a timed-out child gets to clean up after SIGTERM before the group
#: is killed outright. Long enough for a Python `finally` and one killpg; short
#: enough that a hung tool does not add minutes to the sweep.
TERM_GRACE = 8


def run(cmd, timeout, cwd=None, env=None):
    """(stdout+stderr, seconds). Raises Timeout. Leaves nothing behind.

    ON TIMEOUT: SIGINT, then SIGTERM, then kill the group. This ladder is the
    whole reason the function exists rather than `proc_guard.run`.

    Some of what we launch is itself a solver DRIVER (mtsr_min drives autfilt,
    CATS drives autfilt), and a driver puts its own child in a NEW PROCESS
    GROUP. Killing our child's group therefore never reaches the grandchild:
    SIGKILL removes the only process that knew how to clean up, and the
    grandchild keeps a core busy.

    SIGINT and not SIGTERM, and this distinction is the fix: Python's default
    SIGTERM disposition terminates the interpreter WITHOUT unwinding, so the
    driver's `finally` -- the very code that kills its autfilt -- never runs.
    SIGINT raises KeyboardInterrupt, which does unwind. Measured on
    `mtsr_min` at a 3s timeout:

        killpg SIGKILL   -> autfilt --sat-minimize -B still running
        SIGTERM          -> autfilt --sat-minimize -B still running
        SIGINT           -> nothing left

    SIGTERM is still tried second, for a child that ignores SIGINT, and the
    group SIGKILL is the backstop for one that ignores both. Our own reaper
    cannot help here: proc_guard only reaps groups THIS interpreter created,
    deliberately, so that it never kills a stranger's process.
    """
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=cwd,
                            env=env, start_new_session=(os.name == "posix"))
    if os.name == "posix":
        proc_guard.register_group(proc.pid)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        out = err = ""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                proc.send_signal(sig)          # the CHILD, not the group
                out, err = proc.communicate(timeout=TERM_GRACE)
                break
            except subprocess.TimeoutExpired:
                continue
            except ProcessLookupError:
                break
        else:
            _kill_group(proc)
            out, err = proc.communicate()
        raise Timeout(round(time.perf_counter() - t0, 3))
    finally:
        _kill_group(proc)                      # no-op on the normal path
        if os.name == "posix":
            proc_guard.release_group(proc.pid)
    return (out or "") + "\n" + (err or ""), round(time.perf_counter() - t0, 3)


def _kill_group(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


#: Process names the suite is responsible for. A superset of proc_guard.SOLVERS
#: -- that list gates proc_guard's own reaping and is deliberately narrow; this
#: one is paired with an ownership test strong enough to carry a broader list.
WATCH = ("autfilt", "ltl2tgba", "AutoHyper", "HyperRUSTY", "quabs", "CATS",
         "minisat", "z3")

#: The run this reaper belongs to. run_all exports both so that a driver, which
#: is a child, tests descent from the SUITE rather than from itself.
SUITE_PID = int(os.environ.get("BENCH_SUITE_PID", os.getpid()))
SUITE_START = float(os.environ.get("BENCH_SUITE_START", time.time()))
LEAK_LOG = os.environ.get("BENCH_LEAK_LOG", os.path.join(RESULTS, "leaks.log"))

#: Which stage is running, so a log line from a two-day unattended run names
#: the driver that leaked. run_all sets it; a driver run on its own falls back
#: to its own script name.
STAGE = os.environ.get("BENCH_STAGE") or os.path.basename(sys.argv[0])

#: Set BENCH_NO_REAP=1 (run.sh --no-reap) to report without killing.
REAP = os.environ.get("BENCH_NO_REAP", "") in ("", "0")

#: A process that started within this many seconds of the suite is treated as
#: pre-existing. Clock granularity, not caution: /proc starttime resolves to
#: clock ticks and the suite's own start is a float.
_START_SLACK = 5.0

_HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_reported_preexisting = set()


def _boot_time():
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except OSError:
        pass
    return 0.0


_BOOT = _boot_time()


def _proc(pid):
    """(name, ppid, start_wall, cmdline) for a pid, or None if it is gone.

    /proc/<pid>/stat's comm field is parenthesised and may itself contain
    spaces and parentheses, so the fields are taken from after the LAST ')'.
    Field 4 is ppid and field 22 is starttime in clock ticks since boot."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            raw = f.read()
        rest = raw[raw.rindex(")") + 1:].split()
        name = raw[raw.index("(") + 1:raw.rindex(")")]
        ppid = int(rest[1])
        start = _BOOT + float(rest[19]) / _HZ
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        return name, ppid, start, cmd
    except (OSError, ValueError, IndexError):
        return None


def _descends_from(pid, root, limit=40):
    """Is `pid` inside `root`'s process tree? Bounded, so a pid cycle from
    reuse cannot spin."""
    seen = 0
    while pid > 1 and seen < limit:
        if pid == root:
            return True
        info = _proc(pid)
        if info is None:
            return False
        pid = info[1]
        seen += 1
    return False


def _log_leak(text):
    try:
        os.makedirs(os.path.dirname(LEAK_LOG), exist_ok=True)
        with open(LEAK_LOG, "a") as f:
            f.write(text + "\n")
            f.flush()
    except OSError:
        pass


def reap_orphans(stage=None):
    """Kill solver processes THIS RUN orphaned, and record every one.

    Two conditions, both required, and together they are the whole safety
    argument for killing rather than merely reporting:

      1. it STARTED AFTER this suite did -- so a solver the user already had
         running, in this terminal or another, is never a candidate;
      2. it is NOT in the suite's process tree -- so anything a stage is
         legitimately using right now is never a candidate either. A live
         solver is a descendant of run_all; only once its driver dies does it
         get reparented to init, and that is exactly what an orphan is.

    Every kill is appended to results/leaks.log with the stage that leaked it,
    so a two-day unattended run leaves evidence to fix rather than a mystery.
    Processes that fail condition 1 are logged once, as untouched.

    Returns the list of descriptions killed."""
    stage = stage or STAGE
    if os.name != "posix" or not os.path.isdir("/proc"):
        return []
    killed = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == os.getpid():
            continue
        info = _proc(pid)
        if info is None or info[0] not in WATCH:
            continue
        name, _ppid, start, cmd = info
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        if start < SUITE_START - _START_SLACK:
            if pid not in _reported_preexisting:      # say it once, then hush
                _reported_preexisting.add(pid)
                _log_leak(f"{when}  PRE-EXISTING (untouched)  {name}({pid})  "
                          f"age={time.time() - start:.0f}s  {cmd[:120]}")
            continue
        if _descends_from(pid, SUITE_PID):
            continue                                  # in use by a live stage
        age = time.time() - start
        desc = f"{name}({pid})"
        if not REAP:
            _log_leak(f"{when}  ORPHAN (reaping disabled)  {desc}  "
                      f"age={age:.0f}s  stage={stage}  {cmd[:120]}")
            continue
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                continue
        killed.append(desc)
        _log_leak(f"{when}  ORPHAN KILLED  {desc}  age={age:.0f}s  "
                  f"stage={stage}  {cmd[:120]}")
    return killed


def survivors(exclude=()):
    """Solver-ish processes currently running, as 'name(pid)' strings.
    Reports only -- reap_orphans is the one that acts."""
    if os.name != "posix" or not os.path.isdir("/proc"):
        return []
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) in exclude:
            continue
        info = _proc(entry)
        if info and info[0] in WATCH:
            out.append(f"{info[0]}({entry})")
    return out


# --------------------------------------------------------------------------- #
# Instance selection
# --------------------------------------------------------------------------- #
def pick(paths, sample=0, seed=0):
    """A stable subset. Sampling is seeded and sorted, so two drivers given the
    same --sample/--seed measure the SAME instances -- otherwise the per-tool
    CSVs cannot be joined."""
    paths = sorted(paths)
    if sample and sample < len(paths):
        import random
        paths = sorted(random.Random(seed).sample(paths, sample))
    return paths


def load_gate(path=None):
    """instance -> PI_VAL verdict, from pi_gate.py's CSV.

    A causal question is only well posed when the effect actually occurred on
    the trace: CORP answers "No cause exists" and CATS's check fails at PC1
    whenever it did not, so an ungated benchmark measures the tools' rejection
    paths instead of their work. Half of this corpus fails the gate. Returns {}
    if the gate has not been run, in which case drivers run everything."""
    path = path or os.path.join(RESULTS, "gate.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {os.path.splitext(r["file"])[0]: r["verdict"]
                for r in csv.DictReader(f) if r.get("file")}


# --------------------------------------------------------------------------- #
# Thermal duty cycle
# --------------------------------------------------------------------------- #
#: Pause COOLDOWN_S seconds after every COOLDOWN_EVERY_S seconds of work.
#: Time-based, not instance-based, on purpose: instances here range from 0.2s
#: to the full timeout, so "every N instances" would be anything from seconds
#: to an hour of solid load. Set via run_all's --cooldown / --cooldown-every,
#: passed down as environment so a driver run on its own honours them too.
#: 0 disables.
COOLDOWN_S = float(os.environ.get("BENCH_COOLDOWN_S", 20))
COOLDOWN_EVERY_S = float(os.environ.get("BENCH_COOLDOWN_EVERY_S", 300))
_last_cool = time.time()


def cool():
    """Called between instances. Sleeps if enough work has piled up since the
    last break. Cheap and safe to call in a tight loop."""
    global _last_cool
    if COOLDOWN_S <= 0 or COOLDOWN_EVERY_S <= 0:
        return
    if time.time() - _last_cool < COOLDOWN_EVERY_S:
        return
    span = (f"{COOLDOWN_EVERY_S / 60:.0f} min" if COOLDOWN_EVERY_S >= 60
            else f"{COOLDOWN_EVERY_S:.0f}s")
    print(f"{_D}[cooling {COOLDOWN_S:.0f}s after {span} of work]{_0}", flush=True)
    try:
        time.sleep(COOLDOWN_S)
    except KeyboardInterrupt:
        raise
    _last_cool = time.time()


def tick(stage=None):
    """Between instances: reap anything we orphaned, then cool if it is time.

    Reaping runs EVERY instance -- it is a /proc scan, a few milliseconds --
    because a leaked solver taxes every measurement taken after it, so the
    sooner it goes the less it distorts. Cooling is on its own timer."""
    killed = reap_orphans(stage or STAGE)
    if killed:
        print(f"{_Y}[reaped {len(killed)} orphaned solver"
              f"{'s' if len(killed) > 1 else ''}: {', '.join(killed)}]{_0}\n"
              f"{_D}    logged to {os.path.relpath(LEAK_LOG, BENCH)}{_0}",
              flush=True)
    cool()


def progress(i, n, name, note=""):
    print(f"{_D}[{i}/{n}]{_0} {name:<48} {note}", flush=True)


def banner(title):
    print(f"\n{_B}{'=' * 78}\n {title}\n{'=' * 78}{_0}", flush=True)


def install_sigint():
    """Make Ctrl-C a clean stop rather than a stack trace.

    The suite is meant to be interrupted, so an interrupt is a normal exit path:
    the current row is lost, every earlier row is on disk, and re-running
    continues from there."""
    def handler(_sig, _frm):
        print(f"\n{_Y}[interrupted] stopping. Re-run the same command to "
              f"continue where this left off.{_0}", flush=True)
        proc_guard.reap_leaked_solvers()
        sys.exit(130)
    signal.signal(signal.SIGINT, handler)
