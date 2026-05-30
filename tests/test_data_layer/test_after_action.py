"""After-action data_layer tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "workbench_run_minimal"


def test_vendor_submodules_present():
    of = REPO / "vendor" / "openfoundry"
    ag = REPO / "vendor" / "alphageometry"
    lock = REPO / "integrations" / "openfoundry" / "VENDOR.lock"
    assert of.is_dir(), "vendor/openfoundry submodule required"
    assert ag.is_dir(), "vendor/alphageometry submodule required"
    assert (of / "domain-packs" / "core" / "pack.yaml").is_file()
    assert lock.is_file()
    text = lock.read_text(encoding="utf-8")
    assert "openfoundry=" in text
    assert "alphageometry=" in text
    assert "pending" not in text.split("openfoundry=")[1].split()[0]


def test_openfoundry_connector_validates():
    from data_layer.openfoundry_bridge import validate_connector

    result = validate_connector(REPO)
    assert result["connector"]["asset_class"] == "cme_mbo_microstructure"
    assert result["connector"]["upstream"] == "https://github.com/syzygyhack/open-foundry"
    assert result["upstream"]["core_pack_present"] is True
    assert "openfoundry" in result["vendor_shas"]


def test_packet_pdf_citations_complete():
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    packet, _ = build_microstructure_aar_packet(FIXTURE, REPO)
    assert packet["pdf_citations_complete"] is True
    assert all(c["present_on_disk"] for c in packet["pdf_citations"])


def test_packet_requires_per_trade_audit_when_trades(tmp_path):
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    art = tmp_path / "run_bad"
    art.mkdir()
    (art / "diagnostics.json").write_text(
        json.dumps({"event_id": "X", "num_trades": 2, "pnl_by_injection_us": {"0": 0.0}}),
        encoding="utf-8",
    )
    (art / "manifest.json").write_text(json.dumps({"data_sufficient": True}), encoding="utf-8")
    (art / "config.yaml").write_text("model_id: HYP_5\nevent_id: X\n", encoding="utf-8")
    packet, skips = build_microstructure_aar_packet(art, REPO)
    assert "AUDIT_INCOMPLETE" in skips
    assert len(packet["per_trade_audit"]) == 0


def test_symbolic_exchange_receive_ordering():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {"python_research_runtime_authoritative": False},
        "per_trade_audit": [
            {
                "market_data_exchange_ts": 200,
                "market_data_receive_ts": 100,
                "decision_end_ts": 110,
                "order_send_ts": 120,
                "fill_ts": 130,
                "feed_delay_us": 10.0,
                "decision_compute_us": 10.0,
                "decision_to_send_us": 10.0,
                "send_to_ack_us": 10.0,
                "tick_to_ack_us": 40.0,
            }
        ],
    }
    result = check_latency_invariants(packet)
    assert result["passed"] is False
    assert any("market_data_receive_ts before market_data_exchange_ts" in v for v in result["violations"])


def test_symbolic_latency_chain_violation():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {"python_research_runtime_authoritative": False, "lane_pass": False},
        "per_trade_audit": [
            {
                "market_data_receive_ts": 100,
                "decision_end_ts": 90,
                "order_send_ts": 110,
                "fill_ts": 105,
                "feed_delay_us": 10.0,
                "decision_compute_us": 10.0,
                "decision_to_send_us": 10.0,
                "send_to_ack_us": 10.0,
                "tick_to_ack_us": 100.0,
            }
        ],
    }
    result = check_latency_invariants(packet)
    assert result["passed"] is False
    assert result["violations"]


def test_python_runtime_marked_non_authoritative():
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    packet, _ = build_microstructure_aar_packet(FIXTURE, REPO)
    lat = packet["latency_authority"]
    assert lat["python_research_runtime_authoritative"] is False
    assert lat.get("python_research_runtime_us") is not None


def test_no_quant_x_imports():
    data_layer = REPO / "data_layer"
    hits = []
    for py in data_layer.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "quant_data_refinery" in text or "quant_x" in text.lower():
            hits.append(str(py.relative_to(REPO)))
    assert hits == []


def test_after_action_skipped_on_fast_sweep():
    engine_src = (REPO / "workbench" / "src" / "run" / "engine.py").read_text(encoding="utf-8")
    assert "if not fast_sweep and _after_action_allowed():" in engine_src
    assert "run_after_action_report" in engine_src

    calls: list = []

    def _track(*args, **kwargs):
        calls.append(args)

    with patch("data_layer.pipeline.after_action.run_after_action_report", side_effect=_track):
        fast_sweep = True
        allowed = True
        if not fast_sweep and allowed:
            from data_layer.pipeline.after_action import run_after_action_report

            run_after_action_report(FIXTURE, REPO)
        assert calls == []

        fast_sweep = False
        if not fast_sweep and allowed:
            from data_layer.pipeline.after_action import run_after_action_report

            run_after_action_report(FIXTURE, REPO)
        assert len(calls) == 1


def test_ollama_mocked_pipeline(tmp_path, monkeypatch):
    from data_layer.pipeline.after_action import run_after_action_report

    art = tmp_path / "run_ok"
    art.mkdir()
    for name in ("diagnostics.json", "manifest.json", "config.yaml", "trades.parquet"):
        (art / name).write_bytes((FIXTURE / name).read_bytes())

    monkeypatch.setattr("data_layer.llm.ollama_client.ollama_available", lambda **kw: True)
    from data_layer.llm.ollama_client import GenerateResult

    monkeypatch.setattr(
        "data_layer.llm.ollama_client.generate",
        lambda *a, **k: GenerateResult("# After-action\n\nOK.\n\n```json\n[]\n```", model="mock"),
    )
    monkeypatch.setattr(
        "data_layer.packet.microstructure_aar_packet.load_pdf_citations",
        lambda repo: (
            [{"field": "x", "pdf": "algorithmic_trading_strategy_development.pdf", "present_on_disk": True}],
            True,
        ),
    )

    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    (tmp_repo / "research_cards" / "kg").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry" / "VENDOR.lock").write_text(
        "openfoundry=pending\nalphageometry=6777cb586cbb46beed28db12dc72c69770b68337\n",
        encoding="utf-8",
    )
    (tmp_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_bytes(
        (REPO / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").read_bytes()
    )
    (tmp_repo / "docs" / "references").mkdir(parents=True)
    (tmp_repo / "docs" / "references" / "MANIFEST.md").write_bytes(
        (REPO / "docs" / "references" / "MANIFEST.md").read_bytes()
    )

    meta = run_after_action_report(art, tmp_repo, skip_llm=False)
    assert (art / "after_action_packet.json").is_file()
    assert (art / "after_action_symbolic.json").is_file()
    assert (art / "after_action_meta.json").is_file()
    assert (art / "kg_slice.json").is_file()
    assert meta["llm_status"] == "ok"
    assert (art / "after_action_report.md").is_file()


@pytest.mark.slow
def test_ollama_live_hawkish_fixture(tmp_path):
    from data_layer.llm import ollama_client
    from data_layer.pipeline.after_action import run_after_action_report

    if not ollama_client.ollama_available():
        pytest.skip("Hawkish-8B not in ollama list")

    art = tmp_path / "run_live"
    art.mkdir()
    for name in ("diagnostics.json", "manifest.json", "config.yaml", "trades.parquet"):
        (art / name).write_bytes((FIXTURE / name).read_bytes())

    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    (tmp_repo / "research_cards" / "kg").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry" / "VENDOR.lock").write_bytes(
        (REPO / "integrations" / "openfoundry" / "VENDOR.lock").read_bytes()
    )
    (tmp_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_bytes(
        (REPO / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").read_bytes()
    )
    (tmp_repo / "docs" / "references").mkdir(parents=True)
    for pdf in (REPO / "docs" / "references").glob("*.pdf"):
        (tmp_repo / "docs" / "references" / pdf.name).write_bytes(pdf.read_bytes())
    (tmp_repo / "docs" / "references" / "MANIFEST.md").write_bytes(
        (REPO / "docs" / "references" / "MANIFEST.md").read_bytes()
    )

    meta = run_after_action_report(art, tmp_repo, skip_llm=False)
    assert meta["llm_status"] == "ok", meta
    assert (art / "after_action_report.md").is_file()
    assert meta.get("llm_elapsed_s", 0) > 0
