"""Full parameter-matrix IS/OOS backtest runner for WFC."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from decision_engine.python.src.walk_forward import ValidationPeriod

from workbench.src.core.composition import ModelComposition
from workbench.src.core.params import canonical_params_json
from workbench.src.data.event_catalog import list_campaign_events
from workbench.src.optimization.param_matrix import generate_param_grid
from workbench.src.registry.unified_registry import get_model_config
from workbench.src.robustness.wfc.config import load_wfc_config
from workbench.src.robustness.wfc.metrics import aggregate_event_metrics

logger = logging.getLogger(__name__)


class MatrixFoldDataError(RuntimeError):
    """Raised when required fold NPZ data is missing."""


def _git_sha(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _npz_hash(events: List[Any]) -> str:
    parts = sorted(str(getattr(e, "event_id", e)) for e in events)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _events_for_years(
    model_id: str,
    symbol: str,
    repo_root: Path,
    start_year: int,
    end_year: int,
) -> List[Any]:
    period = ValidationPeriod("fold", int(start_year), int(end_year))
    return list(list_campaign_events(model_id, period, symbol, repo_root))


def _run_events(
    engine,
    model_id: str,
    symbol: str,
    events: List[Any],
    *,
    params: Dict[str, Any],
    composition: Optional[ModelComposition],
    chi404_summary: Optional[Path],
    seed: int,
    years_avail: float,
    audit_grade: bool,
) -> List[Dict[str, Any]]:
    outcomes: List[Dict[str, Any]] = []
    for ev in events:
        if not ev.npz_present:
            raise MatrixFoldDataError(f"Missing NPZ for event {ev.event_id}")
        out = engine.run(
            model_id,
            ev.event_id,
            chi404_summary=chi404_summary,
            seed=seed,
            history_years_available=years_avail,
            skip_history_gate=not audit_grade,
            fast_sweep=not audit_grade,
            composition=composition,
            strategy_params=params,
        )
        rep = out.get("report", {})
        latency_compact = rep.get("latency_operating_envelope", {})
        pnl = float(rep.get("net_pnl", 0.0))
        adj = float(rep.get("simulated_latency_adjusted_pnl", pnl))
        ntr = int(rep.get("num_trades", 0))
        outcomes.append(
            {
                "event_id": ev.event_id,
                "release_date": ev.release_date,
                "net_pnl": pnl,
                "net_return_adjusted": adj,
                "num_trades": ntr,
                "expectancy": pnl / ntr if ntr else 0.0,
                **latency_compact,
            }
        )
    return outcomes


def run_full_matrix_oos(
    repo_root: Path,
    *,
    model_id: str,
    symbol: str,
    campaign_id: str,
    composition: Optional[ModelComposition] = None,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    audit_grade: bool = True,
    years_avail: float = 0.0,
    wfc_cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    from workbench.src.run.engine import WorkbenchEngine

    cfg = wfc_cfg or load_wfc_config(repo_root)
    notional = float(cfg.get("notional_capital", 100_000.0))
    min_combos = int(cfg.get("min_parameter_combinations", 100))
    combos = generate_param_grid(model_id, min_combinations=min_combos)
    if not combos:
        return []

    model_cfg = get_model_config(model_id)
    engine = WorkbenchEngine(repo_root)
    rows: List[Dict[str, Any]] = []
    git_sha = _git_sha(repo_root)

    active_folds = [f for f in (cfg.get("folds") or []) if not f.get("evaluate_only")]
    if not active_folds:
        raise MatrixFoldDataError("No active WFC folds configured")

    for fold in active_folds:
        is_events = _events_for_years(
            model_id, symbol, repo_root, fold["is_start_year"], fold["is_end_year"]
        )
        oos_events = _events_for_years(
            model_id, symbol, repo_root, fold["oos_start_year"], fold["oos_end_year"]
        )
        is_runnable = [e for e in is_events if e.npz_present]
        oos_runnable = [e for e in oos_events if e.npz_present]
        fold_id = fold.get("id", fold.get("name", "fold"))
        if len(is_runnable) < len(is_events):
            missing = [e.event_id for e in is_events if not e.npz_present]
            raise MatrixFoldDataError(
                f"Missing IS NPZ for fold {fold_id}: {', '.join(missing[:5])}"
            )
        if len(oos_runnable) < len(oos_events):
            missing = [e.event_id for e in oos_events if not e.npz_present]
            raise MatrixFoldDataError(
                f"Missing OOS NPZ for fold {fold_id}: {', '.join(missing[:5])}"
            )
        if not is_runnable:
            raise MatrixFoldDataError(
                f"Missing IS NPZ for fold {fold_id} ({fold['is_start_year']}-{fold['is_end_year']})"
            )
        if not oos_runnable:
            raise MatrixFoldDataError(
                f"Missing OOS NPZ for fold {fold_id} ({fold['oos_start_year']}-{fold['oos_end_year']})"
            )

        is_years = max(1, fold["is_end_year"] - fold["is_start_year"] + 1)
        oos_years = max(1, fold["oos_end_year"] - fold["oos_start_year"] + 1)
        regime_label = str(fold["regime_label"]) if fold.get("regime_label") else str(fold_id)
        npz_hash = _npz_hash(is_runnable + oos_runnable)

        for combo in combos:
            params = combo["params"]
            is_out = _run_events(
                engine,
                model_id,
                symbol,
                is_runnable,
                params=params,
                composition=composition,
                chi404_summary=chi404_summary,
                seed=seed,
                years_avail=years_avail,
                audit_grade=audit_grade,
            )
            oos_out = _run_events(
                engine,
                model_id,
                symbol,
                oos_runnable,
                params=params,
                composition=composition,
                chi404_summary=chi404_summary,
                seed=seed,
                years_avail=years_avail,
                audit_grade=audit_grade,
            )
            is_metrics = aggregate_event_metrics(is_out, years=is_years, notional_capital=notional)
            oos_metrics = aggregate_event_metrics(oos_out, years=oos_years, notional_capital=notional)
            oos_latency = _aggregate_latency_fields(oos_out)
            rows.append(
                {
                    "run_id": campaign_id,
                    "model_id": model_id,
                    "strategy_id": model_id,
                    "parameter_hash": combo["parameter_hash"],
                    "params": params,
                    "params_json": canonical_params_json(params),
                    "asset": symbol,
                    "timeframe": "event_replay",
                    "regime_label": regime_label,
                    "fold_id": fold_id,
                    "fold_name": fold.get("name", ""),
                    "git_sha": git_sha,
                    "npz_hash": npz_hash,
                    "in_sample_start": f"{fold['is_start_year']}-01-01",
                    "in_sample_end": f"{fold['is_end_year']}-12-31",
                    "out_sample_start": f"{fold['oos_start_year']}-01-01",
                    "out_sample_end": f"{fold['oos_end_year']}-12-31",
                    "is_metrics": is_metrics,
                    "oos_metrics": oos_metrics,
                    "is_sharpe": is_metrics["sharpe"],
                    "oos_sharpe": oos_metrics["sharpe"],
                    "is_profit_factor": is_metrics["profit_factor"],
                    "oos_profit_factor": oos_metrics["profit_factor"],
                    "is_cagr": is_metrics["cagr"],
                    "oos_cagr": oos_metrics["cagr"],
                    "is_max_drawdown": is_metrics["max_drawdown"],
                    "oos_max_drawdown": oos_metrics["max_drawdown"],
                    "is_trade_count": is_metrics["trade_count"],
                    "oos_trade_count": oos_metrics["trade_count"],
                    "latency_operating_envelope_status": oos_latency["status"],
                    "placement_speed_p99_us": oos_latency["placement_speed_p99_us"],
                    "placement_speed_p99_9_us": oos_latency["placement_speed_p99_9_us"],
                    "send_to_ack_p99_us": oos_latency["send_to_ack_p99_us"],
                    "turnover": oos_metrics["turnover"],
                    "slippage_assumptions": model_cfg.execution_assumptions,
                    "transaction_cost_assumptions": model_cfg.execution_assumptions,
                    "capacity_assumptions": "",
                }
            )
    return rows


def _aggregate_latency_fields(outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    statuses = [str(row.get("latency_operating_envelope_status") or "").upper() for row in outcomes]
    p99 = [float(row["placement_speed_p99_us"]) for row in outcomes if isinstance(row.get("placement_speed_p99_us"), (int, float))]
    p99_9 = [float(row["placement_speed_p99_9_us"]) for row in outcomes if isinstance(row.get("placement_speed_p99_9_us"), (int, float))]
    ack = [float(row["send_to_ack_p99_us"]) for row in outcomes if isinstance(row.get("send_to_ack_p99_us"), (int, float))]
    return {
        "status": "PASS" if statuses and all(status == "PASS" for status in statuses) else "FAIL",
        "placement_speed_p99_us": max(p99) if p99 else None,
        "placement_speed_p99_9_us": max(p99_9) if p99_9 else None,
        "send_to_ack_p99_us": max(ack) if ack else None,
    }


def save_matrix_rows(rows: List[Dict[str, Any]], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    flat = []
    for r in rows:
        row = dict(r)
        row["is_metrics"] = json.dumps(r.get("is_metrics", {}))
        row["oos_metrics"] = json.dumps(r.get("oos_metrics", {}))
        row["params"] = json.dumps(r.get("params", {}))
        flat.append(row)
    try:
        import pandas as pd

        path = out_dir / "param_matrix.parquet"
        pd.DataFrame(flat).to_parquet(path, index=False)
        return path
    except Exception as exc:
        import csv

        logger.warning("parquet write failed (%s); falling back to CSV", exc)
        path = out_dir / "param_matrix.csv"
        if flat:
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(flat[0].keys()))
                writer.writeheader()
                writer.writerows(flat)
        else:
            path.write_text("", encoding="utf-8")
        return path


def load_matrix_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    if path.suffix == ".parquet":
        import pandas as pd

        df = pd.read_parquet(path)
        rows = df.to_dict(orient="records")
    else:
        import csv

        with path.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    for r in rows:
        for key in ("is_metrics", "oos_metrics", "params"):
            if key in r and isinstance(r[key], str):
                r[key] = json.loads(r[key])
    return rows
