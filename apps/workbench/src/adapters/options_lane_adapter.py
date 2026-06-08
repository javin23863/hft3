"""Adapter: options_lane fixture backtest for PDF_MODEL_5 (B7 operational, data-isolated)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, List

from workbench.src.core.protocol import Diagnostics, ModelConfig, WorkbenchModel
from workbench.src.run.run_context import RunContext


class OptionsLaneAdapter(WorkbenchModel):
    """Runs options_lane.pipeline fixture-backtest; artifacts under research_cards/parity/."""

    def __init__(self, config: ModelConfig):
        self.model_id = config.model_id
        self.config = config

    def validate_inputs(self, ctx: RunContext) -> List[str]:
        fixture = ctx.repo_root / "options_lane" / "fixtures" / "fair_futures_quotes.ndjson"
        if not fixture.is_file():
            return [f"options fixture missing: {fixture}"]
        return []

    def build_features(self, ctx: RunContext) -> Any:
        return None

    def generate_signals(self, features: Any) -> float:
        return 0.0

    def run_backtest(self, ctx: RunContext) -> Any:
        repo = ctx.repo_root
        cmd = [
            sys.executable,
            "-m",
            "options_lane.pipeline",
            "fixture-backtest",
            "--fixture",
            "fair_futures_quotes.ndjson",
            "--group",
            "example_same_ul",
        ]
        proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout)
        data = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        net = float(data.get("net_pnl", 0.0))
        num = int(data.get("num_arbs", 0))
        return type("OptionsResult", (), {"net_pnl": net, "num_trades": num, "expectancy": net / max(num, 1)})()

    def produce_diagnostics(self, ctx: RunContext, result: Any) -> Diagnostics:
        return Diagnostics(
            self.model_id,
            metrics={"net_pnl": result.net_pnl, "num_trades": result.num_trades},
        )
