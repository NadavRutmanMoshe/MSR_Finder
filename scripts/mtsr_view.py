#!/usr/bin/env python3
"""
Compatibility wrapper — the MTSR pipeline and its display (the minimized HOA
with accepting states in green) live in MTSR_one_example.py.  Kept so
`python3 mtsr_view.py <input.txt> <output.png>` keeps working.
"""
import sys

import MTSR_one_example

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 mtsr_view.py <input.txt> <output.png>")
        sys.exit(1)
    MTSR_one_example.run_workflow(sys.argv[1], sys.argv[2])
