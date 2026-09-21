#!/bin/sh
# The whole benchmark suite, in one line.
#
#     ./run.sh                 everything
#     ./run.sh --list          the plan and what is already done
#     ./run.sh --sample 50     a 50-instance subset (the same 50 for every tool)
#     ./run.sh --only MSR      one group
#
# Ctrl-C is safe at any point: the stage in flight loses at most the instance it
# was on, everything already measured is on disk, and re-running THIS SAME LINE
# continues from there.
exec python3 "$(dirname "$0")/scripts/run_all.py" "$@"
