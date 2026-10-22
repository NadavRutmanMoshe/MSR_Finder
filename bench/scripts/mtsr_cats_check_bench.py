#!/usr/bin/env python3
"""
cats_compare.py -- CATS cause CHECKING vs TraceMSR MTSR SYNTHESIS on the CATS
SYNTCOMP set.

    ~/micromamba/envs/spot/bin/python3 claude_scripts/cats_compare.py \
        <instances...> -o claude_scripts/cats_syntcomp_check.csv --timeout 120

TEMPORAL AGAINST TEMPORAL
-------------------------
CATS decides temporal causality: its cause is a QPTL formula over the inputs,
evaluated over the whole trace.  The only thing on our side that answers a
question of the same kind is MTSR -- the automaton characterizing the input
sequences that guarantee the effect.  MSR is a NON-temporal notion (a finite set
of (input, position) pairs), so putting it against CATS would compare two
different kinds of object.  This driver therefore measures MTSR only, and MSR
appears nowhere in it.

What is being compared, and what is deliberately NOT
----------------------------------------------------
CATS *verifies* a cause it is handed; we *synthesize* one.  There is no shared
answer to score here, so this table compares RUNTIME and reports CATS's verdict
as context.  The claim it supports is not "we are faster at the same task"; it
is "synthesis costs this much, and checking one candidate costs that much" --
which is the number a reader needs, because a checker only becomes a
synthesizer by enumeration (see cats_next_compare.py, where CATS does exactly
that, the enumeration cost is visible, and the two answers CAN be compared).

Why there is no "hand CATS our answer" column
---------------------------------------------
An earlier version rendered our MSR as a cause formula and asked CATS to verify
it.  That column is gone with MSR, and it cannot be rebuilt from MTSR: MTSR is
an omega-regular AUTOMATON, CATS's cause language is QPTL, and CATS's instance
format has no way to accept an automaton as the cause.  Not every omega-regular
language is expressible in LTL, so there is no general translation either.  The
honest consequence is that this comparison is runtime-only, and comparison 2 is
where the answers meet.

The instances
-------------
Built by `cats_gen.py` from CATS's own `eval/syntcomp_instances.json`, so the
SYSTEM is theirs, not a re-synthesis of the same TLSF spec (on the 77 shared
specs only 41 of our own ltlsynt strategies matched theirs).  Don't-care outputs
are resolved by `hoa_canonicalize` because our determinism firewall needs a
concrete Mealy machine; the SAME canonicalized HOA is written into the CATS
instance, so both tools see one object.

Run instances through `pi_gate.py` first.  On an instance where the effect does
not hold on the lasso there is no causal question: CATS fails at PC1 and CORP
answers "No cause exists", so an ungated table measures rejection paths.
49.2% of `cats_bench` fails that gate.
"""
import argparse
import csv
import glob
import json
import os
import random
import re
import subprocess
import sys
import time

import bench_lib as BL

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
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


_DEMO = _msr_finder_scripts()
for p in (_HERE, _DEMO):
    if p not in sys.path:
        sys.path.insert(0, p)

import cats_util  # noqa: E402

SPOT_PY = os.environ.get("SPOT_ENV_PY",
    os.path.expanduser("~/micromamba/envs/spot/bin/python3"))
RANDLTL = os.path.join(os.path.dirname(SPOT_PY), "randltl")
MTSR_MIN = os.path.join(_HERE, "mtsr_min.py")

FIELDS = [
    "instance", "inputs", "aps", "sys_states", "effect",
    # CATS on its own Figure-2 workload
    "cats_cause", "cats_status", "cats_failed", "cats_stages", "cats_s",
    # TraceMSR, temporal side only
    "mtsr_status", "mtsr_states", "mtsr_aps", "mtsr_det", "mtsr_dba_exists",
    "mtsr_mode", "mtsr_s", "mtsr_err",
]


# --------------------------------------------------------------------------- #
def random_cause(inputs, seed):
    """CATS's own cause generator, same flags (PrepareSyntcompInstance.fs).

    Printed back from spot's AST with every AP double-quoted: CATS's instance
    parser wants escaped strings, and spot's own printer glues `X foo` into the
    single AP `Xfoo`.
    """
    import spot
    import effect_gen as eg
    cmd = [RANDLTL, f"--seed={seed}", "--tree-size=22..30",
           "--ltl-priorities", "xor=0, M=0", "-n1"] + list(inputs)
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    line = next((l.strip() for l in out.splitlines() if l.strip()), "1")
    try:
        return eg.to_quoted(spot.formula(line))
    except Exception:
        return line


