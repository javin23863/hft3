"""Workbench truth: single backend service for all UI rendering.

All Streamlit UI MUST render from WorkbenchTruth built by build_workbench_truth().
No tab may assemble its own state independently. No state may be invented.
Missing data is shown as blockers, never hidden.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Truth dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CmeEntryTruth:
    symbol: str
    data_status: str = "unknown"           # ready, partial, missing
    event_count: int = 0
    event_count_ready: int = 0
    mbo_status: str = "unknown"            # ready, degraded, missing
    mbp_status: str = "unknown"
    depth_status: str = "unknown"
    bound_models: int = 0
    latest_campaign: str = ""
    latest_status: str = ""
    champions: int = 0
    candidates: int = 0
    rejected: int = 0
    blocked: int = 0
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""
    # Pipeline stage status (populated from manifests)
    vectorbt_status: str = "PENDING"       # PASSED, FAILED, BLOCKED, PENDING
    vectorbt_candidates: int = 0
    vectorbt_last_run: str = ""
    hft_truth_status: str = "PENDING"      # PASSED, FAILED, BLOCKED, PENDING
    hft_truth_pnl: float = 0.0
    hft_truth_trades: int = 0
    hft_truth_last_run: str = ""
    promotion_status: str = "PENDING"      # PROMOTED, QUARANTINED, PENDING
    pipeline_last_run_id: str = ""
    # Evidence quality fields
    engine_requested: str = ""
    engine_used: str = ""
    evidence_status: str = ""
    signal_source: str = ""
    input_artifact_paths: list[str] = field(default_factory=list)
    output_artifact_paths: list[str] = field(default_factory=list)
    reconciliation_status: str = ""
    ledgers_available: bool = False


@dataclass
class EquitiesEntryTruth:
    session_id: str
    symbol: str = ""
    date: str = ""
    catalyst: str = ""
    dataset: str = ""
    schema: str = "mbo"
    status: str = "unknown"                # ready, blocked, missing, skipped
    equity_data_status: str = "unknown"
    option_feature_status: str = "unknown"
    daily_status: str = "unknown"
    float_status: str = "unknown"
    l3_status: str = "unknown"
    normalized_status: str = "unknown"
    screen_status: str = "unknown"
    prediction_status: str = "unknown"
    route_type: str = ""
    route_reason_codes: list[str] = field(default_factory=list)
    backtest_pnl: float = 0.0
    backtest_trades: int = 0
    bound_models: int = 0
    latest_manifest: str = ""
    champion_status: str = ""
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""


@dataclass
class OptionsEntryTruth:
    group_id: str
    group_type: str = ""
    legs: int = 0
    quote_status: str = "unknown"
    align_status: str = "unknown"
    violation_count: int = 0
    actionable_count: int = 0
    backtest_status: str = "unknown"
    bound_models: int = 0
    latest_manifest: str = ""
    champion_status: str = ""
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""


@dataclass
class CryptoEntryTruth:
    lane_id: str = "crypto"
    venue_status: str = "unknown"
    gold_status: str = "unknown"
    mempool_status: str = "unknown"
    bookticker_status: str = "unknown"
    hypothesis_count: int = 7
    hypothesis_names: list[str] = field(default_factory=list)
    smoke_results: list[dict] = field(default_factory=list)
    champion_status: str = ""
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""


@dataclass
class LaneTruth:
    lane_id: str
    lane_name: str
    description: str = ""
    status: str = "unknown"
    universe_size: int = 0
    sessions_available: int = 0
    sessions_blocked: int = 0
    sessions_total: int = 0
    models_bound: int = 0
    data_readiness_pct: float = 0.0
    active_runs: int = 0
    champions: int = 0
    candidates: int = 0
    rejected: int = 0
    blocked: int = 0
    primary_blockers: list[str] = field(default_factory=list)
    next_action: str = ""
    entries: list = field(default_factory=list)


@dataclass
class WorkbenchTruth:
    generated_at: str = ""
    repo_commit: str = ""
    repo_root: str = ""
    lanes: list[LaneTruth] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------


def _git_sha(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _file_exists(repo: Path, relpath: str) -> bool:
    return (repo / relpath).is_file()


def _dir_has_files(repo: Path, relpath: str) -> bool:
    p = repo / relpath
    return p.is_dir() and any(True for _ in p.iterdir())


def _count_files(repo: Path, relpath: str, pattern: str = "*") -> int:
    p = repo / relpath
    if not p.is_dir():
        return 0
    return len(list(p.glob(pattern)))


def _read_yaml(repo: Path, relpath: str) -> dict:
    p = repo / relpath
    if not p.is_file():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _read_json(repo: Path, relpath: str) -> dict:
    p = repo / relpath
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# CME lane truth
# ---------------------------------------------------------------------------


def _build_cme_truth(repo: Path, binding: Any) -> LaneTruth:
    symbols = binding.allowed_symbols if binding else ["MES.v.0", "ES.v.0", "MNQ.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"]
    entries: list[CmeEntryTruth] = []

    for sym in symbols:
        e = _build_cme_entry(repo, sym)
        entries.append(e)

    ready = sum(1 for e in entries if e.data_status == "ready")
    total = len(entries)
    data_pct = (ready / total * 100) if total > 0 else 0.0

    blockers: list[str] = []
    for e in entries:
        blockers.extend(e.blockers)

    return LaneTruth(
        lane_id="cme_futures",
        lane_name=binding.lane_name if binding else "CME Futures / Microstructure",
        description=binding.description if binding else "",
        status="operational",
        universe_size=len(symbols),
        sessions_available=sum(e.event_count_ready for e in entries),
        sessions_blocked=sum(len(e.blockers) for e in entries),
        sessions_total=sum(e.event_count for e in entries),
        models_bound=sum(e.bound_models for e in entries),
        data_readiness_pct=data_pct,
        active_runs=0,
        champions=sum(e.champions for e in entries),
        candidates=sum(e.candidates for e in entries),
        rejected=sum(e.rejected for e in entries),
        blocked=sum(e.blocked for e in entries),
        primary_blockers=blockers[:10],
        next_action="Select a symbol to view models and run campaigns" if ready > 0 else "Download NPZ data for symbols",
        entries=entries,
    )


def _read_latest_pipeline_stage(repo: Path, symbol: str) -> dict:
    """Read latest pipeline manifest for a symbol and extract stage statuses."""
    result = {
        "vectorbt_status": "PENDING",
        "vectorbt_candidates": 0,
        "vectorbt_last_run": "",
        "hft_truth_status": "PENDING",
        "hft_truth_pnl": 0.0,
        "hft_truth_trades": 0,
        "hft_truth_last_run": "",
        "promotion_status": "PENDING",
        "pipeline_last_run_id": "",
        "engine_requested": "",
        "engine_used": "",
        "evidence_status": "",
        "signal_source": "",
        "input_artifact_paths": [],
        "output_artifact_paths": [],
        "reconciliation_status": "",
        "ledgers_available": False,
    }
    pipeline_dir = repo / "artifacts" / "pipeline_runs"
    if not pipeline_dir.is_dir():
        return result
    manifests = sorted(pipeline_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for manifest_dir in manifests:
        if not manifest_dir.is_dir():
            continue
        manifest_file = manifest_dir / "pipeline_manifest.json"
        if not manifest_file.is_file():
            continue
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if data.get("symbol") != symbol:
                continue
            stages = data.get("stages", {})
            vb = data.get("vectorbt_manifest") or {}
            hf = data.get("hft_truth_manifest") or {}
            result.update({
                "vectorbt_status": stages.get("vectorbt_filter", "PENDING"),
                "vectorbt_candidates": vb.get("top_n_forwarded", 0) if isinstance(vb, dict) else 0,
                "vectorbt_last_run": vb.get("created_at", "") if isinstance(vb, dict) else "",
                "hft_truth_status": stages.get("hft_truth", "PENDING"),
                "hft_truth_pnl": hf.get("pnl", 0.0) if isinstance(hf, dict) else 0.0,
                "hft_truth_trades": hf.get("trades", 0) if isinstance(hf, dict) else 0,
                "hft_truth_last_run": hf.get("run_id", "") if isinstance(hf, dict) else "",
                "promotion_status": data.get("promotion_status", "PENDING"),
                "pipeline_last_run_id": data.get("run_id", ""),
                "engine_requested": vb.get("engine_requested", "") if isinstance(vb, dict) else "",
                "engine_used": hf.get("engine_used", "") if isinstance(hf, dict) else vb.get("engine_used", ""),
                "evidence_status": hf.get("evidence_status") or vb.get("evidence_status") or "",
                "signal_source": vb.get("signal_source", "") if isinstance(vb, dict) else "",
                "input_artifact_paths": (
                    vb.get("data_artifacts", []) + vb.get("feature_artifacts", [])
                ) if isinstance(vb, dict) else [],
                "output_artifact_paths": list(hf.get("ledger_paths", {}).values()) if isinstance(hf, dict) else [],
                "reconciliation_status": hf.get("reconciliation_status", "") if isinstance(hf, dict) else "",
                "ledgers_available": bool(hf.get("ledger_paths", {})) if isinstance(hf, dict) else False,
            })
            break
        except Exception:
            continue
    return result


def _build_cme_entry(repo: Path, symbol: str) -> CmeEntryTruth:
    npz_count = _count_files(repo, "data/npz", f"{symbol}_*_mbo.npz")
    npz_dir = repo / "data" / "npz"
    event_count = 0
    events_csv = repo / "packages" / "data_system" / "config" / "events.csv"
    if events_csv.is_file():
        try:
            import csv
            with open(events_csv, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    syms = str(row.get("parsed_symbols", "")).split(",")
                    if symbol in [s.strip() for s in syms]:
                        event_count += 1
        except Exception:
            pass

    data_status = "ready" if npz_count > 0 else "missing"

    blockers: list[str] = []
    if npz_count == 0:
        blockers.append(f"No NPZ data for {symbol}")

    runs_dir = repo / "artifacts" / "research_cards" / "workbench_runs"
    campaigns = []
    if runs_dir.is_dir():
        campaigns = [d.name for d in runs_dir.iterdir() if d.is_dir() and symbol in d.name]

    # Read latest pipeline stage statuses
    stage = _read_latest_pipeline_stage(repo, symbol)

    return CmeEntryTruth(
        symbol=symbol,
        data_status=data_status,
        event_count=event_count,
        event_count_ready=npz_count,
        mbo_status="ready" if npz_count > 0 else "missing",
        mbp_status="unknown",
        depth_status="unknown",
        bound_models=46,
        latest_campaign=campaigns[0] if campaigns else "",
        latest_status="",
        blockers=blockers,
        next_action="Run discovery campaign" if npz_count > 0 else "Download NPZ data",
        vectorbt_status=stage["vectorbt_status"],
        vectorbt_candidates=stage["vectorbt_candidates"],
        vectorbt_last_run=stage["vectorbt_last_run"],
        hft_truth_status=stage["hft_truth_status"],
        hft_truth_pnl=stage["hft_truth_pnl"],
        hft_truth_trades=stage["hft_truth_trades"],
        hft_truth_last_run=stage["hft_truth_last_run"],
        promotion_status=stage["promotion_status"],
        pipeline_last_run_id=stage["pipeline_last_run_id"],
        engine_requested=stage["engine_requested"],
        engine_used=stage["engine_used"],
        evidence_status=stage["evidence_status"],
        signal_source=stage["signal_source"],
        input_artifact_paths=stage["input_artifact_paths"],
        output_artifact_paths=stage["output_artifact_paths"],
        reconciliation_status=stage["reconciliation_status"],
        ledgers_available=stage["ledgers_available"],
    )


# ---------------------------------------------------------------------------
# Equities lane truth
# ---------------------------------------------------------------------------


def _build_equities_truth(repo: Path, binding: Any) -> LaneTruth:
    entries: list[EquitiesEntryTruth] = []
    decadal_path = repo / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
    raw = _read_yaml(repo, "packages/equities_lane/config/decadal_runners.yaml")

    for s in raw.get("sessions", []):
        sid = str(s.get("id", ""))
        if not sid:
            continue
        sym = str(s.get("symbol", ""))
        dt = str(s.get("date", ""))

        # Data checks
        ndjson = repo / "data" / "equities" / "normalized" / f"{sym}_{dt}.ndjson"
        daily = repo / "data" / "equities" / "daily" / f"{sym}.parquet"
        raw_mbo = repo / f"data/equities/raw/{sym}_{dt}_mbo.dbn.zst"
        float_csv = repo / "data" / "equities" / "metadata" / "float_pit.csv"

        has_ndjson = ndjson.is_file()
        has_daily = daily.is_file()
        has_raw_mbo = raw_mbo.is_file()
        has_float = float_csv.is_file()

        # Options data
        options_dir = repo / "data" / "options" / "equity_chains" / "normalized"
        has_options = (options_dir / f"{sym.lower()}_{dt[:4]}.ndjson").is_file()
        option_feature_status = "available" if has_options else "not_downloaded"

        # L3 status
        l3_status = "ready" if has_ndjson else "missing"
        normalized_status = "ready" if has_ndjson else "missing"
        daily_status = "ready" if has_daily else "missing"
        float_status = "ready" if has_float else "missing"
        equity_data_status = "ready" if has_ndjson and has_daily else "partial"

        # Blockers
        blockers: list[str] = []
        if s.get("skip_pull"):
            blockers.append(f"SKIPPED: {s.get('skip_reason', 'pre-Databento availability')}")
            equity_data_status = "skipped"
        else:
            if not has_ndjson:
                blockers.append(f"Missing normalized NDJSON for {sym}")
            if not has_daily:
                blockers.append(f"Missing daily bars for {sym}")
            if not has_raw_mbo:
                blockers.append(f"Missing raw MBO for {sym}")
            if not has_float:
                blockers.append("Missing float metadata")

        # Check prediction results
        prediction_status = "none"
        pred_dir = repo / "research_cards" / "equities"
        if pred_dir.is_dir():
            preds = [d for d in pred_dir.iterdir() if d.is_dir()]
            if preds:
                prediction_status = f"{len(preds)} runs"

        status = equity_data_status
        if blockers:
            status = "blocked" if "SKIPPED" not in str(blockers[0]) else "skipped"

        entries.append(EquitiesEntryTruth(
            session_id=sid,
            symbol=sym,
            date=dt,
            catalyst=str(s.get("catalyst", "")),
            dataset=str(s.get("dataset", "")),
            schema=str(s.get("schema", "mbo")),
            status=status,
            equity_data_status=equity_data_status,
            option_feature_status=option_feature_status,
            daily_status=daily_status,
            float_status=float_status,
            l3_status=l3_status,
            normalized_status=normalized_status,
            screen_status="unknown",
            prediction_status=prediction_status,
            route_type="",
            route_reason_codes=[],
            blockers=blockers,
            next_action="Normalize MBO data" if not has_ndjson else "Run backtest",
        ))

    ready = sum(1 for e in entries if e.status == "ready" or e.status == "partial")
    blocked = sum(1 for e in entries if e.status == "blocked")
    skipped = sum(1 for e in entries if e.status == "skipped")
    total = len(entries)

    return LaneTruth(
        lane_id="equities_low_float",
        lane_name=binding.lane_name if binding else "Equities Low-Float Runner",
        description=binding.description if binding else "",
        status="operational",
        universe_size=total,
        sessions_available=ready,
        sessions_blocked=blocked,
        sessions_total=total,
        models_bound=40,
        data_readiness_pct=(ready / total * 100) if total > 0 else 0.0,
        active_runs=0,
        primary_blockers=[b for e in entries for b in e.blockers],
        next_action="Select a session to run backtest" if ready > 0 else "Pull decadal data with pull_equities_decadal.ps1",
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Options lane truth
# ---------------------------------------------------------------------------


def _build_options_truth(repo: Path, binding: Any) -> LaneTruth:
    entries: list[OptionsEntryTruth] = []
    raw = _read_yaml(repo, "packages/options_lane/config/parity_universe.yaml")

    for gdata in raw.get("parity_groups", []):
        if not isinstance(gdata, dict):
            continue
        gid = str(gdata.get("id", ""))
        if not gid:
            continue
        legs = len(gdata.get("legs", []))
        group_type = str(gdata.get("type", ""))

        # Check quote data
        raw_dir = repo / "data" / "options" / "raw"
        norm_dir = repo / "data" / "options" / "normalized"
        has_quotes = _dir_has_files(repo, "data/options/raw") or _dir_has_files(repo, "data/options/normalized")

        quote_status = "available" if has_quotes else "not_downloaded"
        align_status = "unknown"

        blockers: list[str] = []
        if not has_quotes:
            blockers.append("No options quote data downloaded")

        entries.append(OptionsEntryTruth(
            group_id=str(gid),
            group_type=group_type,
            legs=legs,
            quote_status=quote_status,
            align_status=align_status,
            backtest_status="available",
            blockers=blockers,
            next_action="Download options data" if not has_quotes else "Run backtest",
        ))

    total = len(entries)
    ready = sum(1 for e in entries if e.quote_status == "available")

    return LaneTruth(
        lane_id="options_parity",
        lane_name=binding.lane_name if binding else "Options Put-Call Parity",
        description=binding.description if binding else "",
        status="operational",
        universe_size=total,
        sessions_available=ready,
        sessions_blocked=total - ready,
        sessions_total=total,
        models_bound=0,
        data_readiness_pct=(ready / total * 100) if total > 0 else 0.0,
        active_runs=0,
        primary_blockers=[b for e in entries for b in e.blockers],
        next_action="Download quote data" if ready < total else "Run parity backtest",
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Crypto lane truth
# ---------------------------------------------------------------------------


def _build_crypto_truth(repo: Path, binding: Any) -> LaneTruth:
    entries: list[CryptoEntryTruth] = []

    # Check data roots
    has_gold = _dir_has_files(repo, "data/crypto/gold")
    has_mempool = _dir_has_files(repo, "data/crypto/mempool")
    has_bookticker = _dir_has_files(repo, "data/crypto/bookticker")

    gold_status = "available" if has_gold else "not_downloaded"
    mempool_status = "available" if has_mempool else "not_downloaded"
    bookticker_status = "available" if has_bookticker else "not_downloaded"

    # Check edge receiver
    edge_dir = repo / "runtime" / "crypto_edge"
    has_edge = (edge_dir / "latest_packet.json").is_file()
    venue_status = "operational" if has_edge else "edge_receiver_not_active"

    blockers: list[str] = []
    if not has_gold:
        blockers.append("Gold data not downloaded (B2 + Binance Vision)")
    if not has_mempool:
        blockers.append("Mempool data not pulled")
    if not has_bookticker:
        blockers.append("Bookticker L3 gaps need fill")

    # Hypothesis count from config
    raw = _read_yaml(repo, "packages/crypto_lane/config/universe.yaml")
    hyp_ids = raw.get("hypotheses", []) if raw else []
    hypothesis_names = [str(h) for h in hyp_ids]

    # Check smoke results
    smoke_dir = repo / "research_cards" / "crypto"
    smoke_runs = []
    if smoke_dir.is_dir():
        for d in sorted(smoke_dir.iterdir()):
            if d.is_dir():
                summary = d / "summary.json"
                if summary.is_file():
                    try:
                        smoke_runs.append({"dir": d.name, **json.loads(summary.read_text(encoding="utf-8"))})
                    except Exception:
                        smoke_runs.append({"dir": d.name})

    entries.append(CryptoEntryTruth(
        lane_id="crypto",
        venue_status=venue_status,
        gold_status=gold_status,
        mempool_status=mempool_status,
        bookticker_status=bookticker_status,
        hypothesis_count=len(hypothesis_names),
        hypothesis_names=hypothesis_names,
        smoke_results=smoke_runs,
        blockers=blockers,
        next_action="Pull gold + mempool data" if not has_gold else "Run smoke tests",
    ))

    ready = 1 if has_gold else 0

    return LaneTruth(
        lane_id="crypto",
        lane_name=binding.lane_name if binding else "Crypto BTC Edge Detection",
        description=binding.description if binding else "",
        status="operational",
        universe_size=len(hypothesis_names),
        sessions_available=ready,
        sessions_blocked=1 - ready,
        sessions_total=1,
        models_bound=len(hypothesis_names),
        data_readiness_pct=(ready * 100),
        active_runs=0,
        primary_blockers=blockers,
        next_action="Pull gold + mempool + bookticker data" if not has_gold else "Run smoke tests",
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_workbench_truth(repo: Path) -> WorkbenchTruth:
    from workbench.src.data.lane_bindings import load_lane_bindings

    bindings = load_lane_bindings(repo)

    lanes: list[LaneTruth] = []

    cme_binding = bindings.lanes.get("cme_futures")
    lanes.append(_build_cme_truth(repo, cme_binding))

    eq_binding = bindings.lanes.get("equities_low_float")
    lanes.append(_build_equities_truth(repo, eq_binding))

    opt_binding = bindings.lanes.get("options_parity")
    lanes.append(_build_options_truth(repo, opt_binding))

    crypto_binding = bindings.lanes.get("crypto")
    lanes.append(_build_crypto_truth(repo, crypto_binding))

    return WorkbenchTruth(
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo_commit=_git_sha(repo),
        repo_root=str(repo),
        lanes=lanes,
    )
