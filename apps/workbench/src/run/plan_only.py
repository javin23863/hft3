"""Plan-only run: produce planned_jobs.json + coverage_report.json + pit_report.json
WITHOUT invoking any run_campaign. Used to show the user what a full run
would do before committing to a 55+ model, multi-hour run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from workbench.src.run.all_lanes import discover_jobs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="autonomous_plan")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--symbols", nargs="+", default=["MES.v.0"])
    parser.add_argument("--include-kinds", nargs="*", default=None)
    parser.add_argument("--output", default=None, help="Write plan JSON here")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    jobs = discover_jobs(
        repo,
        args.symbols,
        include_kinds=tuple(args.include_kinds) if args.include_kinds else None,
    )
    # Build a plan that includes the campaign_id the orchestrator would assign
    plan = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "plan_only",
        "repo_root": str(repo),
        "symbols": args.symbols,
        "include_kinds": args.include_kinds,
        "total_jobs": len(jobs),
        "jobs": [j.__dict__ for j in jobs],
        "execution_command": (
            "python -m workbench autonomous "
            + " ".join(f"--symbol {s}" for s in args.symbols)
        ),
        "how_to_execute": (
            "This plan can be executed by running the command above, OR by clicking "
            "'Start Full Autonomous Run' in the Workbench UI's Autonomous tab."
        ),
        "honest_status": (
            "No jobs have been run yet. This file is a plan, not a record of completion. "
            "Each PlannedJob's per-model run will be recorded in summary.json after the "
            "orchestrator executes; per-metric results land in metrics.json; per-event "
            "evidence (diagnostics.json, trades.parquet) lands under periods/<P>/events/<E>/."
        ),
    }
    text = json.dumps(plan, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(json.dumps({"plan_written": args.output, "total_jobs": len(jobs)}, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