def _sub(cmd, timeout):
    """proc_guard, not subprocess.run(timeout=): the child shells out to spot,
    and a plain timeout kills only the direct child, leaving the solver spinning
    at 100% CPU into the next instance's measurement."""
    t0 = time.monotonic()
    runner = cats_util.proc_guard.run if cats_util.proc_guard else subprocess.run
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=timeout)
        return p, time.monotonic() - t0, False
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - t0, True


def _sub_graceful(cmd, timeout, grace=25):
    """Bound a child that itself owns solver process groups.

    proc_guard's group kill cannot reach a grandchild that called setsid, and
    every solver started through proc_guard does exactly that.  SIGKILLing the
    middle process therefore orphans the solver at 100% CPU -- the leak that
    pegged a core for 37 minutes on 2026-08-25 and silently taxed every
    measurement taken alongside it.

    So the inner bound is coreutils `timeout --signal=TERM`, which gives the
    child a chance to unwind through proc_guard.run's `finally` and killpg its
    own solver group.  `--kill-after` is the backstop if that hangs, and the
    proc_guard wall outside it is the backstop to the backstop.
    """
    wrapped = ["timeout", "--signal=TERM", f"--kill-after={grace}",
               str(timeout)] + list(cmd)
    p, dt, killed = _sub(wrapped, timeout + grace + 15)
    # coreutils reports 124 for the TERM deadline, 137 for the KILL backstop.
    if not killed and p is not None and p.returncode in (124, 137):
        killed = True
    return p, dt, killed


def survivors():
    """Solver processes still alive.  Reported, never killed -- this driver has
    no way to tell a stray of ours from one the user started in another shell,
    and proc_guard.reap_leaked_solvers deliberately refuses to guess either."""
    out = []
    if not os.path.isdir('/proc'):
        return out
    for e in os.listdir('/proc'):
        if not e.isdigit():
            continue
        try:
            with open(f'/proc/{e}/comm') as f:
                name = f.read().strip()
            if name not in ('autfilt', 'ltl2tgba', 'AutoHyper', 'CATS'):
                continue
            # Skip zombies.  A killed solver sits in Z for the instant between
            # its death and its parent reaping it, holds no CPU, and vanishes on
            # its own -- reporting it is a false alarm, and a false alarm here is
            # worse than none, because the next person to see one starts killing
            # things by name.  /proc/<pid>/stat field 3 is the state; the comm in
            # field 2 can contain spaces and ')', so split after the LAST ')'.
            with open(f'/proc/{e}/stat') as f:
                if f.read().rsplit(')', 1)[1].split()[0] == 'Z':
                    continue
            out.append(f"{name}({e})")
        except (OSError, IndexError):
            continue
    return out


def run_mtsr(path, timeout, cause):
    """The tool's MTSR and the tool's check of CATS's candidate (is it THE
    MTSR?), in one process, so `mtsr_s` is what the user of --check waits."""
    p, dt, killed = _sub_graceful([SPOT_PY, MTSR_MIN, os.path.abspath(path),
                                   "--json", "--cause", cause], timeout)
    if killed:
        return {"mtsr_status": "TIMEOUT", "mtsr_s": round(dt, 3)}
    if p is None or p.returncode != 0:
        err = next((ln for ln in ((p.stderr if p else "") or "").strip()
                    .splitlines()[::-1] if ln.strip()), "")
        return {"mtsr_status": "ERROR", "mtsr_s": round(dt, 3),
                "mtsr_err": err[:160]}
    try:
        r = json.loads(p.stdout.strip().split("\n")[-1])
    except Exception:
        return {"mtsr_status": "ERROR", "mtsr_s": round(dt, 3),
                "mtsr_err": "unparseable json"}
    return {"mtsr_status": "OK", "mtsr_states": r.get("states"),
            "mtsr_aps": r.get("aps"), "mtsr_det": r.get("deterministic"),
            "mtsr_dba_exists": r.get("dba_exists"), "mtsr_mode": r.get("mode"),
            "mtsr_verdict": r.get("cause_verdict", ""),
            "mtsr_s": round(dt, 3)}


# --------------------------------------------------------------------------- #
def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.txt")))
        elif p.endswith(".list"):
            with open(p) as f:
                files += [l.strip() for l in f if l.strip()]
        else:
            files += sorted(glob.glob(p))
    return files


MTSR_FIELDS = ["instance", "inputs", "aps", "sys_states", "effect",
               "mtsr_status", "mtsr_states", "mtsr_aps", "mtsr_det",
               "mtsr_dba_exists", "mtsr_mode", "mtsr_verdict", "mtsr_s",
               "mtsr_err"]
