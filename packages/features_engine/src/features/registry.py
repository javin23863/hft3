"""Canonical feature registry and pipeline acceptance checks."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import importlib
from pathlib import Path
from typing import Any, Iterable

import yaml

from features_engine.src.model_registry import all_slugs, load_model_registry, resolve_model_id


CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
FEATURE_REGISTRY_PATH = CONFIG_DIR / "feature_registry.yaml"
MODEL_FEATURE_MAP_PATH = CONFIG_DIR / "model_feature_map.yaml"

REQUIRED_FIELDS = (
    "feature_id",
    "family",
    "subfamily",
    "lane_owner",
    "source_domain",
    "dtype",
    "unit",
    "shape",
    "timestamp_policy",
    "required_inputs",
    "source_tier_required",
    "pit_required",
    "allowed_consumer_lanes",
    "allowed_model_kinds",
    "status",
)

SOURCE_TIER_RANK = {
    "tier_0_reality": 0,
    "tier_1_primary": 1,
    "tier_2_vendor_normalized": 2,
    "tier_3_interpretive": 3,
    "tier_4_untrusted_context": 4,
}

ACTIVE_STATUSES = {"active", "research_only"}


@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    family: str
    subfamily: str
    lane_owner: str
    source_domain: str
    dtype: str
    unit: str
    shape: str
    timestamp_policy: str
    required_inputs: tuple[str, ...]
    source_tier_required: str
    pit_required: bool
    allowed_consumer_lanes: tuple[str, ...]
    allowed_model_kinds: tuple[str, ...]
    status: str
    aliases: tuple[str, ...] = ()
    feature_index_slot: int | None = None
    optional: bool = False
    asset: str = ""
    source_symbol: str = ""

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "FeatureSpec":
        feature_id = str(raw.get("feature_id", "")).strip()
        slot = raw.get("feature_index_slot")
        return cls(
            feature_id=feature_id,
            family=str(raw.get("family", "")).strip(),
            subfamily=str(raw.get("subfamily", "")).strip(),
            lane_owner=str(raw.get("lane_owner", "")).strip(),
            source_domain=str(raw.get("source_domain", "")).strip(),
            dtype=str(raw.get("dtype", "")).strip(),
            unit=str(raw.get("unit", "")).strip(),
            shape=str(raw.get("shape", "")).strip(),
            timestamp_policy=str(raw.get("timestamp_policy", "")).strip(),
            required_inputs=tuple(str(x).strip() for x in raw.get("required_inputs", []) if str(x).strip()),
            source_tier_required=str(raw.get("source_tier_required", "")).strip(),
            pit_required=bool(raw.get("pit_required", True)),
            allowed_consumer_lanes=tuple(
                str(x).strip() for x in raw.get("allowed_consumer_lanes", []) if str(x).strip()
            ),
            allowed_model_kinds=tuple(
                str(x).strip() for x in raw.get("allowed_model_kinds", []) if str(x).strip()
            ),
            status=str(raw.get("status", "")).strip(),
            aliases=tuple(str(x).strip() for x in raw.get("aliases", []) if str(x).strip()),
            feature_index_slot=int(slot) if slot is not None else None,
            optional=bool(raw.get("optional", False)),
            asset=str(raw.get("asset", "")).strip(),
            source_symbol=str(raw.get("source_symbol", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "feature_id": self.feature_id,
            "family": self.family,
            "subfamily": self.subfamily,
            "lane_owner": self.lane_owner,
            "source_domain": self.source_domain,
            "dtype": self.dtype,
            "unit": self.unit,
            "shape": self.shape,
            "timestamp_policy": self.timestamp_policy,
            "required_inputs": list(self.required_inputs),
            "source_tier_required": self.source_tier_required,
            "pit_required": self.pit_required,
            "allowed_consumer_lanes": list(self.allowed_consumer_lanes),
            "allowed_model_kinds": list(self.allowed_model_kinds),
            "status": self.status,
            "aliases": list(self.aliases),
            "optional": self.optional,
        }
        if self.feature_index_slot is not None:
            out["feature_index_slot"] = self.feature_index_slot
        if self.asset:
            out["asset"] = self.asset
        if self.source_symbol:
            out["source_symbol"] = self.source_symbol
        return out


@dataclass(frozen=True)
class FeatureAcceptance:
    feature_id: str
    status: str
    accepted: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "status": self.status,
            "accepted": self.accepted,
            "reason": "; ".join(self.reasons),
            "reasons": list(self.reasons),
        }


class FeatureRegistry:
    def __init__(self, specs: Iterable[FeatureSpec]) -> None:
        self._specs = {spec.feature_id: spec for spec in specs}
        aliases: dict[str, str] = {}
        for spec in self._specs.values():
            if spec.feature_id in aliases and aliases[spec.feature_id] != spec.feature_id:
                raise ValueError(f"Duplicate feature id/alias: {spec.feature_id}")
            aliases[spec.feature_id] = spec.feature_id
            for alias in spec.aliases:
                if alias in aliases and aliases[alias] != spec.feature_id:
                    raise ValueError(f"Duplicate feature alias {alias}: {aliases[alias]} vs {spec.feature_id}")
                aliases[alias] = spec.feature_id
        self._aliases = aliases

    def all_specs(self) -> list[FeatureSpec]:
        return [self._specs[key] for key in sorted(self._specs)]

    def specs_for_lane(self, lane_owner: str) -> list[FeatureSpec]:
        return [spec for spec in self.all_specs() if spec.lane_owner == lane_owner]

    def resolve(self, feature_id: str) -> FeatureSpec:
        canonical = self._aliases.get(feature_id)
        if canonical is None:
            raise KeyError(f"Unknown feature_id: {feature_id}")
        return self._specs[canonical]

    def canonical_id(self, feature_id: str) -> str:
        return self.resolve(feature_id).feature_id

    def accept(
        self,
        feature_id: str,
        *,
        consumer_lane: str,
        source_lane: str = "",
        model_kind: str = "",
        pit_safe: bool = True,
        source_tier: str = "tier_0_reality",
        required_inputs_available: bool = True,
    ) -> FeatureAcceptance:
        try:
            spec = self.resolve(feature_id)
        except KeyError:
            return FeatureAcceptance(feature_id, "REJECTED", False, ("UNREGISTERED_FEATURE",))

        if spec.status == "unavailable":
            return FeatureAcceptance(spec.feature_id, "UNAVAILABLE", False, ("FEATURE_UNAVAILABLE",))
        if spec.status not in ACTIVE_STATUSES:
            return FeatureAcceptance(spec.feature_id, "REJECTED", False, (f"STATUS_{spec.status.upper()}",))

        reasons: list[str] = []
        if source_lane and source_lane != spec.lane_owner:
            reasons.append("WRONG_SOURCE_LANE")
        if not _lane_allowed(consumer_lane, spec.allowed_consumer_lanes):
            reasons.append("WRONG_CONSUMER_LANE")
        if model_kind and not _lane_allowed(model_kind, spec.allowed_model_kinds):
            reasons.append("WRONG_MODEL_KIND")
        if spec.pit_required and not pit_safe:
            reasons.append("PIT_UNSAFE")
        if not _source_tier_ok(source_tier, spec.source_tier_required):
            reasons.append("SOURCE_TIER_INSUFFICIENT")
        if spec.required_inputs and not required_inputs_available:
            if spec.optional:
                return FeatureAcceptance(spec.feature_id, "UNAVAILABLE", False, ("REQUIRED_INPUTS_UNAVAILABLE",))
            reasons.append("REQUIRED_INPUTS_MISSING")

        if reasons:
            return FeatureAcceptance(spec.feature_id, "REJECTED", False, tuple(reasons))
        return FeatureAcceptance(spec.feature_id, "ACCEPTED", True, ("ACCEPTED",))


def _lane_allowed(value: str, allowed: tuple[str, ...]) -> bool:
    return "all_lanes" in allowed or "all" in allowed or value in allowed


def _source_tier_ok(actual: str, required: str) -> bool:
    if actual not in SOURCE_TIER_RANK or required not in SOURCE_TIER_RANK:
        return False
    return SOURCE_TIER_RANK[actual] <= SOURCE_TIER_RANK[required]


@lru_cache(maxsize=1)
def load_feature_registry() -> FeatureRegistry:
    return FeatureRegistry(_load_feature_specs(FEATURE_REGISTRY_PATH))


@lru_cache(maxsize=1)
def load_model_feature_map() -> dict[str, Any]:
    return _read_yaml(MODEL_FEATURE_MAP_PATH)


def feature_ids_for_model(model_id: str) -> list[str]:
    slug = resolve_model_id(model_id)
    payload = load_model_feature_map()
    registry = load_feature_registry()
    explicit = ((payload.get("models") or {}).get(slug) or {}).get("feature_ids")
    if explicit:
        return [registry.canonical_id(str(feature_id)) for feature_id in explicit]
    model_entry = (load_model_registry().get("models") or {}).get(slug, {})
    kind = str(model_entry.get("kind") or "")
    default_ids = ((payload.get("defaults") or {}).get(kind) or {}).get("feature_ids") or []
    return [registry.canonical_id(str(feature_id)) for feature_id in default_ids]


def validate_feature_registry() -> list[str]:
    raw_specs = _load_feature_specs(FEATURE_REGISTRY_PATH)
    errors: list[str] = []
    seen_ids: set[str] = set()
    alias_to_id: dict[str, str] = {}
    model_slugs = set(all_slugs())
    for spec in raw_specs:
        raw = spec.to_dict()
        for field in REQUIRED_FIELDS:
            value = raw.get(field)
            if value in ("", [], None):
                errors.append(f"{spec.feature_id or '<missing>'}: missing {field}")
        if spec.feature_id in model_slugs:
            errors.append(f"{spec.feature_id}: feature_id overlaps model_id")
        if spec.feature_id in seen_ids:
            errors.append(f"duplicate feature_id: {spec.feature_id}")
        seen_ids.add(spec.feature_id)
        if spec.source_tier_required not in SOURCE_TIER_RANK:
            errors.append(f"{spec.feature_id}: bad source_tier_required={spec.source_tier_required}")
        for alias in (spec.feature_id, *spec.aliases):
            if alias in model_slugs:
                errors.append(f"{spec.feature_id}: alias overlaps model_id {alias}")
            prior = alias_to_id.get(alias)
            if prior is not None and prior != spec.feature_id:
                errors.append(f"duplicate alias {alias}: {prior} vs {spec.feature_id}")
            alias_to_id[alias] = spec.feature_id
    return errors


def validate_model_feature_map() -> list[str]:
    errors: list[str] = []
    payload = load_model_feature_map()
    registry = load_feature_registry()
    models = load_model_registry().get("models") or {}
    known_models = set(models)
    for model_id, cfg in (payload.get("models") or {}).items():
        if model_id not in known_models:
            errors.append(f"unknown model_id in model_feature_map: {model_id}")
        model_kind = str((models.get(model_id) or {}).get("kind") or "")
        for feature_id in cfg.get("feature_ids") or []:
            try:
                spec = registry.resolve(str(feature_id))
            except KeyError:
                errors.append(f"{model_id}: unknown feature_id {feature_id}")
                continue
            acceptance = registry.accept(
                spec.feature_id,
                consumer_lane="all_lanes",
                source_lane=spec.lane_owner,
                model_kind=model_kind,
                pit_safe=True,
                source_tier=spec.source_tier_required,
                required_inputs_available=True,
            )
            if not acceptance.accepted:
                errors.append(f"{model_id}: feature_id {feature_id} not eligible: {';'.join(acceptance.reasons)}")
    for kind, cfg in (payload.get("defaults") or {}).items():
        for feature_id in cfg.get("feature_ids") or []:
            try:
                spec = registry.resolve(str(feature_id))
            except KeyError:
                errors.append(f"default {kind}: unknown feature_id {feature_id}")
                continue
            acceptance = registry.accept(
                spec.feature_id,
                consumer_lane="all_lanes",
                source_lane=spec.lane_owner,
                model_kind=str(kind),
                pit_safe=True,
                source_tier=spec.source_tier_required,
                required_inputs_available=True,
            )
            if not acceptance.accepted:
                errors.append(f"default {kind}: feature_id {feature_id} not eligible: {';'.join(acceptance.reasons)}")
    return errors


def _load_feature_specs(path: Path) -> list[FeatureSpec]:
    payload = _read_yaml(path)
    specs: list[FeatureSpec] = []
    for group in payload.get("feature_groups") or []:
        specs.extend(_expand_group(group))
    return specs


def _expand_group(group: dict[str, Any]) -> list[FeatureSpec]:
    kind = str(group.get("kind") or "explicit")
    base = {key: value for key, value in group.items() if key not in {"features", "kind", "module", "constant", "class"}}
    if kind == "feature_index":
        return _feature_index_specs(base)
    if kind == "python_constant":
        values = _load_python_constant(str(group["module"]), str(group["constant"]))
        return [_spec_from_name(base, str(value)) for value in values]
    if kind == "dataclass_fields":
        cls = _load_python_constant(str(group["module"]), str(group["class"]))
        return [_spec_from_name(base, str(name)) for name in getattr(cls, "__dataclass_fields__", {})]
    return [FeatureSpec.from_raw({**base, **dict(feature)}) for feature in group.get("features") or []]


def _feature_index_specs(base: dict[str, Any]) -> list[FeatureSpec]:
    from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX, REGIME_INDEX_MAP, FeatureIndex

    specs: list[FeatureSpec] = []
    mapped_slots = {int(slot) for slot in FEATURE_NAME_TO_INDEX.values()} | {int(slot) for slot in REGIME_INDEX_MAP.values()}
    for name, slot in sorted(FEATURE_NAME_TO_INDEX.items(), key=lambda item: int(item[1])):
        raw = {**base, **_feature_index_category(name)}
        raw.update(
            {
                "feature_id": f"mbo.{raw['family']}.{name}",
                "aliases": [name],
                "feature_index_slot": int(slot),
            }
        )
        specs.append(FeatureSpec.from_raw(raw))
    for name, slot in sorted(REGIME_INDEX_MAP.items(), key=lambda item: int(item[1])):
        raw = {**base, "family": "regime", "subfamily": "mbo_regime_state"}
        aliases = [f"regime_{name}"] if name in FEATURE_NAME_TO_INDEX else [name, f"regime_{name}"]
        raw.update(
            {
                "feature_id": f"mbo.regime.{name}",
                "aliases": aliases,
                "feature_index_slot": int(slot),
            }
        )
        specs.append(FeatureSpec.from_raw(raw))
    for member in FeatureIndex:
        slot = int(member)
        if member.name == "FEATURE_DIM" or slot in mapped_slots:
            continue
        name = member.name.lower()
        raw = {**base, **_feature_index_category(name)}
        raw.update(
            {
                "feature_id": f"mbo.{raw['family']}.{name}",
                "aliases": [name],
                "feature_index_slot": slot,
            }
        )
        specs.append(FeatureSpec.from_raw(raw))
    return specs


def _feature_index_category(name: str) -> dict[str, str]:
    if name.startswith("top_") or name in {
        "book_slope",
        "book_slope_change",
        "spread",
        "spread_stress",
        "mid_price",
    }:
        return {"family": "depth", "subfamily": "mbo_book_state"}
    if "queue" in name or name == "refill_ratio":
        return {"family": "queue", "subfamily": "mbo_queue_state"}
    if any(token in name for token in ("vacuum", "absorption", "iceberg", "reload")):
        return {"family": "liquidity_quality", "subfamily": "mbo_liquidity_quality"}
    if name.startswith("regime") or name in {
        "normal",
        "event_shock",
        "liquidity_vacuum",
        "stop_cascade",
        "prop_flatten",
        "book_rebuild",
        "chop",
        "trend_continuation",
        "spread_stress",
    }:
        return {"family": "regime", "subfamily": "mbo_regime_state"}
    if any(token in name for token in ("distance", "cutoff", "prop_", "news_", "breaking", "max_contract")):
        return {"family": "execution", "subfamily": "session_context"}
    return {"family": "flow", "subfamily": "mbo_order_flow"}


def _spec_from_name(base: dict[str, Any], name: str) -> FeatureSpec:
    prefix = str(base.get("feature_id_prefix") or base.get("lane_owner") or "feature").strip(".")
    raw = dict(base)
    raw.pop("feature_id_prefix", None)
    raw["feature_id"] = f"{prefix}.{name}"
    raw["aliases"] = [raw["feature_id"]]
    return FeatureSpec.from_raw(raw)


def _load_python_constant(module_name: str, attr: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
