"""Connector-gate error classification — distinguishes ontology citation failures.

Companion to `packages/data_layer/llm/packet_runner.py`. The ontology
hardening (phases 5+6) needs the AAR / pipeline responses to report
specific `llm_status` values when the connector fails because of missing
or broken ODL sidecar citations, not just generic vendor/connector errors.
"""

from __future__ import annotations

from pathlib import Path


def test_classify_uncited_ontology():
    from data_layer.llm.packet_runner import _classify_connector_error

    err = "ontology citation sidecars failed (3 of 9):\n  - MarkedMicroEvent: sidecar missing"
    assert _classify_connector_error(err) == "skipped_uncited_ontology"


def test_classify_broken_pdf_cite():
    from data_layer.llm.packet_runner import _classify_connector_error

    err = "ontology citation sidecars failed (1 of 9):\n  - MarkedMicroEvent: sidecar primary.pdf not on disk: bogus.pdf"
    assert _classify_connector_error(err) == "skipped_broken_pdf_cite"


def test_classify_generic_connector_error():
    from data_layer.llm.packet_runner import _classify_connector_error

    assert _classify_connector_error("vendor/openfoundry directory missing") == "skipped_connector"
    assert _classify_connector_error("vendor lock openfoundry not pinned (got 'pending')") == "skipped_connector"
    assert _classify_connector_error("OpenFoundry core pack missing under vendor/openfoundry") == "skipped_connector"


def test_classify_ontology_beats_generic_substring():
    from data_layer.llm.packet_runner import _classify_connector_error

    err = "ontology citation sidecars failed; vendor lock openfoundry not pinned (got 'pending')"
    assert _classify_connector_error(err) == "skipped_uncited_ontology"


def test_schemas_accept_new_llm_status_values():
    """The new ontology statuses must be valid llm_status values in all 3 response schemas."""
    from pathlib import Path

    REPO = Path(__file__).resolve().parents[2]
    schemas = [
        "packages/data_layer/packet/schema_aar_response_v1.json",
        "packages/data_layer/packet/schema_pipeline_response_v1.json",
        "packages/data_layer/packet/schema_pipeline_hypothesis_response_v1.json",
    ]
    for rel in schemas:
        schema_text = (REPO / rel).read_text(encoding="utf-8")
        import json as _json

        sch = _json.loads(schema_text)
        enum = sch["properties"]["llm_status"]["enum"]
        for new_status in ("skipped_uncited_ontology", "skipped_broken_pdf_cite"):
            assert new_status in enum, f"{rel} missing {new_status!r} in llm_status enum"


def test_aar_response_with_new_status_validates():
    """A response with `skipped_uncited_ontology` must pass `validate_aar_packet_out`."""
    from data_layer.packet.validate import validate_aar_packet_out

    response = {
        "schema_version": "1",
        "run_id": "test",
        "input_schema_version": "1",
        "llm_model": None,
        "llm_elapsed_s": 0.0,
        "llm_status": "skipped_uncited_ontology",
        "symbolic_passed": True,
        "decision": {"promote_candidate_recommendation": False},
        "kg_annotations": [],
        "narrative_md": "After-action LLM skipped: ontology citations missing.",
    }
    assert validate_aar_packet_out(response) == []


def test_packet_runner_skips_with_specific_status_when_sidecar_missing(tmp_path):
    """If a sidecar is missing in a fake repo, the AAR response must report `skipped_uncited_ontology`."""
    from unittest.mock import patch

    from data_layer.llm import openai_compatible_client as llm_client
    from data_layer.llm.packet_runner import run_llm_on_aar_packet
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "workbench_run_minimal"

    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    (fake_repo / "integrations" / "openfoundry").mkdir(parents=True)
    REPO = Path(__file__).resolve().parents[2]
    (fake_repo / "integrations" / "openfoundry" / "VENDOR.lock").write_bytes(
        (REPO / "integrations" / "openfoundry" / "VENDOR.lock").read_bytes()
    )
    # Connector declares 1 extension; no sidecar on disk -> skipped_uncited_ontology
    (fake_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_text(
        "connector_id: hft3-cme-mbo\n"
        "schema_version: '1'\n"
        "asset_class: cme_mbo_microstructure\n"
        "upstream: https://github.com/syzygyhack/open-foundry\n"
        "artifact_root: research_cards/workbench_runs\n"
        "stream_mappings: {}\n"
        "ontology_extensions:\n  - MarkedMicroEvent\n",
        encoding="utf-8",
    )
    (fake_repo / "integrations" / "openfoundry" / "domain-packs" / "hft3" / "citations").mkdir(parents=True)
    (fake_repo / "vendor" / "openfoundry" / "domain-packs" / "core").mkdir(parents=True)
    (fake_repo / "vendor" / "openfoundry" / "domain-packs" / "core" / "pack.yaml").write_text("pack: stub\n", encoding="utf-8")
    (fake_repo / "docs" / "references").mkdir(parents=True)
    (fake_repo / "docs" / "references" / "MANIFEST.md").write_bytes(
        (REPO / "docs" / "references" / "MANIFEST.md").read_bytes()
    )
    (fake_repo / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").write_bytes(
        (REPO / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").read_bytes()
    )

    packet, skip_reasons = build_microstructure_aar_packet(FIXTURE, REPO)
    symbolic = check_latency_invariants(packet)

    with patch.object(llm_client, "llm_available", return_value=True):
        out = run_llm_on_aar_packet(
            packet, symbolic, repo_root=fake_repo, skip_reasons=skip_reasons
        )
    assert out["llm_status"] == "skipped_uncited_ontology"
    assert "ontology" in out["narrative_md"].lower() or "sidecar" in out["narrative_md"].lower()
