from __future__ import annotations

import json

import pytest

from research_pipeline.relationship_reasoning import (
    ALLOWED_PROMOTION_TARGETS,
    RelationshipCandidate,
    RelationshipContext,
    RelationshipDataSource,
    RelationshipEvidence,
    RelationshipStatus,
    build_promotion_record,
    defined_sources_for_context,
    mark_validated,
    propose_relationship,
    reject_relationship_candidate,
    validate_relationship_candidate,
)
from research_pipeline.world_events import (
    parse_gdelt_events_payload,
    source_ref_for_world_event,
    write_world_event_cache,
)


def _write_gdelt_cache(tmp_path):
    records = parse_gdelt_events_payload(
        {
            "events": [
                {
                    "GLOBALEVENTID": "123456789",
                    "SQLDATE": "20240601",
                    "Actor1Name": "UNITED STATES",
                    "Actor2Name": "CHINA",
                    "EventCode": "042",
                    "SOURCEURL": "https://example.test/world-event-source",
                }
            ]
        },
        request_url="https://api.gdeltproject.org/api/v2/events/events?query=x",
        fetched_at_utc="2026-06-03T00:00:00+00:00",
    )
    written = write_world_event_cache(tmp_path, records)
    return source_ref_for_world_event(tmp_path, written[0], records[0])


def _gdelt_record_dict():
    records = parse_gdelt_events_payload(
        {
            "events": [
                {
                    "GLOBALEVENTID": "123456789",
                    "SQLDATE": "20240601",
                    "Actor1Name": "UNITED STATES",
                    "Actor2Name": "CHINA",
                    "EventCode": "042",
                    "SOURCEURL": "https://example.test/world-event-source",
                }
            ]
        },
        request_url="https://api.gdeltproject.org/api/v2/events/events?query=x",
        fetched_at_utc="2026-06-03T00:00:00+00:00",
    )
    return records[0].to_dict()


def _write_gdelt_cache_row(tmp_path, cache_date: str, row: dict) -> None:
    path = tmp_path / "artifacts" / "world_events" / "gdelt" / "events" / f"{cache_date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_proposal_starts_proposed_non_authoritative_offline_only():
    candidate = propose_relationship(
        "CPI surprise",
        "may_expand",
        "MES spread volatility",
        RelationshipContext.MACRO,
    )

    assert candidate.status == RelationshipStatus.PROPOSED
    assert candidate.authoritative is False
    assert candidate.offline_only is True
    assert candidate.openfoundry_written is False
    assert candidate.kg_written is False


def test_missing_evidence_or_proof_cannot_validate_or_promote():
    candidate = propose_relationship(
        "queue imbalance",
        "may_precede",
        "short horizon price pressure",
        RelationshipContext.MICRO,
    )

    errors = validate_relationship_candidate(candidate)

    assert "at least one evidence item is required" in errors
    assert "proof_trace is required" in errors
    with pytest.raises(ValueError, match="evidence"):
        mark_validated(candidate)
    with pytest.raises(ValueError, match="only validated"):
        build_promotion_record(candidate)


def test_validated_candidate_with_evidence_and_proof_promotes_non_authoritatively():
    candidate = propose_relationship(
        "rate cut repricing",
        "may_shift",
        "risk-on regime",
        RelationshipContext.REGIME,
        evidence=(
            RelationshipEvidence(
                description="Canonical replay window identifies the macro event under review.",
                source_type=RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV,
                source_ref="packages/data_system/config/events.csv:CPI_2024_09_11_TIGHT",
                confidence=0.6,
            ),
            RelationshipEvidence(
                description="Offline event study showed co-movement in the sample.",
                source_type=RelationshipDataSource.EVENT_CONTEXT_REGIME_MAP,
                source_ref="packages/features_engine/config/event_context_regime.json:event_shock",
                confidence=0.6,
            ),
        ),
        proof_trace=("Reviewed against the offline sample without operational writes.",),
    )

    validated = mark_validated(candidate)
    record = build_promotion_record(validated)

    assert validated.status == RelationshipStatus.VALIDATED
    assert record["promotion_target"] == "kg_relation_review_candidate"
    assert record["authoritative"] is False
    assert record["offline_only"] is True
    assert record["openfoundry_written"] is False
    assert record["kg_written"] is False
    assert record["write_performed"] is False


