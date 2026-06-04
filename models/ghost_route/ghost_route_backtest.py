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
    load_config,
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
    "gross_pnl",
    "fees",
    "slippage_cost",
    "adverse_selection_cost",
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


def _event_at_or_after(events: list[MBOEvent], timestamp: int) -> MBOEvent | None:
    for event in events:
        if event.exchange_timestamp >= timestamp:
            return event
    return events[-1] if events else None


def _mid_price(event: MBOEvent) -> float:
    return (event.best_bid + event.best_ask) / 2.0


def _markout_ticks(fill_price: float, book: MBOEvent | None, direction: str, tick_size: float) -> float:
    if book is None or fill_price <= 0.0:
        return 0.0
    mid = _mid_price(book)
    if direction == "BUY":
        return (mid - fill_price) / tick_size
    return (fill_price - mid) / tick_size


def _micro_reprice_time(events: list[MBOEvent], *, signal_ts: int, direction: str, target_price: float) -> int | str:
    for event in events:
        if event.exchange_timestamp < signal_ts:
            continue
        if direction == "BUY" and event.best_ask > target_price:
            return event.exchange_timestamp
        if direction == "SELL" and event.best_bid < target_price:
            return event.exchange_timestamp
    return ""


def _event_log_row(
    signal_id: int,
    macro: MBOEvent,
    micro: MBOEvent,
    intent: Any,
    fill: Any,
    *,
    micro_events: list[MBOEvent],
    config: GhostRouteConfig,
) -> dict[str, Any]:
    reason = intent.reason
    decay = reason.get("shadow_decay", {})
    tick_size = config.tick_sizes[intent.micro_contract]
    filled_qty = int(fill.filled_quantity)
    horizons = {
        "markout_10us": 10,
        "markout_25us": 25,
        "markout_50us": 50,
        "markout_100us": 100,
        "markout_250us": 250,
        "markout_1ms": 1_000,
    }
    markouts = {
        key: _markout_ticks(
            fill.fill_price,
            _event_at_or_after(micro_events, intent.timestamp_order_arrival + config.us_to_timestamp_delta(horizon_us)),
            intent.direction,
            tick_size,
        )
        for key, horizon_us in horizons.items()
    }
    horizon_key = "markout_1ms" if config.pnl_markout_horizon_us == 1_000 else f"markout_{config.pnl_markout_horizon_us}us"
    gross_per_contract = markouts.get(horizon_key, markouts["markout_250us"])
    gross_pnl = gross_per_contract * filled_qty
    fees = config.fees_ticks * filled_qty
    slippage = config.estimated_slippage_ticks * filled_qty
    adverse = config.adverse_selection_penalty_ticks * filled_qty
    if filled_qty:
        net_pnl = gross_pnl - fees - slippage - adverse
    else:
        net_pnl = -config.miss_penalty_ticks * int(intent.target_quantity)
    lead_time_us = config.timestamp_delta_to_us(intent.timestamp_order_arrival - intent.timestamp_signal)
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
        "micro_reprice_time": _micro_reprice_time(
            micro_events,
            signal_ts=intent.timestamp_signal,
            direction=intent.direction,
            target_price=intent.target_price,
        ),
        "lead_time_us": lead_time_us,
        **markouts,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "slippage_cost": slippage,
        "adverse_selection_cost": adverse,
        "net_pnl": net_pnl,
        "reject_reason": fill.reject_reason,
    }


def run_backtest(
    macro_events: Iterable[MBOEvent | dict[str, Any]],
    micro_events: Iterable[MBOEvent | dict[str, Any]],
    *,
    macro_contract: str,
    config: GhostRouteConfig | None = None,
    config_path: Path | None = None,
    reports_dir: Path | None = None,
) -> dict[str, Any]:
    """Replay events and return summary plus event log.

    This wrapper is intentionally small: it proves ordering, quality gates,
    signal emission, latency insertion, and FAK simulation. Production-scale
    historical data loading stays outside this standalone model.
    """

    if macro_contract not in CONTRACT_PAIRS:
        raise ValueError(f"unsupported macro contract: {macro_contract}")
    cfg = config or load_config(config_path)
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
        window_start = current_macro.exchange_timestamp - cfg.us_to_timestamp_delta(cfg.delta_t_us)
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
        rows.append(
            _event_log_row(
                len(rows) + 1,
                current_macro,
                current_micro,
                intent,
                fill,
                micro_events=micro,
                config=cfg,
            )
        )
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
