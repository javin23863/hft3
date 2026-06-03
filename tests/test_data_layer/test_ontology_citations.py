"""Ontology citation validation — every ODL extension has a grounded sidecar.

Companion to `packages/data_layer/openfoundry_bridge.py` and the hft3 ODL pack
at `integrations/openfoundry/domain-packs/hft3/`. These tests prove the
validator catches missing sidecars, malformed citations, missing PDFs, and
mismatched extension names. The pack is fail-closed: any defect must raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_validate_ontology_citations_all_ok_for_real_pack():
    from data_layer.openfoundry_bridge import validate_ontology_citations

    result = validate_ontology_citations(REPO)
    assert result["all_ok"] is True
    assert len(result["extensions"]) == 9
    expected = {
        "MarkedMicroEvent",
        "BookSnapshotAtDecision",
        "QueuePositionEstimate",
        "LatencyChainUs",
        "CppLatencyBudget",
        "InjectionSweepResult",
        "StrategySignal",
        "FillOutcome",
        "EventContext",
    }
    assert {e["extension"] for e in result["extensions"]} == expected


def test_every_extension_entry_has_required_fields():
    from data_layer.openfoundry_bridge import validate_ontology_citations

    result = validate_ontology_citations(REPO)
    for entry in result["extensions"]:
        assert set(entry.keys()) >= {"extension", "sidecar_path", "ok", "errors"}
        assert isinstance(entry["errors"], list)
        assert entry["ok"] is True
        assert entry["errors"] == []


def test_citation_sidecar_primary_pdf_on_disk():
    """For every ok entry, the sidecar's primary.pdf resolves to docs/references/."""
    import yaml

    from data_layer.openfoundry_bridge import validate_ontology_citations

    result = validate_ontology_citations(REPO)
    for entry in result["extensions"]:
        if entry["ok"]:
            data = yaml.safe_load(Path(entry["sidecar_path"]).read_text(encoding="utf-8"))
            primary = data.get("primary") or {}
            pdf = str(primary.get("pdf", "")).strip()
            assert pdf
            assert (REPO / "docs" / "references" / pdf).is_file(), f"missing PDF: {pdf}"


def test_validate_connector_includes_ontology_citations():
    from data_layer.openfoundry_bridge import validate_connector

    result = validate_connector(REPO)
    assert "ontology_citations" in result
    assert result["ontology_citations"]["all_ok"] is True


def test_assert_connector_valid_passes_for_real_repo():
    from data_layer.openfoundry_bridge import assert_connector_valid, validate_connector

    assert_connector_valid(validate_connector(REPO))


