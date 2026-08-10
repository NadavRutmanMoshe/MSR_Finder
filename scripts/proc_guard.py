#!/usr/bin/env python3
"""
subprocess.run, but it does not leak solver processes when a run is cut short.

The problem
-----------
`subprocess.run(..., timeout=T)` kills only the DIRECT child. AutoHyper is not a
leaf: it shells out to Spot (`autfilt`, `ltl2tgba`) and those grandchildren are
in the same process group but are not touched when we kill their parent. They
get re-parented and keep running at ~100% CPU.

They do not run literally forever -- each exits once its own computation
finishes. But the runs we cut short are precisely the ones whose computation
takes effectively forever, so in practice a timed-out analysis can leave a core
pegged long after the tool has printed its verdict and returned to the prompt.
The same happens on Ctrl-C, which kills the child and re-raises without ever
reaching the grandchildren.

The fix
-------
Put the child in its own process GROUP (`start_new_session=True`) so one
`killpg` reaches the whole tree, and do it in a `finally` so it runs on every
exit path -- timeout, Ctrl-C, and the normal one.

Killing the group on the SUCCESS path is deliberate even though nothing should
be left: AutoHyper waits for its Spot children before exiting, so a clean run
leaks nothing and the killpg is a no-op. It costs one failed syscall and removes
the need to be right about that.

Platform note: process groups are POSIX-only. On Windows `start_new_session` is
not available, so this degrades to exactly the old behaviour (kill the direct
child). Containing a tree there needs a Job object, which is out of scope --
the tool's own runs are on Linux/WSL.
"""
import os
import signal
import subprocess

#: POSIX only -- see the platform note above.
_POSIX = os.name == 'posix'

#: comm names of the processes this tool is responsible for. A second condition,
#: not a sufficient one -- see reap_leaked_solvers.
#:
#: 'dotnet' is deliberately NOT here. AutoHyper ships as an apphost binary, so
#: it shows up as comm='AutoHyper' and never as 'dotnet' (checked with ps during
#: a live run). Listing 'dotnet' would therefore match none of our processes
#: while matching any unrelated .NET service the user happens to run under
#: systemd -- which is orphaned by design, and would be killed.
SOLVERS = ('autfilt', 'ltl2tgba', 'AutoHyper')

#: Process groups this interpreter created, and the subset with a run still in
#: flight. Membership is what makes a process OURS -- see reap_leaked_solvers.
_OUR_GROUPS = set()
_ACTIVE_GROUPS = set()


def register_group(pgid):
    """Declare a process group as ours and currently running.

    `run()` does this for itself. Callers that drive their own `Popen` with
    `start_new_session=True` should call it too (pgid == the child's pid), so
    their strays are covered by the same reaper instead of a second copy of it.
    """
    _OUR_GROUPS.add(pgid)
    _ACTIVE_GROUPS.add(pgid)


def release_group(pgid):
    """Mark a group's run as finished; it stays ours, and becomes reapable."""
    _ACTIVE_GROUPS.discard(pgid)


def _kill_group(proc):
    """SIGKILL the child's whole process group; a no-op once it is gone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run(cmd, timeout=None, input=None, check=False, **popen_kw):
    """Drop-in for `subprocess.run(capture_output=True, text=True, ...)`.

    Same return value and same exceptions (`TimeoutExpired`,
    `CalledProcessError`), so call sites only change which function they call.
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=_POSIX, **popen_kw)
    # start_new_session makes the child a group leader, so pgid == pid.
    pgid = proc.pid if _POSIX else None
    if pgid is not None:
        register_group(pgid)
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()      # reap, so no zombie is left behind
        raise subprocess.TimeoutExpired(cmd, timeout, out, err)
    finally:
        # Every path, not just the timeout: Ctrl-C unwinds through here too, and
        # that is the case the old code missed entirely.
        _kill_group(proc)
        if pgid is not None:
            release_group(pgid)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, out, err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def reap_leaked_solvers():
    """Kill solver processes left behind by OUR OWN finished runs. Returns count.

    A safety net for the one leak the group kill cannot reach: a grandchild that
    called setsid and so left the group before killpg named it.

    Ownership is decided by PROCESS GROUP, not by name and not by whether the
    process looks orphaned. A process is killed only if all three hold:

      1. its pgid is a group THIS interpreter created (`_OUR_GROUPS`) -- so a
         solver the user started in another terminal, or that some other program
         left orphaned, is invisible here no matter what it is called;
      2. that group's run has already finished (`_ACTIVE_GROUPS`) -- so a
         concurrent analysis in this same process is never reaped mid-solve;
      3. its name is in SOLVERS -- redundant against (1), and kept only to cover
         pid reuse: a recycled pgid would have to land on a Spot-named process
         to be mistaken for ours.

    The cost of this rule, accepted deliberately: strays from an EARLIER run of
    the tool are no longer cleaned up, because their groups belong to an
    interpreter that is gone. Not killing a stranger's process matters more than
    tidying up after a previous crash.
    """
    if not _POSIX or not os.path.isdir('/proc'):
        return 0
    reapable = _OUR_GROUPS - _ACTIVE_GROUPS
    if not reapable:
        return 0
    killed = 0
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            pid = int(entry)
            if pid in (os.getpid(), os.getpgrp()):
                continue
            with open(f'/proc/{entry}/comm') as f:
                if f.read().strip() not in SOLVERS:
                    continue
            # /proc/<pid>/stat: pgid is field 5, and the comm in field 2 may
            # itself contain spaces or ')', so split after the LAST ')'.
            with open(f'/proc/{entry}/stat') as f:
                if int(f.read().rsplit(')', 1)[1].split()[2]) not in reapable:
                    continue
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except (OSError, ValueError, IndexError):
            continue
    return killed
