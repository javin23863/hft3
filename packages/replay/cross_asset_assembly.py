"""Cross-asset feature assembly, PIT alignment proof, and leader-leg validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Sequence

# Canonical micro → full-size leader relationships (specs/FEATURES.md cross-asset).
TARGET_DEFAULT_LEADERS: dict[str, tuple[str, ...]] = {
    "MES": ("ES",),
    "MNQ": ("NQ",),
    "ES": ("NQ", "ZN"),  # divergence / macro pairs
    "NQ": ("ES", "ZN"),
}

PROVENANCE_SYMBOL = "_symbol"
PROVENANCE_SOURCE_TS = "_source_timestamp_ns"


@dataclass
class CrossAssetAlignmentResult:
    ok: bool
    target_symbol: str
    required_leaders: tuple[str, ...]
    present_leaders: tuple[str, ...]
    missing_leaders: tuple[str, ...]
    reasons: list[str] = field(default_factory=list)
    per_leg: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_proof(self) -> dict[str, Any]:
        return {
            "target_symbol": self.target_symbol,
            "required_leaders": list(self.required_leaders),
            "present_leaders": list(self.present_leaders),
            "missing_leaders": list(self.missing_leaders),
            "ok": self.ok,
            "reasons": list(self.reasons),
            "per_leg": dict(self.per_leg),
        }


def default_leaders_for_target(target_symbol: str) -> tuple[str, ...]:
    base = str(target_symbol or "MES").split(".")[0].upper()
    return TARGET_DEFAULT_LEADERS.get(base, ("ES",))


def enrich_cross_leg(
    features: Mapping[str, Any],
    *,
    symbol: str,
    source_timestamp_ns: int,
) -> dict[str, Any]:
    """Attach provenance fields to a cross-asset feature leg."""
    out = {k: v for k, v in features.items() if not str(k).startswith("_")}
    out[PROVENANCE_SYMBOL] = str(symbol).upper()
    out[PROVENANCE_SOURCE_TS] = int(source_timestamp_ns)
    return out


def _leg_timestamp_ns(leg: Mapping[str, Any]) -> int | None:
    raw = leg.get(PROVENANCE_SOURCE_TS)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_own_symbol_placeholder(
    *,
    target_symbol: str,
    leader_symbol: str,
    cross_asset_features: Mapping[str, Mapping[str, Any]],
) -> bool:
    """True when leader leg is absent or identical symbol (invalid cross-asset evidence)."""
    target = str(target_symbol).split(".")[0].upper()
    leader = str(leader_symbol).split(".")[0].upper()
    if leader == target:
        return True
    leg = cross_asset_features.get(leader)
    if not isinstance(leg, Mapping) or not leg:
        return True
    declared = str(leg.get(PROVENANCE_SYMBOL) or leader).split(".")[0].upper()
    return declared == target


def validate_cross_asset_alignment(
    cross_asset_features: Mapping[str, Mapping[str, Any]],
    *,
    target_symbol: str,
    required_leaders: Sequence[str] | None = None,
    decision_timestamp_ns: int | None = None,
    max_staleness_ns: int | None = None,
) -> CrossAssetAlignmentResult:
    """Validate leader legs exist with provenance; reject future source timestamps."""
    target = str(target_symbol).split(".")[0].upper()
    leaders = tuple(
        str(s).split(".")[0].upper()
        for s in (required_leaders or default_leaders_for_target(target))
    )
    present: list[str] = []
    missing: list[str] = []
    reasons: list[str] = []
    per_leg: dict[str, dict[str, Any]] = {}

    for leader in leaders:
        if is_own_symbol_placeholder(
            target_symbol=target,
            leader_symbol=leader,
            cross_asset_features=cross_asset_features,
        ):
            missing.append(leader)
            reasons.append(f"missing_or_placeholder_leg:{leader}")
            continue
        leg = cross_asset_features.get(leader) or {}
        src_ts = _leg_timestamp_ns(leg)
        leg_proof: dict[str, Any] = {
            "symbol": leader,
            "source_timestamp_ns": src_ts,
            "feature_keys": sorted(k for k in leg if not str(k).startswith("_")),
        }
        if decision_timestamp_ns is not None and src_ts is not None:
            if src_ts > decision_timestamp_ns:
                reasons.append(f"future_source_timestamp:{leader}")
                leg_proof["pit_ok"] = False
            elif max_staleness_ns is not None and decision_timestamp_ns - src_ts > max_staleness_ns:
                reasons.append(f"stale_source_timestamp:{leader}")
                leg_proof["pit_ok"] = False
            else:
                leg_proof["pit_ok"] = True
        else:
            leg_proof["pit_ok"] = src_ts is not None
        per_leg[leader] = leg_proof
        present.append(leader)

    ok = not missing and not any("future_source_timestamp" in r for r in reasons)
    return CrossAssetAlignmentResult(
        ok=ok,
        target_symbol=target,
        required_leaders=leaders,
        present_leaders=tuple(present),
        missing_leaders=tuple(missing),
        reasons=reasons,
        per_leg=per_leg,
    )


def apply_cross_asset_to_recipe_family(
    family_row: MutableMapping[str, Any],
    alignment: CrossAssetAlignmentResult,
) -> None:
    """Update cross_asset_futures family metadata from alignment proof."""
    family_row["source_symbols"] = list(alignment.present_leaders)
    family_row["source_ids"] = list(alignment.present_leaders)
    if alignment.ok:
        family_row["model_consumption_state"] = "not_measured"
        family_row["missingness_state"] = "present"
        family_row["pit_proof"] = "declared"
        family_row["why_not_used_or_sidelined"] = (
            "leader_legs_aligned_consumption_not_observed_in_screening_path"
        )
    else:
        family_row["model_consumption_state"] = "sidelined_missing_data"
        family_row["missingness_state"] = "missing"
        family_row["pit_proof"] = "rejected" if alignment.reasons else "pending"
        family_row["why_not_used_or_sidelined"] = (
            ";".join(alignment.reasons) if alignment.reasons else "missing_leader_legs"
        )
