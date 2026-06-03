"""Offline-only relationship reasoning candidates for research review.

This module is intentionally separate from OpenFoundry operational ontology and
per-LLM closed-claim KG annotations. It proposes reviewable candidates only; it
does not persist, call a network, invoke LLMs, or write to a KG.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path


class RelationshipContext(str, Enum):
    MICRO = "micro"
    MACRO = "macro"
    WORLD_EVENT = "world_event"
    REGIME = "regime"


class RelationshipStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED = "rejected"


class RelationshipDataSource(str, Enum):
    DATABENTO_CME_MBO_NPZ = "databento_cme_mbo_npz"
    MICROSTRUCTURE_PDF_MANIFEST = "microstructure_pdf_manifest"
    ECONOMIC_EVENT_UNIVERSE = "economic_event_universe"
    SOURCED_RELEASE_CALENDAR = "sourced_release_calendar"
    DATA_SYSTEM_EVENTS_CSV = "data_system_events_csv"
    FEATURES_ENGINE_REGIME_FILTER = "features_engine_regime_filter"
    EVENT_CONTEXT_REGIME_MAP = "event_context_regime_map"
    GDELT_WORLD_EVENTS = "gdelt_world_events"


ALLOWED_PROMOTION_TARGETS = frozenset(
    {
        "kg_relation_review_candidate",
        "openfoundry_relation_review_candidate",
    }
)


@dataclass(frozen=True)
class RelationshipSourceDefinition:
    source_type: RelationshipDataSource
    contexts: tuple[RelationshipContext, ...]
    path: str
    authority: str
    notes: str
    empirical: bool


SOURCE_DEFINITIONS = {
    RelationshipDataSource.DATABENTO_CME_MBO_NPZ: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.DATABENTO_CME_MBO_NPZ,
        contexts=(RelationshipContext.MICRO,),
        path="data/npz/<symbol>_<event_id>_mbo.npz",
        authority="Offline CME MBO replay observations from Databento GLBX.MDP3.",
        notes="Resolved by canonical event replay; never Rithmic trial data for historical research.",
        empirical=True,
    ),
    RelationshipDataSource.MICROSTRUCTURE_PDF_MANIFEST: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.MICROSTRUCTURE_PDF_MANIFEST,
        contexts=(RelationshipContext.MICRO,),
        path="docs/references/MANIFEST.md",
        authority="Citation authority for microstructure concepts, not raw observations.",
        notes="Grounds terms such as marked events, queue estimates, latency chains, and event context.",
        empirical=False,
    ),
    RelationshipDataSource.ECONOMIC_EVENT_UNIVERSE: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.ECONOMIC_EVENT_UNIVERSE,
        contexts=(RelationshipContext.MACRO, RelationshipContext.REGIME),
        path="packages/economic_event_universe/config/event_universe.yaml",
        authority="Authoritative macro event catalog metadata for offline research.",
        notes="Defines anchors, windows, labels, official source URLs, and research readiness status.",
        empirical=False,
    ),
    RelationshipDataSource.SOURCED_RELEASE_CALENDAR: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.SOURCED_RELEASE_CALENDAR,
        contexts=(RelationshipContext.MACRO, RelationshipContext.REGIME),
        path="packages/data_system/config/release_calendars/*.csv",
        authority="Official macro release rows only when row_status is SOURCED.",
        notes="Dates are maintained from cited source_url values; Python must not invent them.",
        empirical=True,
    ),
    RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV,
        contexts=(RelationshipContext.MACRO, RelationshipContext.REGIME),
        path="packages/data_system/config/events.csv",
        authority="Canonical replay event_id and window artifact built from sourced calendars.",
        notes="Research replay resolves event windows from this file, never from folder dates.",
        empirical=True,
    ),
    RelationshipDataSource.FEATURES_ENGINE_REGIME_FILTER: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.FEATURES_ENGINE_REGIME_FILTER,
        contexts=(RelationshipContext.REGIME,),
        path="packages/features_engine/src/regime/regime_filter.py",
        authority="Model-derived regime posterior input for review, not an external fact source.",
        notes="Defines regime labels and posterior logic used by offline analysis.",
        empirical=False,
    ),
    RelationshipDataSource.EVENT_CONTEXT_REGIME_MAP: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.EVENT_CONTEXT_REGIME_MAP,
        contexts=(RelationshipContext.REGIME,),
        path="packages/features_engine/config/event_context_regime.json",
        authority="Configured event-context to regime-boost mapping.",
        notes="Grounds configured boosts such as event_shock and prop_flatten; not a causal proof.",
        empirical=False,
    ),
    RelationshipDataSource.GDELT_WORLD_EVENTS: RelationshipSourceDefinition(
        source_type=RelationshipDataSource.GDELT_WORLD_EVENTS,
        contexts=(RelationshipContext.WORLD_EVENT,),
        path="artifacts/world_events/gdelt/events/<YYYYMMDD>.jsonl:<GLOBALEVENTID>",
        authority="Offline cached GDELT 2.1 event record with actor, event, location, and source URL provenance.",
        notes="Backend-only world-event source for geopolitical/event relationship review; no UI or trading path.",
        empirical=True,
    ),
}


@dataclass(frozen=True)
class RelationshipEvidence:
    description: str
    source_type: RelationshipDataSource | str
    source_ref: str
    confidence: float | None = None


@dataclass(frozen=True)
class RelationshipCandidate:
    subject: str
    predicate: str
    object: str
    context: RelationshipContext
    status: RelationshipStatus = RelationshipStatus.PROPOSED
    evidence: tuple[RelationshipEvidence, ...] = field(default_factory=tuple)
    proof_trace: tuple[str, ...] = field(default_factory=tuple)
    rationale: str | None = None
    rejection_reason: str | None = None
    authoritative: bool = False
    offline_only: bool = True
    openfoundry_written: bool = False
    kg_written: bool = False


def propose_relationship(
    subject: str,
    predicate: str,
    object: str,
    context: RelationshipContext | str,
    *,
    evidence: tuple[RelationshipEvidence, ...] | list[RelationshipEvidence] = (),
    proof_trace: tuple[str, ...] | list[str] = (),
    rationale: str | None = None,
) -> RelationshipCandidate:
    return RelationshipCandidate(
        subject=subject,
        predicate=predicate,
        object=object,
        context=RelationshipContext(context),
        evidence=tuple(evidence),
        proof_trace=tuple(proof_trace),
        rationale=rationale,
    )


def validate_relationship_candidate(
    candidate: RelationshipCandidate,
    *,
    repo_root: Path | None = None,
) -> tuple[str, ...]:
    errors: list[str] = []
    allowed_definitions = {
        definition.source_type: definition
        for definition in defined_sources_for_context(candidate.context)
    }
    allowed_sources = set(allowed_definitions)
    if not allowed_sources:
        errors.append(f"context {candidate.context.value} has no defined relationship data sources")
    if not candidate.subject.strip():
        errors.append("subject is required")
    if not candidate.predicate.strip():
        errors.append("predicate is required")
    if not candidate.object.strip():
        errors.append("object is required")
    if not candidate.evidence:
        errors.append("at least one evidence item is required")
    has_empirical_source = False
    for index, item in enumerate(candidate.evidence):
        if not item.description.strip():
            errors.append(f"evidence[{index}].description is required")
        if not item.source_ref.strip():
            errors.append(f"evidence[{index}].source_ref is required")
        try:
            source_type = RelationshipDataSource(item.source_type)
        except ValueError:
            errors.append(f"evidence[{index}].source_type is not defined")
        else:
            if source_type not in allowed_sources:
                errors.append(
                    f"evidence[{index}].source_type {source_type.value} "
                    f"is not valid for context {candidate.context.value}"
                )
            else:
                definition = allowed_definitions[source_type]
                errors.extend(_source_ref_errors(index, definition, item.source_ref, repo_root=repo_root))
                has_empirical_source = has_empirical_source or definition.empirical
        if item.confidence is not None and not 0.0 <= item.confidence <= 1.0:
            errors.append(f"evidence[{index}].confidence must be between 0 and 1")
    if candidate.evidence and not has_empirical_source:
        errors.append("at least one empirical offline evidence source is required")
    if not candidate.proof_trace:
        errors.append("proof_trace is required")
    for index, step in enumerate(candidate.proof_trace):
        if not step.strip():
            errors.append(f"proof_trace[{index}] is required")
    if candidate.authoritative:
        errors.append("candidate must remain non-authoritative")
    if not candidate.offline_only:
        errors.append("candidate must remain offline-only")
    if candidate.openfoundry_written:
        errors.append("candidate must not claim an OpenFoundry write")
    if candidate.kg_written:
        errors.append("candidate must not claim a KG write")
    return tuple(errors)


def defined_sources_for_context(
    context: RelationshipContext | str,
) -> tuple[RelationshipSourceDefinition, ...]:
    relationship_context = RelationshipContext(context)
    return tuple(
        definition
        for definition in SOURCE_DEFINITIONS.values()
        if relationship_context in definition.contexts
    )


def _source_ref_errors(
    index: int,
    definition: RelationshipSourceDefinition,
    source_ref: str,
    *,
    repo_root: Path | None,
) -> list[str]:
    source_type = definition.source_type
    if source_type == RelationshipDataSource.DATABENTO_CME_MBO_NPZ:
        if not re.fullmatch(r"data/npz/[^/\s]+_[^/\s]+_mbo\.npz", source_ref):
            return [f"evidence[{index}].source_ref must match data/npz/<symbol>_<event_id>_mbo.npz"]
        return []
    if source_type == RelationshipDataSource.MICROSTRUCTURE_PDF_MANIFEST:
        if not re.fullmatch(r"docs/references/MANIFEST\.md:[A-Za-z0-9_.-]+", source_ref):
            return [f"evidence[{index}].source_ref must start with docs/references/MANIFEST.md:"]
        return []
    if source_type == RelationshipDataSource.ECONOMIC_EVENT_UNIVERSE:
        if not re.fullmatch(
            r"packages/economic_event_universe/config/event_universe\.yaml:[A-Z0-9_]+",
            source_ref,
        ):
            return [
                f"evidence[{index}].source_ref must start with "
                "packages/economic_event_universe/config/event_universe.yaml:"
            ]
        return []
    if source_type == RelationshipDataSource.SOURCED_RELEASE_CALENDAR:
        if not re.fullmatch(
            r"packages/data_system/config/release_calendars/[A-Za-z0-9_.-]+\.csv:"
            r"row_status=SOURCED:[A-Z0-9_]+",
            source_ref,
        ):
            return [
                f"evidence[{index}].source_ref must identify a release_calendars/*.csv "
                "row with row_status=SOURCED"
            ]
        return []
    if source_type == RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV:
        if not re.fullmatch(r"packages/data_system/config/events\.csv:[A-Z0-9_]+", source_ref):
            return [f"evidence[{index}].source_ref must start with packages/data_system/config/events.csv:"]
        return []
    if source_type == RelationshipDataSource.FEATURES_ENGINE_REGIME_FILTER:
        if not re.fullmatch(
            r"packages/features_engine/src/regime/regime_filter\.py:[a-z_]+",
            source_ref,
        ):
            return [
                f"evidence[{index}].source_ref must start with "
                "packages/features_engine/src/regime/regime_filter.py:"
            ]
        return []
    if source_type == RelationshipDataSource.EVENT_CONTEXT_REGIME_MAP:
        if not re.fullmatch(
            r"packages/features_engine/config/event_context_regime\.json:[a-z_]+",
            source_ref,
        ):
            return [
                f"evidence[{index}].source_ref must start with "
                "packages/features_engine/config/event_context_regime.json:"
            ]
        return []
    if source_type == RelationshipDataSource.GDELT_WORLD_EVENTS:
        match = re.fullmatch(
            r"artifacts/world_events/gdelt/events/[0-9]{8}\.jsonl:[0-9]+",
            source_ref,
        )
        if not match:
            return [
                f"evidence[{index}].source_ref must match "
                "artifacts/world_events/gdelt/events/<YYYYMMDD>.jsonl:<GLOBALEVENTID>"
            ]
        if repo_root is None:
            return [f"evidence[{index}].repo_root is required to validate cached GDELT evidence"]
        rel_path, event_id = source_ref.split(":", 1)
        cache_path = repo_root / rel_path
        if not cache_path.is_file():
            return [f"evidence[{index}].source_ref cache file is missing"]
        return _gdelt_cache_record_errors(index, cache_path, event_id)
    return [f"evidence[{index}].source_type has no source_ref rule"]


def _gdelt_cache_record_errors(index: int, cache_path: Path, event_id: str) -> list[str]:
    from research_pipeline.world_events.gdelt import GDELT_PROVIDER_VERSION
    from research_pipeline.world_events.models import WorldEventSource

    try:
        rows = [json.loads(line) for line in cache_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"evidence[{index}].source_ref cache file is unreadable: {exc}"]

    for row in rows:
        if str(row.get("event_id", "")) != event_id:
            continue
        if str(row.get("event_date", "")) != cache_path.stem:
            return [f"evidence[{index}].source_ref cache date does not match record date"]
        provenance = row.get("provenance") or {}
        if provenance.get("provider") != WorldEventSource.GDELT_EVENTS.value:
            return [f"evidence[{index}].source_ref record provider is not GDELT"]
        if provenance.get("provider_version") != GDELT_PROVIDER_VERSION:
            return [f"evidence[{index}].source_ref record provider version is not GDELT 2.1 events"]
        if not provenance.get("source_url"):
            return [f"evidence[{index}].source_ref record is missing source_url provenance"]
        return []
    return [f"evidence[{index}].source_ref event_id was not found in cache file"]


def mark_validated(candidate: RelationshipCandidate, *, repo_root: Path | None = None) -> RelationshipCandidate:
    errors = validate_relationship_candidate(candidate, repo_root=repo_root)
    if errors:
        raise ValueError("; ".join(errors))
    if candidate.status == RelationshipStatus.REJECTED:
        raise ValueError("rejected candidate cannot be validated")
    return replace(candidate, status=RelationshipStatus.VALIDATED, rejection_reason=None)


def reject_relationship_candidate(
    candidate: RelationshipCandidate,
    reason: str,
) -> RelationshipCandidate:
    if not reason.strip():
        raise ValueError("rejection reason is required")
    return replace(
        candidate,
        status=RelationshipStatus.REJECTED,
        rejection_reason=reason,
    )


def build_promotion_record(
    candidate: RelationshipCandidate,
    promotion_target: str = "kg_relation_review_candidate",
    *,
    repo_root: Path | None = None,
) -> dict[str, object]:
    if candidate.status != RelationshipStatus.VALIDATED:
        raise ValueError("only validated relationship candidates can be promoted")
    if promotion_target not in ALLOWED_PROMOTION_TARGETS:
        raise ValueError("promotion_target must be a non-authoritative review candidate target")
    errors = validate_relationship_candidate(candidate, repo_root=repo_root)
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "record_type": "relationship_reasoning_promotion_record",
        "promotion_target": promotion_target,
        "subject": candidate.subject,
        "predicate": candidate.predicate,
        "object": candidate.object,
        "context": candidate.context.value,
        "status": candidate.status.value,
        "evidence": [
            {
                "description": item.description,
                "source_type": RelationshipDataSource(item.source_type).value,
                "source_ref": item.source_ref,
                "source_path": SOURCE_DEFINITIONS[RelationshipDataSource(item.source_type)].path,
                "source_authority": SOURCE_DEFINITIONS[RelationshipDataSource(item.source_type)].authority,
                "source_empirical": SOURCE_DEFINITIONS[RelationshipDataSource(item.source_type)].empirical,
                "confidence": item.confidence,
            }
            for item in candidate.evidence
        ],
        "proof_trace": list(candidate.proof_trace),
        "rationale": candidate.rationale,
        "authoritative": False,
        "offline_only": True,
        "openfoundry_written": False,
        "kg_written": False,
        "write_performed": False,
    }


__all__ = [
    "ALLOWED_PROMOTION_TARGETS",
    "SOURCE_DEFINITIONS",
    "RelationshipCandidate",
    "RelationshipContext",
    "RelationshipDataSource",
    "RelationshipEvidence",
    "RelationshipSourceDefinition",
    "RelationshipStatus",
    "build_promotion_record",
    "defined_sources_for_context",
    "mark_validated",
    "propose_relationship",
    "reject_relationship_candidate",
    "validate_relationship_candidate",
]
