"""Job worker — claims pending jobs and executes them.

This is the executor the loop was missing. The loop mechanics (claim -> handle ->
complete/fail) are always real; the *heavy* command execution (running the
gauntlet / proposer subprocess) is gated behind ``HFT3_ORCH_EXEC=1`` so a worker
tick never fires a multi-hour job by accident, and a command still carrying
placeholder args (not fully materialized) is skipped rather than run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Callable, Optional

from . import job_runner


def exec_enabled() -> bool:
    return os.environ.get("HFT3_ORCH_EXEC") == "1"


def _default_handler(job: dict, do_exec: bool) -> dict:
    cmd = job.get("command", {}) or {}
    if not do_exec:
        return {"executed": False, "note": "HFT3_ORCH_EXEC!=1; command recorded only", "command": cmd}
    entry = cmd.get("entry")
    args = [str(a) for a in (cmd.get("args") or [])]
    if not entry or any("<" in a and ">" in a for a in args):
        return {"executed": False, "note": "command not fully materialized; skipped", "command": cmd}
    if job.get("host") == "chi404" and os.environ.get("HFT3_ORCH_CHI404") != "1":
        return {"executed": False, "note": "heavy CHI404 job; set HFT3_ORCH_CHI404=1 on the box to run", "command": cmd}
    full = [sys.executable, entry, *args] if str(entry).endswith(".py") else [entry, *args]
    proc = subprocess.run(full, capture_output=True, text=True)
    return {"executed": True, "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:]}


def run_once(*, handler: Optional[Callable] = None, do_exec: Optional[bool] = None) -> dict:
    do_exec = exec_enabled() if do_exec is None else do_exec
    h = handler or _default_handler
    processed = []
    for job in job_runner.list_jobs("pending"):
        jid = job["job_id"]
        try:
            job_runner.claim(jid)
        except Exception:
            continue  # claimed by another worker
        try:
            result = h(job, do_exec)
            job_runner.complete(jid, result)
            processed.append({"job_id": jid, "state": "done"})
        except Exception as exc:
            job_runner.fail(jid, str(exc))
            processed.append({"job_id": jid, "state": "failed", "error": str(exc)})
    return {"processed": len(processed), "jobs": processed}


def main(argv: Optional[list] = None) -> int:
    argparse.ArgumentParser(prog="lifecycle_worker").parse_args(argv)
    print(json.dumps(run_once(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
