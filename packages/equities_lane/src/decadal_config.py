"""Load decadal runner catalog."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from equities_lane.src.config_loader import _assert_data_isolation
from equities_lane.src.types import ChainRules, DecadalCatalog, DecadalSession, OptionsChainSpec


def _repo_root_from_cfg(cfg_path: Path, data: dict[str, Any]) -> Path:
    repo_root = Path(data.get("repo_root", ".")).resolve()
    if not repo_root.is_absolute():
        repo_root = (cfg_path.parent.parent.parent.parent / repo_root).resolve()
    return repo_root


def _decadal_paths(data: dict[str, Any], repo_root: Path) -> dict[str, Path]:
    paths_cfg = data.get("paths", {})
    paths = {
        "raw_root": repo_root / paths_cfg.get("raw_root", "data/equities/raw"),
        "normalized_root": repo_root / paths_cfg.get("normalized_root", "data/equities/normalized"),
        "daily_root": repo_root / paths_cfg.get("daily_root", "data/equities/daily"),
        "manifest_root": repo_root / paths_cfg.get("manifest_root", "data/equities/manifest"),
        "float_metadata": repo_root / paths_cfg.get(
            "float_metadata", "data/equities/metadata/float_pit.csv"
        ),
        "options_chains_raw": repo_root / "data/options/equity_chains/raw",
        "options_chains_normalized": repo_root / "data/options/equity_chains/normalized",
    }
    for label, p in paths.items():
        _assert_data_isolation(p, repo_root, label)
    return paths


def _load_chain_rules(raw: dict[str, Any]) -> ChainRules:
    return ChainRules(
        reference_price=str(raw.get("reference_price", "premarket_open")),
        strike_window_pct=float(raw.get("strike_window_pct", 0.30)),
        max_strikes_per_side=int(raw.get("max_strikes_per_side", 10)),
        expiry_policy=str(raw.get("expiry_policy", "nearest_listed")),
        max_dte_days=int(raw.get("max_dte_days", 45)),
    )


def _load_options_spec(raw: dict[str, Any] | None, defaults: dict[str, Any]) -> OptionsChainSpec | None:
    base = defaults.get("options", {})
    merged = {**base, **(raw or {})}
    if not merged.get("enabled", True):
        return None
    rules_raw = merged.get("chain_rules", {})
    if isinstance(base.get("chain_rules"), dict):
        rules_raw = {**base["chain_rules"], **rules_raw}
    return OptionsChainSpec(
        enabled=True,
        dataset=str(merged.get("dataset", "OPRA.PILLAR")),
        schema=str(merged.get("schema", "cbbo-1m")),
        stype_in=str(merged.get("stype_in", "parent")),
        inherit_window=bool(merged.get("inherit_window", True)),
        chain_rules=_load_chain_rules(rules_raw if isinstance(rules_raw, dict) else {}),
    )


def load_decadal_catalog(path: str | Path) -> DecadalCatalog:
    cfg_path = Path(path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    repo_root = _repo_root_from_cfg(cfg_path, data)
    paths = _decadal_paths(data, repo_root)
    defaults = data.get("defaults", {})
    sessions: list[DecadalSession] = []
    for row in data.get("sessions", []):
        skip_pull = bool(row.get("skip_pull", False))
        skip_reason = row.get("skip_reason")
        if skip_reason and not skip_pull:
            skip_pull = True
        sessions.append(
            DecadalSession(
                id=str(row["id"]),
                symbol=str(row["symbol"]),
                date=str(row["date"]),
                dataset=str(row.get("dataset", defaults.get("dataset", "XNAS.ITCH"))),
                schema=str(row.get("schema", defaults.get("schema", "mbo"))),
                stype_in=str(row.get("stype_in", defaults.get("stype_in", "raw_symbol"))),
                premarket_start=str(
                    row.get("premarket_start", defaults.get("premarket_start", "04:00"))
                ),
                session_end=str(row.get("session_end", defaults.get("session_end", "16:00"))),
                catalyst=str(row.get("catalyst", "")),
                notes=str(row.get("notes", "")),
                expected_degraded=bool(row.get("expected_degraded", False)),
                skip_pull=skip_pull,
                skip_reason=str(skip_reason) if skip_reason else None,
                options=_load_options_spec(row.get("options"), defaults),
            )
        )
    return DecadalCatalog(
        repo_root=repo_root,
        sessions=sessions,
        paths=paths,
        daily_lookback_days=int(defaults.get("daily_lookback_days", 756)),
        l3_only=bool(defaults.get("l3_only", True)),
    )


def get_decadal_session(catalog: DecadalCatalog, session_id: str) -> DecadalSession:
    for s in catalog.sessions:
        if s.id == session_id:
            return s
    raise KeyError(f"decadal session {session_id!r} not found")
