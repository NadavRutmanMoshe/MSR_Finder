#!/usr/bin/env bash
# Does this installation actually work? Run the three things the README
# promises and check what comes back, not just the exit status.
#
#     docker run --rm msr-finder /opt/msr_finder/docker/smoke.sh
#     ./docker/smoke.sh                      (on a host install, too)
#
# It is also run during `docker build`, so an image that finishes building is
# an image whose solver, spot bindings and graphviz all work.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
ok()   { printf '\033[92m  ok\033[0m    %s\n' "$*"; }
bad()  { printf '\033[91m  FAIL\033[0m  %s\n' "$*"; fail=1; }
step() { printf '\033[1m%s\033[0m\n' "$*"; }

# Work on a copy: analyses 2-4 write next to the input file.
cp "$ROOT/examples/example_1.txt" "$ROOT/examples/example_1_cause.txt" "$WORK/"

run() {                      # <label> <file> <args...>
    ( cd "$ROOT/scripts" && timeout 300 "$PY" tracemsr.py "$WORK/$1" "${@:2}" ) \
        > "$WORK/out.txt" 2>&1
    return $?
}

step "1. MSR with the given PI  (AutoHyper + .NET)"
if run example_1.txt --run 1; then
    # The MSR of example_1 is this exact set of pins -- see the README.
    if grep -qF '{ (i1,0), (i2,2), (i2,3), (i1,4), (i2,4) }' "$WORK/out.txt"; then
        ok "reason R is the expected 5 pins"
    else
        bad "ran, but R is not the expected set:"; grep -A2 'Reason R' "$WORK/out.txt"
    fi
else
    bad "analysis 1 did not finish"; tail -15 "$WORK/out.txt"
fi

step "2. MTSR  (spot bindings + autfilt + graphviz)"
if run example_1.txt --run 4; then
    grep -q '^States: 4'            "$WORK/out.txt" && ok "minimized MTSR has 4 states" \
                                                    || bad "unexpected MTSR automaton"
    [ -s "$WORK/example_1_mtsr.png" ] && ok "example_1_mtsr.png rendered" \
                                      || bad "no PNG written (graphviz 'dot' missing?)"
else
    bad "analysis 4 did not finish"; tail -15 "$WORK/out.txt"
fi

step "3. --check  (cause checking, the CATS question)"
if run example_1_cause.txt --run 4 --check; then
    grep -q 'EQUIVALENT' "$WORK/out.txt" && ok "the shipped candidate cause is the MTSR" \
                                         || bad "verdict is not EQUIVALENT"
else
    bad "--check did not finish"; tail -15 "$WORK/out.txt"
fi

step "4. optional: --hyperQB  (bounded backend)"
if [ -x "${HYPERQB_BIN:-$ROOT/external_tools/HyperQB/target/release/HyperRUSTY}" ]; then
    if run example_1.txt --run 1 --hyperQB && grep -q 'Status: SAT' "$WORK/out.txt"; then
        ok "HyperQB answered and the witness was certified"
    else
        bad "HyperQB is present but the run did not produce a certified reason"
        tail -15 "$WORK/out.txt"
    fi
else
    printf '\033[2m  skip  no HyperQB build (build with --build-arg WITH_HYPERQB=1)\033[0m\n'
fi

echo
if [ "$fail" -eq 0 ]; then printf '\033[92mall good\033[0m\n'; else printf '\033[91msmoke test failed\033[0m\n'; fi
exit "$fail"