def test_missing_sidecar_fails_validation(tmp_path):
    import yaml

    from data_layer.openfoundry_bridge import validate_ontology_citations

    fake_repo = tmp_path / "fake_repo"
    (fake_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (fake_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_text(
        "connector_id: hft3-cme-mbo\n"
        "schema_version: '1'\n"
        "asset_class: cme_mbo_microstructure\n"
        "upstream: https://github.com/syzygyhack/open-foundry\n"
        "artifact_root: research_cards/workbench_runs\n"
        "stream_mappings: {}\n"
        "ontology_extensions:\n"
        "  - MarkedMicroEvent\n",
        encoding="utf-8",
    )
    (fake_repo / "integrations" / "openfoundry" / "domain-packs" / "hft3" / "citations").mkdir(parents=True)
    # No sidecar file written

    result = validate_ontology_citations(fake_repo)
    assert result["all_ok"] is False
    assert len(result["extensions"]) == 1
    assert result["extensions"][0]["ok"] is False
    assert "sidecar missing" in result["extensions"][0]["errors"][0]


def test_bad_page_fails_validation(tmp_path):
    from data_layer.openfoundry_bridge import validate_ontology_citations

    fake_repo = tmp_path / "fake_repo"
    (fake_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (fake_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_text(
        "connector_id: hft3-cme-mbo\n"
        "schema_version: '1'\n"
        "asset_class: cme_mbo_microstructure\n"
        "upstream: https://github.com/syzygyhack/open-foundry\n"
        "artifact_root: research_cards/workbench_runs\n"
        "stream_mappings: {}\n"
        "ontology_extensions:\n"
        "  - MarkedMicroEvent\n",
        encoding="utf-8",
    )
    citations_dir = fake_repo / "integrations" / "openfoundry" / "domain-packs" / "hft3" / "citations"
    citations_dir.mkdir(parents=True)
    (citations_dir / "MarkedMicroEvent.yaml").write_text(
        "extension: MarkedMicroEvent\n"
        "primary:\n"
        "  pdf: chicago_cme_microstructure_mathematical_model.pdf\n"
        "  section: '§4 MBO Marked Point Process'\n"
        "  page: 0\n"
        "claims:\n"
        "  - 'bad page'\n",
        encoding="utf-8",
    )
    # Need a real references dir with the PDF on disk; fake_repo doesn't have it
    (fake_repo / "docs" / "references").mkdir(parents=True)
    (fake_repo / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").write_bytes(b"%PDF-stub")

    result = validate_ontology_citations(fake_repo)
    assert result["all_ok"] is False
    assert any("primary.page" in e for e in result["extensions"][0]["errors"])


def test_missing_claims_fails_validation(tmp_path):
    from data_layer.openfoundry_bridge import validate_ontology_citations

    fake_repo = tmp_path / "fake_repo"
    (fake_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (fake_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_text(
        "connector_id: hft3-cme-mbo\n"
        "schema_version: '1'\n"
        "asset_class: cme_mbo_microstructure\n"
        "upstream: https://github.com/syzygyhack/open-foundry\n"
        "artifact_root: research_cards/workbench_runs\n"
        "stream_mappings: {}\n"
        "ontology_extensions:\n"
        "  - MarkedMicroEvent\n",
        encoding="utf-8",
    )
    citations_dir = fake_repo / "integrations" / "openfoundry" / "domain-packs" / "hft3" / "citations"
    citations_dir.mkdir(parents=True)
    (citations_dir / "MarkedMicroEvent.yaml").write_text(
        "extension: MarkedMicroEvent\n"
        "primary:\n"
        "  pdf: chicago_cme_microstructure_mathematical_model.pdf\n"
        "  section: '§4 MBO Marked Point Process'\n"
        "  page: 2\n"
        "claims: []\n",
        encoding="utf-8",
    )
    (fake_repo / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").parent.mkdir(parents=True)
    (fake_repo / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").write_bytes(b"%PDF-stub")

    result = validate_ontology_citations(fake_repo)
    assert result["all_ok"] is False
    assert any("claims" in e for e in result["extensions"][0]["errors"])


def test_extension_mismatch_fails_validation(tmp_path):
    from data_layer.openfoundry_bridge import validate_ontology_citations

    fake_repo = tmp_path / "fake_repo"
    (fake_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (fake_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_text(
        "connector_id: hft3-cme-mbo\n"
        "schema_version: '1'\n"
        "asset_class: cme_mbo_microstructure\n"
        "upstream: https://github.com/syzygyhack/open-foundry\n"
        "artifact_root: research_cards/workbench_runs\n"
        "stream_mappings: {}\n"
        "ontology_extensions:\n"
        "  - MarkedMicroEvent\n",
        encoding="utf-8",
    )
    citations_dir = fake_repo / "integrations" / "openfoundry" / "domain-packs" / "hft3" / "citations"
    citations_dir.mkdir(parents=True)
    (citations_dir / "MarkedMicroEvent.yaml").write_text(
        "extension: WrongName\n"
        "primary:\n"
        "  pdf: chicago_cme_microstructure_mathematical_model.pdf\n"
        "  section: '§4 MBO Marked Point Process'\n"
        "  page: 2\n"
        "claims:\n"
        "  - 'mismatch'\n",
        encoding="utf-8",
    )
    (fake_repo / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").parent.mkdir(parents=True)
    (fake_repo / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").write_bytes(b"%PDF-stub")

    result = validate_ontology_citations(fake_repo)
    assert result["all_ok"] is False
    assert any("extension mismatch" in e for e in result["extensions"][0]["errors"])
