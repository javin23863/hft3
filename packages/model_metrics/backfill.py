"""Backfill institutional metrics from existing run artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from model_metrics.config import load_metrics_config
from model_metrics.envelope import generate_behavior_envelope
from model_metrics.persistence import write_metric_bundle
from model_metrics.registry import calculate_metric_values
from model_metrics.scorecard import generate_model_scorecard


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_any(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _validation_reports(run_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted((run_dir / "validation_reports").glob("*.json")):
        payload = _read_json(path)
        if payload:
            payload["_artifact_id"] = str(path)
            out.append(payload)
    return out


def _trades_from_validation(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for report in reports:
        result = report.get("result") or {}
        trade_pnls = result.get("trade_pnls") or []
        if isinstance(trade_pnls, list) and trade_pnls:
            for pnl in trade_pnls:
                trades.append(
                    {
                        "realized_pnl": pnl,
                        "gross_pnl": pnl,
                        "fill_status": "FILLED",
                        "slippage_bps": result.get("slippage_bps"),
                        "spread_bps": result.get("spread_bps"),
                    }
                )
        elif result:
            trades.append(
                {
                    "realized_pnl": result.get("net_pnl"),
                    "gross_pnl": result.get("gross_pnl", result.get("net_pnl")),
                    "fill_status": "FILLED" if not result.get("error") else "REJECTED",
                    "slippage_bps": result.get("slippage_bps"),
                    "spread_bps": result.get("spread_bps"),
                }
            )
    return trades


def _trades_from_campaign_periods(summary: dict[str, Any]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for period in summary.get("periods") or []:
        if not isinstance(period, dict):
            continue
        before_period = len(trades)
        events = period.get("event_results") or []
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict):
                continue
            trade_pnls = event.get("trade_pnls")
            if isinstance(trade_pnls, list) and trade_pnls:
                for pnl in trade_pnls:
                    trades.append(
                        {
                            "realized_pnl": pnl,
                            "gross_pnl": pnl,
                            "fill_status": "FILLED",
                            "slippage_bps": event.get("slippage_bps"),
                            "spread_bps": event.get("spread_bps"),
                            "holding_period_min": event.get("holding_period_min"),
                        }
                    )
            elif event.get("net_pnl") is not None:
                trades.append(
                    {
                        "realized_pnl": event.get("net_pnl"),
                        "gross_pnl": event.get("gross_pnl", event.get("net_pnl")),
                        "fill_status": "FILLED" if not event.get("error") else "REJECTED",
                        "slippage_bps": event.get("slippage_bps"),
                        "spread_bps": event.get("spread_bps"),
                        "holding_period_min": event.get("holding_period_min"),
                    }
                )
        if len(trades) == before_period and period.get("net_pnl") is not None:
            trades.append(
                {
                    "realized_pnl": period.get("net_pnl"),
                    "gross_pnl": period.get("net_pnl"),
                    "fill_status": "FILLED" if period.get("gate_pass") is not False else "REJECTED",
                }
            )
    return trades


def _folds_from_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    folds = []
    for row in status.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        folds.append(
            {
                "return": row.get("proxy_net_pnl_bps"),
                "sharpe": row.get("deflated_sharpe_cdf"),
                "max_drawdown": row.get("proxy_max_drawdown_bps"),
            }
        )
    return folds


def _folds_from_campaign_periods(summary: dict[str, Any]) -> list[dict[str, Any]]:
    folds: list[dict[str, Any]] = []
    for period in summary.get("periods") or []:
        if not isinstance(period, dict):
            continue
        folds.append(
            {
                "return": period.get("net_pnl"),
                "sharpe": period.get("sharpe"),
                "max_drawdown": period.get("max_drawdown"),
            }
        )
    return folds


def _best_candidate(candidate_rankings: Any) -> dict[str, Any]:
    if isinstance(candidate_rankings, list):
        for row in candidate_rankings:
            if isinstance(row, dict):
                return row
    return {}


def _first_spec(experiment_spec: Any) -> dict[str, Any]:
    if isinstance(experiment_spec, list):
        for row in experiment_spec:
            if isinstance(row, dict):
                return row
    return experiment_spec if isinstance(experiment_spec, dict) else {}


def _normalize_asset_class(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    aliases = {
        "CRYPTO_AUTONOMOUS_SMOKE": "CRYPTO",
        "CRYPTO_LANE": "CRYPTO",
        "DIGITAL_ASSET": "CRYPTO",
        "DIGITAL_ASSETS": "CRYPTO",
        "EQUITY": "EQUITIES",
        "STOCK": "EQUITIES",
        "STOCKS": "EQUITIES",
        "CME_FUTURES": "FUTURES",
        "RATES": "FUTURES",
        "RATE_FUTURES": "FUTURES",
        "INDEX_FUTURES": "FUTURES",
        "OPTION": "OPTIONS",
        "OPTIONS_LANE": "OPTIONS",
        "PREDICTION_MARKET": "PREDICTION_MARKETS",
        "PREDICTION": "PREDICTION_MARKETS",
    }
    return aliases.get(raw, raw)


def _infer_asset_class(*, explicit: Any = "", symbol: Any = "", first_spec: dict[str, Any] | None = None) -> str:
    spec = first_spec or {}
    binding = spec.get("model_binding") if isinstance(spec.get("model_binding"), dict) else {}
    for value in (
        explicit,
        spec.get("asset_class"),
        spec.get("lane"),
        binding.get("asset_class"),
        binding.get("lane"),
        binding.get("campaign_mode"),
    ):
        normalized = _normalize_asset_class(value)
        if normalized:
            return normalized
    sym = str(symbol or "").upper().replace(".V.0", "").replace(".C.0", "")
    if not sym:
        return ""
    if "/" in sym or sym.endswith(("USDT", "USD", "PERP")) and any(token in sym for token in ("BTC", "ETH", "SOL")):
        return "CRYPTO"
    futures_roots = {
        "ES",
        "MES",
        "NQ",
        "MNQ",
        "RTY",
        "M2K",
        "YM",
        "MYM",
        "ZT",
        "ZF",
        "ZN",
        "ZB",
        "UB",
        "SR3",
        "ZQ",
        "CL",
        "GC",
        "SI",
        "HG",
        "6E",
        "6J",
        "6B",
        "VX",
    }
    root = sym.split(".")[0]
    if root in futures_roots:
        return "FUTURES"
    if sym.endswith(("_CALL", "_PUT")):
        return "OPTIONS"
    if sym.isalpha() and 1 <= len(sym) <= 5:
        return "EQUITIES"
    return ""


def run_inputs_from_run_dir(run_dir: Path) -> dict[str, Any]:
    status = _read_json(run_dir / "status.json") or _read_json(run_dir / "summary.json")
    campaign_meta = _read_json(run_dir / "campaign.json")
    scoring = _read_json(run_dir / "scoring_summary.json")
    backtest_metrics = _read_json(run_dir / "backtest_metrics.json")
    candidate_rankings = _read_json_any(run_dir / "candidate_rankings.json")
    best_candidate = _best_candidate(candidate_rankings)
    best_summary = best_candidate.get("summary") if isinstance(best_candidate.get("summary"), dict) else {}
    config_snapshot = _read_yaml(run_dir / "config_snapshot.yaml")
    experiment_spec = _read_json_any(run_dir / "experiment_spec.json")
    first_spec = _first_spec(experiment_spec)
    if not status and scoring:
        status = scoring
    robustness = _read_json(run_dir / "robustness_summary.json")
    robustness_gates = _read_json(run_dir / "robustness_gates.json")
    walk_forward_results = _read_json(run_dir / "walk_forward_results.json")
    walk_forward_correlation = _read_json(run_dir / "walk_forward_correlation.json")
    vectorbt = _read_json(run_dir / "vectorbt_summary.json")
    config = _read_json(run_dir / "manifest.json")
    validation = _validation_reports(run_dir)
    raw_decision = status.get("decision") or scoring.get("decision") or {}
    decision = raw_decision if isinstance(raw_decision, dict) else {}
    evidence_candidate = (
        best_candidate.get("model_id")
        or best_candidate.get("candidate_id")
        or best_candidate.get("alpha_id")
        or (scoring.get("selected_candidate") or {}).get("model_id")
        or (scoring.get("selected_candidate") or {}).get("candidate_id")
        or decision.get("evidence_candidate_id")
        or decision.get("top_smoke_candidate")
        or status.get("model_id")
        or campaign_meta.get("model_id")
        or first_spec.get("alpha_id")
        or run_dir.name
    )
    trades = _trades_from_validation(validation)
    if not trades:
        trades = _trades_from_campaign_periods(status)
    if not trades and best_summary:
        trades = _trades_from_campaign_periods(best_summary)
    returns = [trade["realized_pnl"] for trade in trades if trade.get("realized_pnl") is not None]
    result_rows = [report.get("result") or {} for report in validation]
    result_rows.extend(
        event
        for period in status.get("periods") or []
        if isinstance(period, dict)
        for event in (period.get("event_results") or [])
        if isinstance(event, dict)
    )
    execution = {
        "fill_rate": _first(result_rows, "fill_rate"),
        "slippage_bps": _first(result_rows, "slippage_bps"),
        "adverse_selection_rate": _first(result_rows, "adverse_selection_cost"),
        "latency_order_to_ack": decision.get("latency_order_to_ack_ms") or status.get("latency_order_to_ack_ms"),
        "alpha_half_life": decision.get("alpha_half_life_ms") or status.get("alpha_half_life_ms"),
    }
    robustness_pack = robustness.get("robustness_pack") or {}
    if not robustness_pack and status.get("robustness_checks"):
        checks = status.get("robustness_checks") or []
        if isinstance(checks, dict):
            checks = checks.get("checks") or checks.get("items") or []
        if isinstance(checks, list):
            robustness_pack = {
                "passed": [row.get("name") for row in checks if isinstance(row, dict) and str(row.get("status")).upper() == "PASS"],
                "failed": [row.get("name") for row in checks if isinstance(row, dict) and str(row.get("status")).upper() == "FAIL"],
            }
    folds = _folds_from_status(status)
    if not folds:
        folds = _folds_from_campaign_periods(status)
    if not folds and best_summary:
        folds = _folds_from_campaign_periods(best_summary)
    robustness_input = {
        "folds": folds,
        "walk_forward_efficiency": robustness.get("walk_forward_efficiency") or walk_forward_results.get("walk_forward_efficiency"),
        "deflated_sharpe_ratio": robustness.get("deflated_sharpe_ratio"),
        "PBO": robustness.get("PBO"),
        "cost_sensitivity_score": _pass_score({"robustness_pack": robustness_pack}, "transaction_cost_sensitivity"),
        "slippage_sensitivity_score": _pass_score({"robustness_pack": robustness_pack}, "slippage_sensitivity"),
        "capacity_sensitivity_score": _pass_score({"robustness_pack": robustness_pack}, "capacity_sensitivity"),
    }
    data_cfg = config_snapshot.get("data") or {}
    symbol = str(config.get("symbol", status.get("symbol", campaign_meta.get("symbol", first_spec.get("symbol", "")))))
    asset_class = _infer_asset_class(
        explicit=(
            status.get("asset_class")
            or status.get("scenario")
            or config.get("asset_class")
            or data_cfg.get("asset_class")
            or campaign_meta.get("asset_class")
            or best_candidate.get("asset_class")
        ),
        symbol=symbol,
        first_spec=first_spec,
    )
    return {
        "context": {
            "model_id": str(evidence_candidate),
            "model_version": str(status.get("model_version", campaign_meta.get("param_hash", ""))),
            "run_id": str(status.get("run_id", run_dir.name)),
            "campaign_id": str(status.get("campaign_id", campaign_meta.get("campaign_id", scoring.get("campaign_id", "")))),
            "asset_class": asset_class,
            "symbol": symbol,
            "timeframe": str(config.get("timeframe", status.get("timeframe", ""))),
            "regime_id": str(config.get("regime_id", status.get("regime_id", ""))),
            "robustness_run_id": str(robustness.get("run_id", status.get("run_id", run_dir.name))),
        },
        "created_at": status.get("finished_at") or status.get("updated_at") or "1970-01-01T00:00:00+00:00",
        "source_artifact_ids": [str(run_dir / name) for name in (
            "status.json",
            "summary.json",
            "campaign.json",
            "robustness_summary.json",
            "backtest_metrics.json",
            "candidate_rankings.json",
            "scoring_summary.json",
            "robustness_gates.json",
            "walk_forward_results.json",
            "walk_forward_correlation.json",
        ) if (run_dir / name).is_file()]
        + [str(report.get("_artifact_id")) for report in validation if report.get("_artifact_id")],
        "returns": returns,
        "trades": trades,
        "execution": execution,
        "robustness": robustness_input,
        "portfolio": status.get("portfolio_metrics") or {},
        "prediction": status.get("prediction_metrics") or {},
        "expected_feature_ranges": status.get("expected_feature_ranges") or {},
        "approved_regime_ids": status.get("approved_regime_ids") or [],
        "blocked_regime_ids": status.get("blocked_regime_ids") or [],
        "raw": {
            "status": status,
            "campaign": campaign_meta,
            "scoring_summary": scoring,
            "backtest_metrics": backtest_metrics,
            "candidate_rankings": candidate_rankings,
            "robustness_summary": robustness,
            "robustness_gates": robustness_gates,
            "walk_forward_results": walk_forward_results,
            "walk_forward_correlation": walk_forward_correlation,
            "vectorbt_summary": vectorbt,
        },
    }


def _first(rows: list[dict[str, Any]], name: str) -> Any:
    for row in rows:
        if row.get(name) is not None:
            return row.get(name)
    return None


def _pass_score(robustness: dict[str, Any], name: str) -> float | None:
    pack = robustness.get("robustness_pack") or {}
    failed = set(pack.get("failed") or [])
    passed = set(pack.get("passed") or [])
    if name in failed:
        return 0.0
    if name in passed:
        return 1.0
    return None


def generate_bundle_for_run_dir(
    run_dir: Path | str,
    *,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Generate scorecard/envelope artifacts from one model run directory."""

    repo = Path(root) if root is not None else Path.cwd()
    run = Path(run_dir)
    inputs = run_inputs_from_run_dir(run)
    config = load_metrics_config(repo)
    metrics = calculate_metric_values(inputs)
    scorecard = generate_model_scorecard(inputs, config=config, metrics=metrics)
    envelope = generate_behavior_envelope(inputs, scorecard, config=config)
    out = Path(output_dir) if output_dir is not None else run / "model_metrics"
    paths = write_metric_bundle(
        out,
        scorecard,
        envelope,
        logs=[{"status": "ok", "run_dir": str(run), "metric_count": len(metrics)}],
        force=force,
    )
    return {
        "status": "ok",
        "run_dir": str(run),
        "output_dir": str(out),
        "scorecard": scorecard.to_dict(),
        "envelope": envelope.to_dict(),
        "paths": paths,
        "warnings": list(scorecard.warnings) + list(envelope.warnings),
    }


