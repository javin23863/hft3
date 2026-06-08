"""Load equities universe config and enforce lane data-isolation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from equities_lane.src.types import (
    DatabentoConfig,
    ExecutionConfig,
    ExperimentConfig,
    FeatureToggles,
    FilterConfig,
    PatternConfig,
    SessionSpec,
    SignalConfig,
    UniverseConfig,
    WalkForwardConfig,
)


def _assert_data_isolation(path: Path, repo_root: Path, label: str) -> None:
    prod_npz = (repo_root / "data" / "npz").resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(prod_npz)
        raise ValueError(
            f"Data-isolation violation: {label}={path} must not be under production {prod_npz}"
        )
    except ValueError as exc:
        if "Data-isolation violation" in str(exc):
            raise
    return


def _paths_from_cfg(data: dict[str, Any], repo_root: Path) -> dict[str, Path]:
    paths_cfg = data.get("paths", {})
    paths = {
        "raw_root": repo_root / paths_cfg.get("raw_root", "data/equities/raw"),
        "normalized_root": repo_root / paths_cfg.get("normalized_root", "data/equities/normalized"),
        "replay_root": repo_root / paths_cfg.get("replay_root", "data/replay/equities"),
        "reports_root": repo_root / paths_cfg.get("reports_root", "research_cards/equities"),
        "float_metadata": repo_root / paths_cfg.get(
            "float_metadata", "packages/equities_lane/fixtures/float_metadata.csv"
        ),
        "daily_bars": repo_root / paths_cfg.get(
            "daily_bars", "data/equities/daily"
        ),
        "daily_bars_fixture": repo_root / paths_cfg.get(
            "daily_bars_fixture", "packages/equities_lane/fixtures/daily_bars.csv"
        ),
    }
    for label, p in paths.items():
        _assert_data_isolation(p, repo_root, label)
    return paths


def load_universe(path: str | Path) -> tuple[Path, UniverseConfig, dict[str, Path]]:
    cfg_path = Path(path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    repo_root = Path(data.get("repo_root", ".")).resolve()
    if not repo_root.is_absolute():
        # config/universe.yaml -> equities_lane -> packages -> repo root
        repo_root = (cfg_path.parent.parent.parent.parent / repo_root).resolve()

    paths = _paths_from_cfg(data, repo_root)
    db_raw = data.get("databento", {})
    databento = DatabentoConfig(
        dataset=str(db_raw.get("dataset", "XNAS.ITCH")),
        schema_primary=str(db_raw.get("schema_primary", "mbo")),
        schema_degraded=str(db_raw.get("schema_degraded", "mbp-1")),
        stype_in=str(db_raw.get("stype_in", "raw_symbol")),
        session_start=str(db_raw.get("session_start", "09:30")),
        session_end=str(db_raw.get("session_end", "16:00")),
        premarket_start=str(db_raw.get("premarket_start", "04:00")),
    )

    sessions = [
        SessionSpec(
            id=s["id"],
            symbol=s["symbol"],
            date=s["date"],
            fixture=s.get("fixture"),
        )
        for s in data.get("sessions", [])
    ]

    f_raw = data.get("filters", {})
    filters = FilterConfig(
        float_max_shares=_toggle(f_raw, "float_max_shares", 20_000_000),
        min_premarket_gap_pct=_toggle(f_raw, "min_premarket_gap_pct", 20.0),
        min_rvol=_toggle(f_raw, "min_rvol", 3.0),
        rvol_lookback_days=int(f_raw.get("min_rvol", {}).get("lookback_days", 20)),
        min_premarket_volume=_toggle(f_raw, "min_premarket_volume", 400_000),
        min_float_rotation=_toggle(f_raw, "min_float_rotation", 0.5),
        prior_runner=_toggle_bool(f_raw, "prior_runner", True),
        min_prior_run_pct=float(f_raw.get("prior_runner", {}).get("min_prior_run_pct", 15.0)),
        overhead_resistance=_toggle_bool(f_raw, "overhead_resistance", False),
        consolidation_days=int(f_raw.get("overhead_resistance", {}).get("consolidation_days", 20)),
    )

    feat_raw = data.get("features", {})
    features = FeatureToggles(
        ofi=bool(feat_raw.get("ofi", True)),
        vpin=bool(feat_raw.get("vpin", True)),
        hawkes=bool(feat_raw.get("hawkes", True)),
        hmm=bool(feat_raw.get("hmm", True)),
        l3_queue=bool(feat_raw.get("l3_queue", True)),
        l3_cancellation=bool(feat_raw.get("l3_cancellation", True)),
        l3_iceberg=bool(feat_raw.get("l3_iceberg", True)),
    )

    pat_raw = data.get("patterns", {})
    patterns = PatternConfig(
        orb_window_minutes=int(pat_raw.get("orb_window_minutes", 10)),
        consolidation_pullback_pct=float(pat_raw.get("consolidation_pullback_pct", 3.0)),
    )

    sig_raw = data.get("signals", {})
    signals = SignalConfig(
        min_mlofi_pc1=float(sig_raw.get("min_mlofi_pc1", 0.0)),
        min_hmm_markup_prob=float(sig_raw.get("min_hmm_markup_prob", 0.5)),
        max_vpin_exit_percentile=float(sig_raw.get("max_vpin_exit_percentile", 0.95)),
        min_hawkes_score=float(sig_raw.get("min_hawkes_score", 0.0)),
    )

    ex_raw = data.get("execution", {})
    execution = ExecutionConfig(
        latency_ms=float(ex_raw.get("latency_ms", 5.0)),
        slippage_bps=float(ex_raw.get("slippage_bps", 10.0)),
        fee_per_share=float(ex_raw.get("fee_per_share", 0.005)),
        allow_short=bool(ex_raw.get("allow_short", False)),
        borrow_fee_bps=float(ex_raw.get("borrow_fee_bps", 50.0)),
        halt_on_limit_up=bool(ex_raw.get("halt_on_limit_up", True)),
    )

    wf_raw = data.get("walk_forward", {})
    walk_forward = WalkForwardConfig(
        train_days=int(wf_raw.get("train_days", 60)),
        val_days=int(wf_raw.get("val_days", 20)),
        test_days=int(wf_raw.get("test_days", 20)),
        step_days=int(wf_raw.get("step_days", 20)),
    )

    exp_raw = data.get("experiment", {})
    experiment = ExperimentConfig(
        ablation_modules=list(exp_raw.get("ablation_modules", ["ofi", "vpin", "hawkes", "hmm"]))
    )

    universe = UniverseConfig(
        databento=databento,
        sessions=sessions,
        filters=filters,
        features=features,
        patterns=patterns,
        signals=signals,
        execution=execution,
        walk_forward=walk_forward,
        experiment=experiment,
        l3_only=bool(data.get("l3_only", True)),
    )
    return repo_root, universe, paths


def load_session_by_id(path: str | Path, session_id: str) -> SessionSpec:
    _, universe, _ = load_universe(path)
    for s in universe.sessions:
        if s.id == session_id:
            return s
    raise KeyError(f"session {session_id!r} not found in {path}")


def _toggle(raw: dict, key: str, default: float) -> tuple[bool, float]:
    block = raw.get(key, {})
    if isinstance(block, dict):
        return bool(block.get("enabled", True)), float(block.get("value", default))
    return True, float(default)


def _toggle_bool(raw: dict, key: str, default: bool) -> bool:
    block = raw.get(key, {})
    if isinstance(block, dict):
        return bool(block.get("enabled", default))
    return default