def test_rejected_candidate_cannot_promote():
    candidate = RelationshipCandidate(
        subject="queue imbalance",
        predicate="may_increase",
        object="short horizon price pressure",
        context=RelationshipContext.MICRO,
        evidence=(
            RelationshipEvidence(
                "offline note",
                RelationshipDataSource.DATABENTO_CME_MBO_NPZ,
                "data/npz/MES_CPI_2024_09_11_TIGHT_mbo.npz",
            ),
        ),
        proof_trace=("human proof",),
    )
    rejected = reject_relationship_candidate(candidate, "insufficient support")

    assert rejected.status == RelationshipStatus.REJECTED
    assert rejected.rejection_reason == "insufficient support"
    with pytest.raises(ValueError, match="only validated"):
        build_promotion_record(rejected)


def test_promotion_target_must_be_review_artifact():
    candidate = mark_validated(
        propose_relationship(
            "CPI surprise",
            "may_expand",
            "MES spread volatility",
            RelationshipContext.MACRO,
            evidence=(
                RelationshipEvidence(
                    "offline note",
                    RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV,
                    "packages/data_system/config/events.csv:CPI_2024_09_11_TIGHT",
                ),
            ),
            proof_trace=("human-reviewed proof step",),
        )
    )

    assert "kg_relation_review_candidate" in ALLOWED_PROMOTION_TARGETS
    with pytest.raises(ValueError, match="review candidate"):
        build_promotion_record(candidate, promotion_target="research_cards/kg")


def test_boundary_flags_and_bad_confidence_fail_validation():
    candidate = RelationshipCandidate(
        subject="queue imbalance",
        predicate="may_precede",
        object="short horizon price pressure",
        context=RelationshipContext.MICRO,
        evidence=(
            RelationshipEvidence(
                "offline note",
                RelationshipDataSource.DATABENTO_CME_MBO_NPZ,
                "data/npz/MES_CPI_2024_09_11_TIGHT_mbo.npz",
                confidence=1.5,
            ),
        ),
        proof_trace=("human proof",),
        authoritative=True,
        offline_only=False,
        openfoundry_written=True,
        kg_written=True,
    )

    errors = validate_relationship_candidate(candidate)

    assert "evidence[0].confidence must be between 0 and 1" in errors
    assert "candidate must remain non-authoritative" in errors
    assert "candidate must remain offline-only" in errors
    assert "candidate must not claim an OpenFoundry write" in errors
    assert "candidate must not claim a KG write" in errors


