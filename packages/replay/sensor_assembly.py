"""VIX/VVIX sensor and VIX-options feature assembly with PIT provenance."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping

from replay.cross_asset_assembly import PROVENANCE_SOURCE_TS, PROVENANCE_SYMBOL

VIX_SENSOR_KEY = "VIX"

VIX_VVIX_COLUMNS = frozenset({"vix_atm_strike", "vix_atm_ramp"})

VIX_OPTIONS_COLUMNS = frozenset(
    {
        "vix_opt_quote_intensity",
        "vix_quote_arrival_accel",
        "vix_opt_spread_stress",
        "vix_opt_depth_imbalance",
        "vix_opt_bipower_var",
        "vix_opt_tsrv",
    }
)

# Labeled proxy per vix_features.py — not true put/call decomposition.
VIX_OPTIONS_PROXY_COLUMNS = frozenset({"vix_opt_depth_imbalance"})


@dataclass
class VixFamilyValidation:
    vix_vvix_ok: bool
    vix_options_ok: bool
    vvix_missingness: str
    options_missingness: str
    reasons: list[str] = field(default_factory=list)
    vvix_columns_present: list[str] = field(default_factory=list)
    options_columns_present: list[str] = field(default_factory=list)
    proxy_only_columns: list[str] = field(default_factory=list)
    source_timestamp_ns: int | None = None
    decision_timestamp_ns: int | None = None

    def to_proof(self) -> dict[str, Any]:
        return {
            "sensor_key": VIX_SENSOR_KEY,
            "vix_vvix_ok": self.vix_vvix_ok,
            "vix_options_ok": self.vix_options_ok,
            "vvix_missingness": self.vvix_missingness,
            "options_missingness": self.options_missingness,
            "reasons": list(self.reasons),
            "vvix_columns_present": list(self.vvix_columns_present),
            "options_columns_present": list(self.options_columns_present),
            "proxy_only_columns": list(self.proxy_only_columns),
            "source_timestamp_ns": self.source_timestamp_ns,
            "decision_timestamp_ns": self.decision_timestamp_ns,
        }


def enrich_sensor_leg(
    features: Mapping[str, Any],
    *,
    sensor_id: str,
    source_timestamp_ns: int,
    sensor_kind: str = "vix_options",
) -> dict[str, Any]:
    """Attach provenance; preserve NaN (no silent zero-fill)."""
    out: dict[str, Any] = {}
    for key, value in features.items():
        if str(key).startswith("_"):
            continue
        out[key] = value
    out[PROVENANCE_SYMBOL] = str(sensor_id).upper()
    out[PROVENANCE_SOURCE_TS] = int(source_timestamp_ns)
    out["_sensor_kind"] = sensor_kind
    return out


def _value_state(value: Any) -> str:
    if value is None:
        return "missing"
    try:
        if math.isnan(float(value)):
            return "malformed"
    except (TypeError, ValueError):
        return "malformed"
    return "present"


def _leg_source_ts(leg: Mapping[str, Any]) -> int | None:
    raw = leg.get(PROVENANCE_SOURCE_TS)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def validate_vix_families(
    leg: Mapping[str, Any] | None,
    *,
    decision_timestamp_ns: int | None = None,
) -> VixFamilyValidation:
    """Validate VIX/VVIX vs VIX-options legs separately; never treat NaN as zero."""
    if not leg:
        return VixFamilyValidation(
            vix_vvix_ok=False,
            vix_options_ok=False,
            vvix_missingness="missing",
            options_missingness="missing",
            reasons=["vix_sensor_leg_missing"],
        )

    reasons: list[str] = []
    src_ts = _leg_source_ts(leg)
    if decision_timestamp_ns is not None:
        if src_ts is None:
            reasons.append("missing_vix_provenance")
        elif src_ts > decision_timestamp_ns:
            reasons.append("future_vix_source_timestamp")

    vvix_present: list[str] = []
    vvix_missing = 0
    for col in sorted(VIX_VVIX_COLUMNS):
        state = _value_state(leg.get(col))
        if state == "present":
            vvix_present.append(col)
        else:
            vvix_missing += 1
            if state == "malformed":
                reasons.append(f"malformed_vvix:{col}")

    opt_present: list[str] = []
    opt_missing = 0
    proxy_only: list[str] = []
    for col in sorted(VIX_OPTIONS_COLUMNS):
        state = _value_state(leg.get(col))
        if state == "present":
            opt_present.append(col)
            if col in VIX_OPTIONS_PROXY_COLUMNS:
                proxy_only.append(col)
        else:
            opt_missing += 1
            if state == "malformed":
                reasons.append(f"malformed_vix_options:{col}")

    vvix_miss_state = "missing" if not vvix_present else ("malformed" if vvix_missing else "present")
    if vvix_present and vvix_missing:
        vvix_miss_state = "available_not_selected"

    opt_miss_state = "missing" if not opt_present else ("malformed" if opt_missing else "present")
    if opt_present and opt_missing:
        opt_miss_state = "available_not_selected"
    if proxy_only and opt_present:
        opt_miss_state = "proxy_only" if len(proxy_only) == len(opt_present) else opt_miss_state

    vvix_malformed = any(r.startswith("malformed_vvix:") for r in reasons)
    opt_malformed = any(r.startswith("malformed_vix_options:") for r in reasons)
    pit_blocked = any(r in {"missing_vix_provenance", "future_vix_source_timestamp"} for r in reasons)

    vvix_ok = bool(vvix_present) and not vvix_malformed and not pit_blocked
    opt_ok = bool(opt_present) and not opt_malformed and not pit_blocked

    return VixFamilyValidation(
        vix_vvix_ok=vvix_ok,
        vix_options_ok=opt_ok,
        vvix_missingness=vvix_miss_state,
        options_missingness=opt_miss_state,
        reasons=reasons,
        vvix_columns_present=vvix_present,
        options_columns_present=opt_present,
        proxy_only_columns=proxy_only,
        source_timestamp_ns=src_ts,
        decision_timestamp_ns=decision_timestamp_ns,
    )


def apply_vix_to_recipe_families(
    feature_families: MutableMapping[str, MutableMapping[str, Any]],
    validation: VixFamilyValidation,
) -> None:
    """Update vix_vvix_sensor and vix_options family rows from sensor proof."""
    vvix = dict(feature_families.get("vix_vvix_sensor") or {})
    vvix["source_ids"] = [VIX_SENSOR_KEY]
    vvix["selected_features"] = list(validation.vvix_columns_present)
    vvix["missingness_state"] = validation.vvix_missingness
    if validation.vix_vvix_ok:
        vvix["model_consumption_state"] = "not_measured"
        vvix["pit_proof"] = "declared"
        vvix["why_not_used_or_sidelined"] = "vix_vvix_sensor_present_consumption_not_observed_in_screen"
    else:
        vvix["model_consumption_state"] = "sidelined_missing_data"
        vvix["pit_proof"] = "rejected" if validation.reasons else "pending"
        vvix["why_not_used_or_sidelined"] = ";".join(validation.reasons) or "vix_vvix_sensor_unavailable"
    feature_families["vix_vvix_sensor"] = vvix

    vopt = dict(feature_families.get("vix_options") or {})
    vopt["source_ids"] = [VIX_SENSOR_KEY]
    vopt["selected_features"] = list(validation.options_columns_present)
    vopt["missingness_state"] = validation.options_missingness
    if validation.proxy_only_columns:
        vopt["transformations"] = ["proxy_only_depth_imbalance"]
    if validation.vix_options_ok:
        vopt["model_consumption_state"] = "not_measured"
        vopt["pit_proof"] = "declared"
        why = "vix_options_present_consumption_not_observed_in_screen"
        if validation.proxy_only_columns:
            why += ";proxy_only_columns=" + ",".join(validation.proxy_only_columns)
        vopt["why_not_used_or_sidelined"] = why
    else:
        vopt["model_consumption_state"] = "sidelined_missing_data"
        vopt["pit_proof"] = "rejected" if validation.reasons else "pending"
        vopt["why_not_used_or_sidelined"] = ";".join(validation.reasons) or "vix_options_unavailable"
    feature_families["vix_options"] = vopt