def discover_run_dirs(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in (
        "runtime/workbench/crypto_smoke/*",
        "runtime/workbench/runs/*",
        "runtime/validation/*",
        "artifacts/runs/*",
    ):
        for path in root.glob(pattern):
            if path.is_dir() and any(
                (path / name).is_file()
                for name in (
                    "status.json",
                    "summary.json",
                    "robustness_summary.json",
                    "scoring_summary.json",
                    "candidate_rankings.json",
                    "backtest_metrics.json",
                )
            ):
                candidates.append(path)
    return sorted(set(candidates))


def backfill_model_metrics(root: Path | str, *, force: bool = False) -> dict[str, Any]:
    repo = Path(root)
    processed = []
    skipped = []
    failed = []
    for run_dir in discover_run_dirs(repo):
        out_dir = run_dir / "model_metrics"
        if out_dir.exists() and not force:
            skipped.append({"run_dir": str(run_dir), "reason": "model_metrics artifacts already exist"})
            continue
        try:
            processed.append(generate_bundle_for_run_dir(run_dir, root=repo, force=True))
        except Exception as exc:
            failed.append({"run_dir": str(run_dir), "reason": str(exc)})
    top = sorted(
        (
            {
                "run_dir": item["run_dir"],
                "model_id": item["scorecard"]["model_id"],
                "grade": item["scorecard"]["grade"],
                "weighted_score": item["scorecard"]["weighted_score"],
            }
            for item in processed
        ),
        key=lambda row: row["weighted_score"],
        reverse=True,
    )
    return {
        "models_processed": len(processed),
        "models_skipped": len(skipped),
        "failed_calculations": len(failed),
        "metrics_calculated": sum(len(item["scorecard"]["metrics"]) for item in processed),
        "missing_data_warnings": [warning for item in processed for warning in item.get("warnings", [])],
        "top_models_by_grade": top[:20],
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill institutional model metrics from run artifacts.")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--force", action="store_true", help="Overwrite existing metric artifacts")
    args = parser.parse_args(argv)
    summary = backfill_model_metrics(Path(args.root), force=args.force)
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 1 if summary["failed_calculations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