def test_undefined_or_wrong_context_source_fails_validation():
    undefined = propose_relationship(
        "CPI surprise",
        "may_expand",
        "MES spread volatility",
        RelationshipContext.MACRO,
        evidence=(RelationshipEvidence("offline note", "local_note", "not canonical"),),
        proof_trace=("human proof",),
    )
    wrong_context = propose_relationship(
        "CPI surprise",
        "may_expand",
        "MES spread volatility",
        RelationshipContext.MACRO,
        evidence=(
            RelationshipEvidence(
                "MBO replay note",
                RelationshipDataSource.DATABENTO_CME_MBO_NPZ,
                "data/npz/MES_CPI_2024_09_11_TIGHT_mbo.npz",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert "evidence[0].source_type is not defined" in validate_relationship_candidate(undefined)
    assert any(
        "is not valid for context macro" in error
        for error in validate_relationship_candidate(wrong_context)
    )


def test_spoofed_source_ref_fails_validation():
    candidate = propose_relationship(
        "CPI surprise",
        "may_expand",
        "MES spread volatility",
        RelationshipContext.MACRO,
        evidence=(
            RelationshipEvidence(
                "not canonical",
                RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV,
                "live/news/feed:CPI_2024_09_11_TIGHT",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "source_ref must start with packages/data_system/config/events.csv:" in error
        for error in validate_relationship_candidate(candidate)
    )


def test_empty_event_ref_fragment_fails_validation():
    candidate = propose_relationship(
        "CPI surprise",
        "may_expand",
        "MES spread volatility",
        RelationshipContext.MACRO,
        evidence=(
            RelationshipEvidence(
                "empty event id",
                RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV,
                "packages/data_system/config/events.csv:",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "source_ref must start with packages/data_system/config/events.csv:" in error
        for error in validate_relationship_candidate(candidate)
    )


def test_malformed_databento_ref_fails_validation():
    candidate = propose_relationship(
        "queue imbalance",
        "may_precede",
        "short horizon price pressure",
        RelationshipContext.MICRO,
        evidence=(
            RelationshipEvidence(
                "malformed NPZ path",
                RelationshipDataSource.DATABENTO_CME_MBO_NPZ,
                "data/npz/_mbo.npz",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "source_ref must match data/npz/<symbol>_<event_id>_mbo.npz" in error
        for error in validate_relationship_candidate(candidate)
    )


def test_release_calendar_requires_sourced_row_ref():
    candidate = propose_relationship(
        "NFP release",
        "may_expand",
        "MES spread volatility",
        RelationshipContext.MACRO,
        evidence=(
            RelationshipEvidence(
                "calendar row without sourced status",
                RelationshipDataSource.SOURCED_RELEASE_CALENDAR,
                "packages/data_system/config/release_calendars/bls_nfp.csv:NFP_2024_09_06",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "row_status=SOURCED" in error
        for error in validate_relationship_candidate(candidate)
    )


def test_release_calendar_fake_sourced_marker_fails_validation():
    candidate = propose_relationship(
        "NFP release",
        "may_expand",
        "MES spread volatility",
        RelationshipContext.MACRO,
        evidence=(
            RelationshipEvidence(
                "fake sourced marker",
                RelationshipDataSource.SOURCED_RELEASE_CALENDAR,
                "packages/data_system/config/release_calendars/bls_nfp.csv:"
                "row_status=SOURCED_FAKE:NFP_2024_09_06",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "row_status=SOURCED" in error
        for error in validate_relationship_candidate(candidate)
    )


def test_definition_only_evidence_cannot_validate_relationship():
    candidate = propose_relationship(
        "CPI_TIGHT",
        "may_boost",
        "event_shock regime",
        RelationshipContext.REGIME,
        evidence=(
            RelationshipEvidence(
                "configured boost definition",
                RelationshipDataSource.EVENT_CONTEXT_REGIME_MAP,
                "packages/features_engine/config/event_context_regime.json:event_shock",
            ),
        ),
        proof_trace=("human proof",),
    )

    errors = validate_relationship_candidate(candidate)

    assert "at least one empirical offline evidence source is required" in errors
    with pytest.raises(ValueError, match="empirical"):
        mark_validated(candidate)


def test_world_event_context_accepts_gdelt_world_events_source(tmp_path):
    source_ref = _write_gdelt_cache(tmp_path)
    candidate = propose_relationship(
        "geopolitical shock",
        "may_increase",
        "macro uncertainty",
        RelationshipContext.WORLD_EVENT,
        evidence=(
            RelationshipEvidence(
                "Cached GDELT event links actors and event code with source URL provenance.",
                RelationshipDataSource.GDELT_WORLD_EVENTS,
                source_ref,
            ),
        ),
        proof_trace=("human proof",),
    )

    validated = mark_validated(candidate, repo_root=tmp_path)
    record = build_promotion_record(validated, repo_root=tmp_path)

    assert validated.status == RelationshipStatus.VALIDATED
    assert record["context"] == "world_event"
    assert record["evidence"][0]["source_type"] == "gdelt_world_events"
    assert record["evidence"][0]["source_empirical"] is True


def test_world_event_spoofed_gdelt_source_ref_fails_validation():
    candidate = propose_relationship(
        "geopolitical shock",
        "may_increase",
        "macro uncertainty",
        RelationshipContext.WORLD_EVENT,
        evidence=(
            RelationshipEvidence(
                "spoofed world event ref",
                RelationshipDataSource.GDELT_WORLD_EVENTS,
                "https://api.gdeltproject.org/api/v2/events/events:123456789",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "source_ref must match artifacts/world_events/gdelt/events" in error
        for error in validate_relationship_candidate(candidate)
    )


def test_world_event_canonical_looking_ref_requires_cache_file(tmp_path):
    candidate = propose_relationship(
        "geopolitical shock",
        "may_increase",
        "macro uncertainty",
        RelationshipContext.WORLD_EVENT,
        evidence=(
            RelationshipEvidence(
                "canonical-looking but no cache",
                RelationshipDataSource.GDELT_WORLD_EVENTS,
                "artifacts/world_events/gdelt/events/20240601.jsonl:123456789",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "cache file is missing" in error
        for error in validate_relationship_candidate(candidate, repo_root=tmp_path)
    )


def test_world_event_validation_requires_repo_root_for_cache_check():
    candidate = propose_relationship(
        "geopolitical shock",
        "may_increase",
        "macro uncertainty",
        RelationshipContext.WORLD_EVENT,
        evidence=(
            RelationshipEvidence(
                "canonical-looking but unchecked",
                RelationshipDataSource.GDELT_WORLD_EVENTS,
                "artifacts/world_events/gdelt/events/20240601.jsonl:123456789",
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(
        "repo_root is required" in error
        for error in validate_relationship_candidate(candidate)
    )


@pytest.mark.parametrize(
    ("mutate", "source_ref", "expected"),
    [
        (
            lambda row: row,
            "artifacts/world_events/gdelt/events/20240601.jsonl:999999999",
            "event_id was not found",
        ),
        (
            lambda row: {**row, "event_date": "20240602"},
            "artifacts/world_events/gdelt/events/20240601.jsonl:123456789",
            "cache date does not match record date",
        ),
        (
            lambda row: {**row, "provenance": {**row["provenance"], "provider": "manual_world_event"}},
            "artifacts/world_events/gdelt/events/20240601.jsonl:123456789",
            "record provider is not GDELT",
        ),
        (
            lambda row: {**row, "provenance": {**row["provenance"], "provider_version": "gdelt_1_0"}},
            "artifacts/world_events/gdelt/events/20240601.jsonl:123456789",
            "provider version is not GDELT 2.1 events",
        ),
        (
            lambda row: {**row, "provenance": {**row["provenance"], "source_url": ""}},
            "artifacts/world_events/gdelt/events/20240601.jsonl:123456789",
            "missing source_url provenance",
        ),
    ],
)
def test_world_event_cached_record_invariants_are_enforced(tmp_path, mutate, source_ref, expected):
    row = mutate(_gdelt_record_dict())
    _write_gdelt_cache_row(tmp_path, "20240601", row)
    candidate = propose_relationship(
        "geopolitical shock",
        "may_increase",
        "macro uncertainty",
        RelationshipContext.WORLD_EVENT,
        evidence=(
            RelationshipEvidence(
                "cached GDELT invariant test",
                RelationshipDataSource.GDELT_WORLD_EVENTS,
                source_ref,
            ),
        ),
        proof_trace=("human proof",),
    )

    assert any(expected in error for error in validate_relationship_candidate(candidate, repo_root=tmp_path))


def test_defined_sources_are_explicit_by_context():
    micro_sources = {source.source_type for source in defined_sources_for_context(RelationshipContext.MICRO)}
    macro_sources = {source.source_type for source in defined_sources_for_context(RelationshipContext.MACRO)}
    regime_sources = {source.source_type for source in defined_sources_for_context(RelationshipContext.REGIME)}

    assert micro_sources == {
        RelationshipDataSource.DATABENTO_CME_MBO_NPZ,
        RelationshipDataSource.MICROSTRUCTURE_PDF_MANIFEST,
    }
    assert macro_sources == {
        RelationshipDataSource.ECONOMIC_EVENT_UNIVERSE,
        RelationshipDataSource.SOURCED_RELEASE_CALENDAR,
        RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV,
    }
    assert regime_sources == {
        RelationshipDataSource.ECONOMIC_EVENT_UNIVERSE,
        RelationshipDataSource.SOURCED_RELEASE_CALENDAR,
        RelationshipDataSource.DATA_SYSTEM_EVENTS_CSV,
        RelationshipDataSource.FEATURES_ENGINE_REGIME_FILTER,
        RelationshipDataSource.EVENT_CONTEXT_REGIME_MAP,
    }
    assert {source.source_type for source in defined_sources_for_context(RelationshipContext.WORLD_EVENT)} == {
        RelationshipDataSource.GDELT_WORLD_EVENTS,
    }


def test_context_enum_includes_required_contexts():
    assert {context.value for context in RelationshipContext} == {
        "micro",
        "macro",
        "world_event",
        "regime",
    }
