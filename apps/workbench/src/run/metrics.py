"""Trader-grade metrics computation.

Reads a campaign's per-event artifacts and computes the full trader
metrics block required for honest result display. Every metric that
cannot be computed because the underlying backtester did not emit the
required ledger/equity-curve is marked MISSING_REQUIRED_LEDGER (and the
specific missing artifact is named) — never fabricated.

Inputs (per campaign):
  - summary.json               (campaign_runner output; periods[] + per-event net_pnl/num_trades)
  - periods/<P>/events/<E>/diagnostics.json (per-event detail)
  - periods/<P>/events/<E>/trades.parquet   (if engine wrote one)

Outputs:
  - metrics.json               (single block, schema_version=1)
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class MetricsResult:
    schema_version: int = 1
    source: str = ""
    notes: List[str] = field(default_factory=list)
    missing_required: List[Dict[str, str]] = field(default_factory=list)

    # Trader metrics
    total_pnl: float = 0.0
    net_pnl_after_fees: Optional[float] = None
    num_trades: int = 0
    win_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy_per_trade: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    drawdown_duration: Optional[int] = None
    recovery_time: Optional[int] = None
    calmar_ratio: Optional[float] = None
    exposure_time_sec: Optional[float] = None
    turnover: Optional[float] = None
    avg_holding_time_sec: Optional[float] = None
    fees_total: Optional[float] = None
    slippage_estimate: Optional[float] = None
    route_distribution: Dict[str, int] = field(default_factory=dict)
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    data_coverage_pct: Optional[float] = None
    blocked_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Aggregation from summary.json
# ---------------------------------------------------------------------------


def _event_outcomes(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect event outcomes from either a per-campaign or autonomous summary.

    Per-campaign summaries (from campaign_runner.run_campaign) carry a
    "periods" list, each with "event_results" inside.

    Autonomous summaries (from all_lanes.run_all_lanes) carry a
    "job_outcomes" list at the top level. Each entry has model_id, symbol,
    status, and (in future revisions) per-event detail.
    """
    out: List[Dict[str, Any]] = []
    for p in summary.get("periods", []):
        for ev in p.get("event_results", []):
            out.append({**ev, "period": p.get("name")})
    # Autonomous layout
    for j in summary.get("job_outcomes", []):
        if isinstance(j, dict) and j.get("net_pnl") is not None:
            out.append(
                {
                    "event_id": j.get("job_id", ""),
                    "release_date": "",
                    "net_pnl": j.get("net_pnl", 0.0),
                    "num_trades": j.get("num_trades", 0),
                    "expectancy": j.get("expectancy", 0.0),
                    "survives_cpp_execution_delay": True,
                    "trades_vetoed_by_defense": 0,
                    "run_id": j.get("artifact_dir", ""),
                }
            )
    return out


