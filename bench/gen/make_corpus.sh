#!/usr/bin/env bash
# Rebuild the paper's corpus from scratch and compare it with the shipped one.
#
#     gen/make_corpus.sh [outdir]          (from bench/; default outdir: rerun/corpus)
#
# 1. CATS_RL:  CATS's 204 systems x 10 random effects (gen/cats_randltl_gen.py), ~2 min
# 2. the gate: effect holds on the lasso and is not trivial (scripts/pi_gate.py), seconds
# 3. split:    |IP| <= 3 -> CATS_RL_ip3, |IP| = 4 -> CATS_RL_ip4
# Needs CATS's eval/syntcomp_instances.json: bash tools/fetch.sh cats (install.sh does it).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-rerun/corpus}"
SPY="${SPOT_ENV_PY:-$HOME/micromamba/envs/spot/bin/python3}"
export PATH="$(dirname "$SPY"):$PATH"
mkdir -p "$OUT"
"$SPY" gen/cats_randltl_gen.py -o "$OUT/CATS_RL"
"$SPY" scripts/pi_gate.py "$OUT/CATS_RL" --csv "$OUT/gate.csv" -o "$OUT/gate_ok.txt" --quiet
mkdir -p "$OUT/CATS_RL_ip3" "$OUT/CATS_RL_ip4"
while read -r p; do
    if grep -q "inputs=4," "$p"; then cp "$p" "$OUT/CATS_RL_ip4/"; else cp "$p" "$OUT/CATS_RL_ip3/"; fi
done < "$OUT/gate_ok.txt"
echo
for d in CATS_RL CATS_RL_ip3 CATS_RL_ip4; do
    if diff -rq "$OUT/$d" "$d" > /dev/null; then
        echo "  $d: identical to the shipped one ($(ls "$d" | wc -l) files)"
    else
        echo "  $d: DIFFERS from the shipped one"; diff -rq "$OUT/$d" "$d" | head -5
    fi
done
