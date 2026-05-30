#!/usr/bin/env python3
"""DEPRECATED — log reprocessing bypasses daemon audit trail."""
import sys

print(
    "ERROR: chi404_reprocess_sweep_log.py moved to scripts/deprecated/ "
    "(log-laundering forbidden).",
    file=sys.stderr,
)
print(
    "Use: bash scripts/chi404_run_paper_latency_sweep.sh "
    "(daemon + real R|Trader UI exports only).",
    file=sys.stderr,
)
raise SystemExit(1)
