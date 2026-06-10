"""Adapter: options_lane fixture backtest for PDF_MODEL_5 / DEALER_HEDGING (equities lane)."""

from __future__ import annotations

import json
import os
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
        fixture = ctx.repo_root / "packages" / "options_lane" / "fixtures" / "fair_futures_quotes.ndjson"
        if not fixture.is_file():
            return [f"options fixture missing: {fixture}"]
        return []

    def build_features(self, ctx: RunContext) -> Any:
        return None

    def generate_signals(self, features: Any) -> float:
        return 0.0

    def run_backtest(self, ctx: RunContext) -> Any:
        repo = ctx.repo_root
        # options_lane.pipeline self-inserts _PKG_ROOT (packages/) into sys.path when run as a
        # module, but only if Python can first *find* the options_lane package.  When cwd is the
        # repo root, `packages/` is NOT on sys.path by default.  We extend PYTHONPATH so that
        # `-m options_lane.pipeline` resolves correctly regardless of the caller's environment.
        env = os.environ.copy()
        packages_dir = str(repo / "packages")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{packages_dir}{os.pathsep}{existing}" if existing else packages_dir
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
        proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout)
        out = proc.stdout.strip()
        start = out.find("{")
        data = json.loads(out[start:]) if start != -1 else {}
        net = float(data.get("net_pnl", 0.0))
        num = int(data.get("num_arbs", 0))
        return type("OptionsResult", (), {"net_pnl": net, "num_trades": num, "expectancy": net / max(num, 1)})()

    def produce_diagnostics(self, ctx: RunContext, result: Any) -> Diagnostics:
        return Diagnostics(
            self.model_id,
            metrics={"net_pnl": result.net_pnl, "num_trades": result.num_trades},
        )
