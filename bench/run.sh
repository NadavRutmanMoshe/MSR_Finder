#!/bin/sh
# Re-measure the paper's benchmark. See scripts/rerun.py for everything; in short:
#
#     ./run.sh --plan          what would run, and how long it took on our machine
#     ./run.sh                 the subset (every 10th instance; 4.5 h on our laptop)
#     ./run.sh --full          all 987 instances (~45 h)
#
# Ctrl-C is safe at any point; the same command again continues where it stopped.
# Results go to bench/rerun/, never over bench/results/.
# Loads ../env.sh itself, so a terminal without it works too. No cd: a
# --corpus folder is relative to where you are.
here="$(cd "$(dirname "$0")" && pwd)"
[ -f "$here/../env.sh" ] && . "$here/../env.sh"
exec python3 "$here/scripts/rerun.py" "$@"