def _fees_slippage(event_dirs: List[Path]) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Dict[str, int]]:
    """Return (fees_total, slippage_estimate, p50_ms, p95_ms, p99_ms, exposure_sec, turnover, avg_holding_sec, route_dist)."""
    fees = None
    slippage = None
    p50 = p95 = p99 = None
    exposure = None
    turnover = None
    avg_holding = None
    route_dist: Dict[str, int] = {}
    for ev_dir in event_dirs:
        diag_path = ev_dir / "diagnostics.json"
        if not diag_path.is_file():
            continue
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rep = diag.get("report", {})
        for k, dst in (("fees_total", "fees"), ("slippage_estimate", "slippage")):
            v = rep.get(k)
            if v is not None and dst == "fees":
                fees = (fees or 0.0) + float(v)
            elif v is not None and dst == "slippage":
                slippage = (slippage or 0.0) + float(v)
        for venue, n in (rep.get("route_distribution") or {}).items():
            route_dist[str(venue)] = route_dist.get(str(venue), 0) + int(n)
        lat = rep.get("latency_ms") or {}
        for k, dst in (("p50", "p50"), ("p95", "p95"), ("p99", "p99")):
            v = lat.get(k)
            if v is not None:
                if dst == "p50":
                    p50 = v if p50 is None else (p50 + v) / 2
                elif dst == "p95":
                    p95 = v if p95 is None else (p95 + v) / 2
                else:
                    p99 = v if p99 is None else (p99 + v) / 2
        if rep.get("exposure_time_sec") is not None:
            exposure = (exposure or 0.0) + float(rep["exposure_time_sec"])
        if rep.get("turnover") is not None:
            turnover = (turnover or 0.0) + float(rep["turnover"])
        if rep.get("avg_holding_time_sec") is not None:
            avg_holding = rep["avg_holding_time_sec"] if avg_holding is None else (avg_holding + rep["avg_holding_time_sec"]) / 2
    return fees, slippage, p50, p95, p99, exposure, turnover, avg_holding, route_dist


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_metrics(artifact_dir: Path) -> MetricsResult:
    artifact_dir = Path(artifact_dir)
    summary_path = artifact_dir / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"summary.json not found in {artifact_dir}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    res = MetricsResult(source=str(artifact_dir))

    events = _event_outcomes(summary)
    res.notes.append(f"aggregated from {len(events)} event outcomes across {len(summary.get('periods', []))} periods")

    # ---- pnl / trade counts from summary (no ledger required) ----
    pnl_list: List[float] = []
    num_trades_total = 0
    for ev in events:
        pnl = float(ev.get("net_pnl", 0.0))
        pnl_list.append(pnl)
        res.total_pnl += pnl
        num_trades_total += int(ev.get("num_trades", 0))
    res.num_trades = num_trades_total
    res.expectancy_per_trade = (res.total_pnl / res.num_trades) if res.num_trades else 0.0

    # ---- locate event_dirs ----
    # Two layouts supported:
    #  (a) Single-campaign layout: <artifact_dir>/periods/<P>/events/<E>/
    #  (b) Autonomous layout: <artifact_dir>/<per-job-subdir>/periods/<P>/events/<E>/
    #      where per-job-subdir is a sibling of summary.json.
    periods_root = artifact_dir / "periods"
    event_dirs: List[Path] = []
    seen: set[str] = set()
    if periods_root.is_dir():
        for period_dir in periods_root.iterdir():
            events_root = period_dir / "events"
            if events_root.is_dir():
                for p in events_root.iterdir():
                    if p.is_dir() and str(p) not in seen:
                        seen.add(str(p))
                        event_dirs.append(p)
    # Autonomous: walk per-job subdirs
    for sub in artifact_dir.iterdir():
        if not sub.is_dir() or sub.name in {"periods", "errors.jsonl", "planned_jobs.json",
                                              "summary.json", "status.json", "control.json",
                                              "campaign.json", "coverage_report.json",
                                              "pit_report.json", "evidence_snapshot.json",
                                              "metrics.json", "backend.log"}:
            continue
        sub_periods = sub / "periods"
        if sub_periods.is_dir():
            for period_dir in sub_periods.iterdir():
                events_root = period_dir / "events"
                if events_root.is_dir():
                    for p in events_root.iterdir():
                        if p.is_dir() and str(p) not in seen:
                            seen.add(str(p))
                            event_dirs.append(p)

    has_per_trade_ledger = False
    per_trade_pnls: List[float] = []
    for ev_dir in event_dirs:
        trades = ev_dir / "trades.parquet"
        if trades.is_file():
            try:
                import pandas as pd  # type: ignore

                df = pd.read_parquet(trades)
                if "pnl" in df.columns and len(df):
                    per_trade_pnls.extend(float(x) for x in df["pnl"].tolist())
                    has_per_trade_ledger = True
            except Exception:
                # ledger unreadable for any reason -> we cannot compute the metrics
                # that require it. Mark them missing below.
                pass

    if has_per_trade_ledger and per_trade_pnls:
        wins = [p for p in per_trade_pnls if p > 0]
        losses = [p for p in per_trade_pnls if p < 0]
        res.win_rate = (len(wins) / len(per_trade_pnls)) if per_trade_pnls else None
        res.avg_win = (sum(wins) / len(wins)) if wins else None
        res.avg_loss = (sum(losses) / len(losses)) if losses else None
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        res.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

        # Equity curve, drawdown, Sharpe, Sortino, Calmar
        equity = np.cumsum(per_trade_pnls)
        if len(equity) > 1:
            rets = np.diff(equity)
            if rets.std() > 0:
                res.sharpe_ratio = float(rets.mean() / rets.std() * math.sqrt(252))
            downside = rets[rets < 0]
            if downside.size and downside.std() > 0:
                res.sortino_ratio = float(rets.mean() / downside.std() * math.sqrt(252))
            running_max = np.maximum.accumulate(equity)
            drawdown = equity - running_max
            res.max_drawdown = float(drawdown.min())
            # drawdown duration: longest stretch where drawdown < 0
            in_dd = drawdown < 0
            max_dd_len = 0
            cur_len = 0
            for v in in_dd:
                if v:
                    cur_len += 1
                    max_dd_len = max(max_dd_len, cur_len)
                else:
                    cur_len = 0
            res.drawdown_duration = int(max_dd_len)
            # recovery time: trades from trough back to prior peak
            trough_idx = int(np.argmin(equity))
            peak_before = float(running_max[trough_idx])
            recovered_at = None
            for j in range(trough_idx + 1, len(equity)):
                if equity[j] >= peak_before:
                    recovered_at = j - trough_idx
                    break
            res.recovery_time = int(recovered_at) if recovered_at is not None else None
            # Calmar = annual_return / max_drawdown
            if res.max_drawdown < 0:
                total_return = float(equity[-1] - equity[0])
                n = len(equity)
                annualized = total_return * (252 / max(n, 1))
                res.calmar_ratio = annualized / abs(res.max_drawdown) if res.max_drawdown else None
    else:
        # Honest: no per-trade ledger found -> we cannot compute these.
        for name in (
            "win_rate",
            "avg_win",
            "avg_loss",
            "profit_factor",
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown",
            "drawdown_duration",
            "recovery_time",
            "calmar_ratio",
        ):
            res.missing_required.append(
                {
                    "metric": name,
                    "required_input": "per-event trades.parquet with column 'pnl'",
                    "status": "MISSING_REQUIRED_LEDGER",
                }
            )

    # ---- fees / slippage / latency / exposure / route from per-event diagnostics ----
    fees, slippage, p50, p95, p99, exposure, turnover, avg_holding, route_dist = _fees_slippage(event_dirs)
    if fees is not None:
        res.fees_total = fees
    else:
        res.missing_required.append(
            {
                "metric": "fees_total",
                "required_input": "per-event diagnostics.report.fees_total",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    if slippage is not None:
        res.slippage_estimate = slippage
    else:
        res.missing_required.append(
            {
                "metric": "slippage_estimate",
                "required_input": "per-event diagnostics.report.slippage_estimate",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    if p50 is not None:
        res.latency_p50_ms = p50
    else:
        res.missing_required.append(
            {
                "metric": "latency_p50_ms",
                "required_input": "per-event diagnostics.report.latency_ms.p50",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    if p95 is not None:
        res.latency_p95_ms = p95
    else:
        res.missing_required.append(
            {
                "metric": "latency_p95_ms",
                "required_input": "per-event diagnostics.report.latency_ms.p95",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    if p99 is not None:
        res.latency_p99_ms = p99
    else:
        res.missing_required.append(
            {
                "metric": "latency_p99_ms",
                "required_input": "per-event diagnostics.report.latency_ms.p99",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    if exposure is not None:
        res.exposure_time_sec = exposure
    else:
        res.missing_required.append(
            {
                "metric": "exposure_time_sec",
                "required_input": "per-event diagnostics.report.exposure_time_sec",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    if turnover is not None:
        res.turnover = turnover
    else:
        res.missing_required.append(
            {
                "metric": "turnover",
                "required_input": "per-event diagnostics.report.turnover",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    if avg_holding is not None:
        res.avg_holding_time_sec = avg_holding
    else:
        res.missing_required.append(
            {
                "metric": "avg_holding_time_sec",
                "required_input": "per-event diagnostics.report.avg_holding_time_sec",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )
    res.route_distribution = route_dist

    # ---- net pnl after fees (only if we have fees) ----
    if res.fees_total is not None:
        res.net_pnl_after_fees = res.total_pnl - res.fees_total
    else:
        res.missing_required.append(
            {
                "metric": "net_pnl_after_fees",
                "required_input": "fees_total (from diagnostics.report.fees_total)",
                "status": "MISSING_REQUIRED_LEDGER",
            }
        )

    # ---- coverage / blocked / skipped counts (always derivable) ----
    events_total = len(events)
    events_present = sum(1 for e in events if e.get("net_pnl") is not None)
    res.data_coverage_pct = (events_present / events_total * 100.0) if events_total else None
    res.blocked_count = sum(1 for e in events if e.get("net_pnl", 0) == 0 and e.get("num_trades", 0) == 0)
    res.skipped_count = int(summary.get("counts", {}).get("skipped", 0))
    res.failed_count = int(summary.get("counts", {}).get("failed", 0))

    return res


def write_metrics(artifact_dir: Path, result: Optional[MetricsResult] = None) -> Path:
    artifact_dir = Path(artifact_dir)
    result = result or compute_metrics(artifact_dir)
    out = artifact_dir / "metrics.json"
    out.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return out