CATS_FIELDS = ["instance", "inputs", "aps", "sys_states", "effect",
               "cats_cause", "cats_status", "cats_failed", "cats_stages",
               "cats_s"]


def _pin_hash_seed():
    """The recorded candidates were drawn with `hash(name)` under
    PYTHONHASHSEED=0 (run_interleaved.sh set it). `hash(str)` is salted per
    process otherwise, so re-exec with the same seed: a re-run then draws
    exactly the candidate each recorded row used (`cats_cause`)."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable] + sys.argv)


def main():
    _pin_hash_seed()
    ap = argparse.ArgumentParser(
        description="MTSR synthesis vs CATS cause CHECKING, on SYNTCOMP. "
                    "Writes two CSVs.")
    ap.add_argument('paths', nargs='*', default=[BL.SYNTCOMP])
    ap.add_argument('-o', '--outdir',
                    default=os.path.join(BL.RESULTS, "MTSR_CATS_check"))
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--sample', type=int, default=0)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--workdir', default=None,
                    help="keep the exact files handed to CATS here")
    ap.add_argument('--no-gate', action='store_true')
    ap.add_argument('--candidates', metavar='CSV',
                    help="take each instance's candidate from the cats_cause "
                         "column of an earlier cats_check.csv instead of "
                         "drawing it (the draw itself is reproducible too: "
                         "see _pin_hash_seed)")
    args = ap.parse_args()
    given = {}
    if args.candidates:
        with open(args.candidates, newline="") as f:
            given = {r["instance"]: r["cats_cause"] for r in csv.DictReader(f)}
    BL.install_sigint()

    files = sorted(collect(args.paths))
    gate = BL.load_gate()
    if gate and not args.no_gate:      # gate first: see msr_bench for why
        files = [f for f in files
                 if gate.get(os.path.splitext(os.path.basename(f))[0]) == "OK"]
    files = BL.pick(files, args.sample, args.seed)

    workdir = args.workdir or os.path.join(
        os.environ.get('TMPDIR', '/tmp'), f"cats_check_{os.getpid()}")
    os.makedirs(workdir, exist_ok=True)

    rows = BL.PairRows(args.outdir, "mtsr.csv", MTSR_FIELDS,
                       "cats_check.csv", CATS_FIELDS)
    BL.banner("MTSR vs CATS --check  ->  "
              "results/MTSR_CATS_check/{mtsr,cats_check}.csv")
    todo = [f for f in files if os.path.basename(f) not in rows.done]
    print(f"{len(files)} gated instances, {len(rows.done)} already done, "
          f"{len(todo)} to run\n", flush=True)

    for i, path in enumerate(todo, 1):
        name = os.path.basename(path)
        data = cats_util.parse_instance(path)
        if not all(k in data for k in ('system', 'effect', 'lasso', 'inputs')):
            print(f"  [{i}/{len(todo)}] {name}: missing sections, skipped")
            continue
        ins = re.findall(r'"([^"]+)"', data['inputs'])
        aps = cats_util.system_aps(data['system'])
        row = {"instance": name, "inputs": len(ins), "aps": len(aps),
               "sys_states": cats_util.system_states(data['system']),
               "effect": data['effect'].replace("\n", " ")}

        # CATS on a random candidate cause -- their own Figure-2 protocol, with
        # their generator's flags (see random_cause). CATS is a CHECKER: it is
        # handed a candidate, it does not search for one.
        cause = given.get(name) or random_cause(ins, args.seed + (hash(name) & 0xffff))
        f_cats = os.path.join(workdir, name[:-4] + "__cats.txt")
        cats_util.write_instance(f_cats, name, ins, cause,
                                 data['effect'], data['lasso'], data['system'])
        r = cats_util.run("check", f_cats, args.timeout)
        row.update({"cats_cause": cause, "cats_status": r["status"],
                    "cats_failed": r.get("failed", ""),
                    "cats_stages": r.get("stages", ""),
                    "cats_s": r["wall_s"]})
        row.update(run_mtsr(path, args.timeout, cause))
        rows.write(row)
        BL.progress(i, len(todo), name,
                    f"cats={row['cats_status']:<9}{row['cats_s']:>7}s "
                    f"(failed {row['cats_failed'] or '-'}) | "
                    f"mtsr={row['mtsr_status']:<8}{row.get('mtsr_states')}st "
                    f"{row.get('mtsr_verdict', '')} {row.get('mtsr_s')}s")
        BL.tick()
    rows.close()
    left = survivors()
    if left:
        print(f"WARNING: solver processes still alive: {', '.join(left)}")


if __name__ == '__main__':
    main()
