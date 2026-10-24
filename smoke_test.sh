#!/usr/bin/env bash
# The smoke test: does this installation work? About 2 minutes.
#
#     source env.sh && ./smoke_test.sh
#
# 1. the tool        MSR (AutoHyper), MTSR + PNG, --check, and --hyperQB, each
#                    compared with its known answer           (docker/smoke.sh)
# 2. the paper       all 12 benchmark tables/figures of the paper regenerated from
#                    the recorded results, compared number by number with the
#                    submitted ones                           (bench/paper/make_tables.py)
#                    and the 37 numbers its text quotes       (bench/paper/check_text.py)
# 3. the baselines   one benchmark instance through every tool (AutoHyper, HyperQB,
#                    ExplainHyper, MTSR, CORP, CATS), answers compared with the
#                    recorded ones                            (bench/scripts/rerun.py)
#
# Every step prints ok / FAIL; the last line says whether all passed.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
[ -f "$ROOT/env.sh" ] && . "$ROOT/env.sh"
SPY="${SPOT_ENV_PY:-$HOME/micromamba/envs/spot/bin/python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()   { printf '\033[92m  ok\033[0m    %s\n' "$*"; }
bad()  { printf '\033[91m  FAIL\033[0m  %s\n' "$*"; fail=1; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "[1/3] the tool"
if "$ROOT/docker/smoke.sh"; then ok "the tool works"; else bad "the tool (see above)"; fi

step "[2/3] the paper's tables, from the recorded results"
if ( cd "$ROOT/bench/paper" && "$SPY" make_tables.py --out "$WORK/tables" ) > "$WORK/tables.txt" 2>&1; then
    grep -E '^  (tab|fig)_' "$WORK/tables.txt"
    ok "every regenerated table and figure has the paper's numbers"
else
    cat "$WORK/tables.txt"; bad "make_tables.py: some float differs or failed"
fi
if ( cd "$ROOT/bench/paper" && "$SPY" check_text.py ) > "$WORK/text.txt" 2>&1; then
    ok "$(tail -1 "$WORK/text.txt")"
else
    cat "$WORK/text.txt"; bad "check_text.py: a number in the text does not follow from the data"
fi

step "[3/3] one benchmark instance through every tool"
plan="$(cd "$ROOT/bench" && python3 scripts/rerun.py --plan --instances TorcsGearing__e02)"
if grep -q 'not available' <<< "$plan"; then
    grep 'not available' <<< "$plan"
    printf '\033[93m  skip\033[0m  baselines not installed (bash install.sh, without --tool-only)\n'
else
    if ( cd "$ROOT/bench" && timeout 900 python3 scripts/rerun.py --instances TorcsGearing__e02 --out "$WORK/rerun" \
         && python3 scripts/compare_runs.py --rerun "$WORK/rerun" ) > "$WORK/rerun.txt" 2>&1; then
        sed -n '/rows/,$p' "$WORK/rerun.txt"
        ok "every tool ran and gave the recorded answer"
    else
        cat "$WORK/rerun.txt"; tail -30 "$WORK/rerun/rerun.log" 2>/dev/null
        bad "a tool failed or answered differently"
    fi
fi

echo
if [ "$fail" -eq 0 ]; then printf '\033[92mSMOKE TEST PASSED\033[0m\n'; else printf '\033[91mSMOKE TEST FAILED\033[0m\n'; fi
exit "$fail"
