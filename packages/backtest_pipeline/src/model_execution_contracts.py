"""Central semantic-execution contract for every canonical model slug.

No-cherry-pick v2 (refines the 2026-06-29 all-model uniform-flow rule):

    Every canonical slug appears in the manifest/evidence ledger.
    Only semantically executable standalone strategies enter the standalone
    HBT order queue. Non-standalone slugs emit an explicit semantic blocker or
    composition-only receipt. No omitted rows. No fake standalone PnL.

This module is the single source of truth for *execution role*. It derives
each slug's role from the authoritative catalog/registry data rather than a
hand-maintained parallel taxonomy:

  - kind + role + blocks_trade + target/valid universe from the model registry;
  - role / blocks_trade / requires from the workbench catalog;
  - the PDF structural role sets (previously stranded in
    ``pipeline_model_router``) are re-homed here and re-exported, so the router
    and the manifest read one taxonomy, not two;
  - required leader / sensor tapes from ``replay.cross_asset_assembly``.

Fail-closed: any slug that cannot be classified raises, and any slug absent
from the registry has no contract. Callers must treat a missing contract as
``unknown_semantic_contract`` rather than defaulting to standalone alpha.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import yaml

from features_engine.src.model_registry import all_slugs, load_model_registry, resolve_model_id
from replay.cross_asset_assembly import required_leaders_for_model, required_sensors_for_model

REPO_ROOT = Path(__file__).resolve().parents[3]
_CATALOG_PATH = REPO_ROOT / "apps" / "workbench" / "config" / "model_catalog.yaml"

# ---------------------------------------------------------------------------
# PDF structural role sets — single source of truth (re-homed from
# pipeline_model_router, which now imports these). Keep membership here.
# ---------------------------------------------------------------------------
PDF_STRUCTURAL_EVAL = frozenset(
    {"BOOK_PRESSURE", "CROSS_ASSET_LEAD_LAG", "VPIN_TOXICITY", "DOW_YM_INDEX", "TRANSFER_ENTROPY", "STOCHASTIC_THERMO"}
)
PDF_DIAGNOSTICS = frozenset({"TREASURY_CTD", "QUANTUM_SPREAD_DEFENSE", "HAWKES_TOXIC_FLOW"})
PDF_HYBRID_REPLAY = frozenset({"HYBRID_EXECUTION"})
PDF_OPTIONS_FIXTURE = frozenset({"DEALER_HEDGING"})

# Execution roles (semantic surface a slug is allowed to run on).
EXECUTION_ROLES = frozenset(
    {
        "primary_alpha",
        "cross_asset_primary_alpha",
        "sensor_conditioned_primary_alpha",
        "defensive_overlay",
        "execution_engine",
        "context_feature",
        "options_fixture",
        "rl_research_blocked",
    }
)

# Standalone HBT policies (whether/how a slug may enter the order queue).
STANDALONE_POLICIES = frozenset(
    {
        "standalone_executable",
        "requires_leader_tape",
        "requires_sensor_tape",
        "composition_only",
        "diagnostic_only",
        "blocked_not_order_strategy",
    }
)

# Roles whose policy permits entering the standalone order queue *once tape
# requirements (if any) are satisfied*. Tape presence is checked downstream by
# the manifest (leader_tape_missing / sensor_tape_missing) — this set is purely
# about semantic admissibility.
_STANDALONE_CAPABLE_POLICIES = frozenset(
    {"standalone_executable", "requires_leader_tape", "requires_sensor_tape"}
)

_ROLE_TO_POLICY = {
    "primary_alpha": "standalone_executable",
    "cross_asset_primary_alpha": "requires_leader_tape",
    "sensor_conditioned_primary_alpha": "requires_sensor_tape",
    "defensive_overlay": "composition_only",
    "execution_engine": "composition_only",
    "context_feature": "diagnostic_only",
    "options_fixture": "diagnostic_only",
    "rl_research_blocked": "blocked_not_order_strategy",
}


@dataclass(frozen=True)
class ModelExecutionContract:
    canonical_model_id: str
    kind: str
    execution_role: str
    standalone_hbt_policy: str
    valid_instrument_universe: tuple[str, ...]
    target_instrument_universe: tuple[str, ...]
    required_leaders: tuple[str, ...]
    required_sensors: tuple[str, ...]
    requires_models: tuple[str, ...]
    blocks_trade: bool
    authority_refs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_standalone_alpha(self) -> bool:
        """True if the slug may (semantically) enter the standalone order queue.

        Leader/sensor tape presence is still enforced downstream; this only
        says the *role* is order-producing.
        """
        return self.standalone_hbt_policy in _STANDALONE_CAPABLE_POLICIES


@lru_cache(maxsize=1)
def _load_catalog() -> Mapping[str, Mapping[str, object]]:
    if not _CATALOG_PATH.exists():
        return {}
    data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8")) or {}
    models = data.get("models") if isinstance(data, dict) and "models" in data else data
    return models if isinstance(models, dict) else {}


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def _role_of(slug: str, kind: str, registry_entry: Mapping[str, object], catalog_entry: Mapping[str, object]) -> str:
    """Derive execution_role by precedence over authoritative sources."""
    catalog_role = str(catalog_entry.get("role") or registry_entry.get("role") or "").strip().lower()
    blocks_trade = bool(catalog_entry.get("blocks_trade") or registry_entry.get("blocks_trade"))
    is_defensive = blocks_trade or catalog_role == "defensive"

    if kind == "reinforcement_learning":
        return "rl_research_blocked"
    if slug in PDF_HYBRID_REPLAY:
        return "execution_engine"
    if slug in PDF_OPTIONS_FIXTURE:
        return "options_fixture"
    if kind == "pdf_structural":
        # Structural payloads never become standalone order signals. Defensive
        # ones gate execution (composition); the rest are context/diagnostic.
        return "defensive_overlay" if is_defensive else "context_feature"
    if kind == "hypothesis":
        if is_defensive:
            return "defensive_overlay"
        if required_sensors_for_model(slug):
            return "sensor_conditioned_primary_alpha"
        if _as_tuple(registry_entry.get("target_instrument_universe")) or required_leaders_for_model(slug):
            return "cross_asset_primary_alpha"
        return "primary_alpha"
    raise ValueError(f"unknown_semantic_contract: slug={slug!r} kind={kind!r}")


def _contract_from_entry(
    slug: str, entry: Mapping[str, object], catalog_entry: Mapping[str, object]
) -> ModelExecutionContract:
    kind = str(entry.get("kind") or "").strip()
    role = _role_of(slug, kind, entry, catalog_entry)
    policy = _ROLE_TO_POLICY[role]
    requires = _as_tuple(catalog_entry.get("requires") or entry.get("requires"))
    return ModelExecutionContract(
        canonical_model_id=slug,
        kind=kind,
        execution_role=role,
        standalone_hbt_policy=policy,
        valid_instrument_universe=_as_tuple(entry.get("valid_instrument_universe")),
        target_instrument_universe=_as_tuple(entry.get("target_instrument_universe")),
        required_leaders=required_leaders_for_model(slug),
        required_sensors=required_sensors_for_model(slug),
        requires_models=requires,
        blocks_trade=bool(catalog_entry.get("blocks_trade") or entry.get("blocks_trade")),
        authority_refs=_as_tuple(entry.get("authority_refs")),
    )


@lru_cache(maxsize=1)
def _build_all() -> dict[str, ModelExecutionContract]:
    registry_models = load_model_registry().get("models", {})
    catalog = _load_catalog()
    contracts: dict[str, ModelExecutionContract] = {}
    for slug in all_slugs():
        entry = registry_models.get(slug, {}) or {}
        catalog_entry = catalog.get(slug, {}) or {}
        contracts[slug] = _contract_from_entry(slug, entry, catalog_entry)
    return contracts


def contract_for(
    model_id: str, registry_entry: Mapping[str, object] | None = None
) -> ModelExecutionContract:
    """Contract for a slug, optionally derived from a caller-supplied registry entry.

    When ``registry_entry`` is given (e.g. the manifest builder's own
    ``registry_path`` models), the contract is derived from THAT entry so the
    semantic verdict stays consistent with the registry the caller is using —
    the id is not resolved against the global registry. When omitted, falls back
    to the global registry lookup (raises KeyError on an unknown slug).

    CAVEAT (Greptile P1, PR #73): with ``registry_entry`` supplied, a slug
    absent from the canonical registry is classified from its declared kind —
    the caller's registry is treated as the run's authority. The runner's
    defense-in-depth guard (``_semantic_guard_reasons``) closes the gap: it
    consults the CANONICAL registry only and fail-closes unknown slugs with
    ``semantic_blocker:unknown_semantic_contract`` before any standalone run.
    """
    if registry_entry is None:
        return model_execution_contract(model_id)
    slug = str(model_id).strip()
    catalog_entry = _load_catalog().get(slug, {}) or {}
    return _contract_from_entry(slug, registry_entry, catalog_entry)


def all_contracts() -> dict[str, ModelExecutionContract]:
    """Contract for every canonical registry slug. Coverage == all_slugs()."""
    return dict(_build_all())


def model_execution_contract(model_id: str) -> ModelExecutionContract:
    """Contract for one slug (accepts canonical or legacy id).

    Raises KeyError for an unknown id — callers must fail closed with
    ``unknown_semantic_contract`` rather than assume standalone alpha.
    """
    slug = resolve_model_id(model_id)
    contracts = _build_all()
    if slug not in contracts:
        raise KeyError(f"unknown_semantic_contract: {model_id!r}")
    return contracts[slug]


def execution_role(model_id: str) -> str:
    return model_execution_contract(model_id).execution_role


def standalone_hbt_policy(model_id: str) -> str:
    return model_execution_contract(model_id).standalone_hbt_policy


def is_standalone_alpha(model_id: str) -> bool:
    return model_execution_contract(model_id).is_standalone_alpha
