"""Event-time Ghost Route backtest wrapper."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .ghost_route_metrics import summarize_event_log, write_csv, write_markdown_report
from .ghost_route_model import (
    CONTRACT_PAIRS,
    GhostRouteConfig,
    GhostRouteModel,
    MBOEvent,
    simulate_fak_order,
    sort_events,
    validate_event_window,
)


EVENT_LOG_FIELDS = (
    "signal_id",
    "timestamp_signal",
    "timestamp_order_arrival",
    "macro_contract",
    "micro_contract",
    "direction",
    "macro_bid",
    "macro_ask",
    "macro_bid_size",
    "macro_ask_size",
    "micro_bid",
    "micro_ask",
    "micro_bid_size",
    "micro_ask_size",
    "shadow_decay_side",
    "NSD",
    "CTR",
    "nOFI",
    "spread_zscore",
    "expected_edge_ticks",
    "target_price",
    "target_quantity",
    "available_depth_at_signal",
    "available_depth_at_arrival",
    "fill_status",
    "filled_quantity",
    "fill_price",
    "micro_reprice_time",
    "lead_time_us",
    "markout_10us",
    "markout_25us",
    "markout_50us",
    "markout_100us",
    "markout_250us",
    "markout_1ms",
    "net_pnl",
    "reject_reason",
)


def _event_by_time(events: list[MBOEvent], timestamp: int) -> MBOEvent | None:
    candidate = None
    for event in events:
        if event.exchange_timestamp <= timestamp:
            candidate = event
        else:
            break
    return candidate


def _event_log_row(signal_id: int, macro: MBOEvent, micro: MBOEvent, intent: Any, fill: Any) -> dict[str, Any]:
    reason = intent.reason
    decay = reason.get("shadow_decay", {})
    return {
        "signal_id": f"ghost_route_{signal_id}",
        "timestamp_signal": intent.timestamp_signal,
        "timestamp_order_arrival": intent.timestamp_order_arrival,
        "macro_contract": intent.macro_contract,
        "micro_contract": intent.micro_contract,
        "direction": intent.direction,
        "macro_bid": macro.best_bid,
        "macro_ask": macro.best_ask,
        "macro_bid_size": macro.best_bid_size,
        "macro_ask_size": macro.best_ask_size,
        "micro_bid": micro.best_bid,
        "micro_ask": micro.best_ask,
        "micro_bid_size": micro.best_bid_size,
        "micro_ask_size": micro.best_ask_size,
        "shadow_decay_side": decay.get("side", ""),
        "NSD": decay.get("normalized_shadow_decay", 0.0),
        "CTR": decay.get("cancel_trade_ratio", 0.0),
        "nOFI": reason.get("nOFI", 0.0),
        "spread_zscore": reason.get("spread_zscore", 0.0),
        "expected_edge_ticks": reason.get("expected_edge_ticks", 0.0),
        "target_price": intent.target_price,
        "target_quantity": intent.target_quantity,
        "available_depth_at_signal": reason.get("available_depth", 0.0),
        "available_depth_at_arrival": fill.available_depth_at_arrival,
        "fill_status": fill.fill_status,
        "filled_quantity": fill.filled_quantity,
        "fill_price": fill.fill_price,
        "micro_reprice_time": "",
        "lead_time_us": "",
        "markout_10us": "",
        "markout_25us": "",
        "markout_50us": "",
        "markout_100us": "",
        "markout_250us": "",
        "markout_1ms": "",
        "gross_pnl": 0.0,
        "fees": 0.0,
        "slippage_cost": 0.0,
        "adverse_selection_cost": 0.0,
        "net_pnl": 0.0,
        "reject_reason": fill.reject_reason,
    }


def run_backtest(
    macro_events: Iterable[MBOEvent | dict[str, Any]],
    micro_events: Iterable[MBOEvent | dict[str, Any]],
    *,
    macro_contract: str,
    config: GhostRouteConfig | None = None,
    reports_dir: Path | None = None,
) -> dict[str, Any]:
    """Replay events and return summary plus event log.

    This wrapper is intentionally small: it proves ordering, quality gates,
    signal emission, latency insertion, and FAK simulation. Production-scale
    historical data loading stays outside this standalone model.
    """

    if macro_contract not in CONTRACT_PAIRS:
        raise ValueError(f"unsupported macro contract: {macro_contract}")
    cfg = config or GhostRouteConfig()
    macro = sort_events(macro_events)
    micro = sort_events(micro_events)
    macro_quality = validate_event_window(macro)
    micro_quality = validate_event_window(micro)
    if not macro_quality.ok or not micro_quality.ok:
        reject_reason = macro_quality.reject_reason or micro_quality.reject_reason
        summary = {
            "classification": "FAIL",
            "data_quality_ok": False,
            "reject_reason": reject_reason,
            "event_log": [],
        }
        return summary
    model = GhostRouteModel(cfg)
    rows: list[dict[str, Any]] = []
    for idx in range(1, len(macro)):
        current_macro = macro[idx]
        current_micro = _event_by_time(micro, current_macro.exchange_timestamp)
        if current_micro is None:
            continue
        window_start = current_macro.exchange_timestamp - cfg.delta_t_us
        window = [event for event in macro if window_start <= event.exchange_timestamp <= current_macro.exchange_timestamp]
        intent = model.evaluate(
            macro_contract=macro_contract,
            macro_window_events=window,
            previous_macro=macro[idx - 1],
            current_macro=current_macro,
            current_micro=current_micro,
            data_quality_ok=True,
            global_risk_block=False,
        )
        if intent is None:
            continue
        arrival_book = _event_by_time(micro, intent.timestamp_order_arrival)
        fill = simulate_fak_order(intent, arrival_book or current_micro)
        rows.append(_event_log_row(len(rows) + 1, current_macro, current_micro, intent, fill))
    summary = summarize_event_log(rows)
    summary.update(
        {
            "model": "ghost_route",
            "macro_contract": macro_contract,
            "micro_contract": CONTRACT_PAIRS[macro_contract],
            "data_quality_ok": True,
            "config": asdict(cfg),
            "event_log": rows,
        }
    )
    if reports_dir is not None:
        write_csv(reports_dir / "ghost_route_backtest_report.csv", rows)
        write_markdown_report(reports_dir / "ghost_route_backtest_report.md", summary)
    return summary
