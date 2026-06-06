"""After-action data_layer tests."""

from __future__ import annotations

import json
import os
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
    from data_layer.packet.microstructure_aar_packet import (
        build_microstructure_aar_packet,
        validate_packet_schema,
    )

    packet, _ = build_microstructure_aar_packet(FIXTURE, REPO)
    assert packet["pdf_citations_complete"] is True
    assert all(c["present_on_disk"] for c in packet["pdf_citations"])
    sim = packet["simulation_fidelity"]
    assert sim["cpp_replay_available"] is False
    assert sim["cpp_stack_verified"] is False
    assert sim["queue_tracker_status"] == "stub_or_unverified"
    assert validate_packet_schema(packet) == []


def test_packet_simulation_fidelity_link_only(tmp_path: Path):
    from data_layer.packet.microstructure_aar_packet import (
        build_microstructure_aar_packet,
        validate_packet_schema,
    )

    art = tmp_path / "run_link"
    art.mkdir()
    (art / "diagnostics.json").write_text(
        json.dumps(
            {
                "event_id": "X",
                "num_trades": 0,
                "pnl_by_injection_us": {"0": 0.0},
                "cpp_replay_available": False,
                "cpp_stack_verified": True,
                "cpp_stack_checks": {
                    "gateway_init": True,
                    "spsc_queue_roundtrip": True,
                    "feature_extract": True,
                    "decision_evaluate": True,
                    "risk_precheck": True,
                },
            }
        ),
        encoding="utf-8",
    )
    (art / "manifest.json").write_text(json.dumps({"data_sufficient": True}), encoding="utf-8")
    (art / "config.yaml").write_text("model_id: HYP_5\nevent_id: X\n", encoding="utf-8")
    packet, _ = build_microstructure_aar_packet(art, REPO)
    assert packet["simulation_fidelity"]["queue_tracker_status"] == "link_only"
    assert validate_packet_schema(packet) == []


def test_packet_accepts_structured_workbench_latency_authority(tmp_path: Path):
    from data_layer.packet.microstructure_aar_packet import (
        build_microstructure_aar_packet,
        validate_packet_schema,
    )

    art = tmp_path / "run_structured_latency"
    art.mkdir()
    (art / "diagnostics.json").write_text(
        json.dumps(
            {
                "event_id": "NFP_2025_10_03_TIGHT",
                "num_trades": 0,
                "latency_authority": {
                    "authority": "chi404_cpp_latency_summary",
                    "python_research_runtime_authoritative": False,
                    "lane_pass": True,
                    "promote_candidate": False,
                },
                "breakeven_us": 2000000.0,
                "pnl_by_injection_us": {"0": 0.0},
            }
        ),
        encoding="utf-8",
    )
    (art / "manifest.json").write_text(json.dumps({"data_sufficient": True}), encoding="utf-8")
    (art / "config.yaml").write_text("model_id: HYP_5\nevent_id: NFP_2025_10_03_TIGHT\n", encoding="utf-8")

    packet, _ = build_microstructure_aar_packet(art, REPO)

    assert packet["latency_authority"]["authority"] == "chi404_cpp_latency_summary"
    assert packet["latency_authority"]["python_research_runtime_authoritative"] is False
    assert packet["latency_authority"]["lane_pass"] is True
    assert packet["latency_authority"]["promote_candidate"] is False
    assert validate_packet_schema(packet) == []


def test_packet_stack_verified_requires_all_checks(tmp_path: Path):
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    art = tmp_path / "run_bad_stack"
    art.mkdir()
    (art / "diagnostics.json").write_text(
        json.dumps(
            {
                "event_id": "X",
                "num_trades": 0,
                "pnl_by_injection_us": {"0": 0.0},
                "cpp_stack_verified": True,
                "cpp_stack_checks": {"gateway_init": True, "spsc_queue_roundtrip": False},
            }
        ),
        encoding="utf-8",
    )
    (art / "manifest.json").write_text(json.dumps({"data_sufficient": True}), encoding="utf-8")
    (art / "config.yaml").write_text("model_id: HYP_5\nevent_id: X\n", encoding="utf-8")
    packet, _ = build_microstructure_aar_packet(art, REPO)
    assert packet["simulation_fidelity"]["cpp_stack_verified"] is False
    assert packet["simulation_fidelity"]["queue_tracker_status"] == "stub_or_unverified"


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


def test_packet_waives_audit_for_quote_engine(tmp_path):
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    art = tmp_path / "run_quote"
    art.mkdir()
    (art / "diagnostics.json").write_text(
        json.dumps(
            {
                "event_id": "CPI_2024_09_11_TIGHT",
                "num_trades": 5,
                "execution_assumptions": "quote_engine",
                "ablation_modes": [{"mode_id": "hybrid_full", "num_trades": 5}],
            }
        ),
        encoding="utf-8",
    )
    (art / "manifest.json").write_text(json.dumps({"data_sufficient": True}), encoding="utf-8")
    (art / "config.yaml").write_text(
        "model_id: PDF_MODEL_4\nevent_id: CPI_2024_09_11_TIGHT\nexecution_assumptions: quote_engine\n",
        encoding="utf-8",
    )
    packet, skips = build_microstructure_aar_packet(art, REPO)
    assert "AUDIT_INCOMPLETE" not in skips
    assert packet.get("audit_waiver_reason") == "quote_engine_aggregate_only"
    assert packet.get("simulation_fidelity", {}).get("quote_engine_replay") is True
    assert len(packet.get("ablation_modes", [])) == 1


def test_packet_waives_audit_for_crypto_order_book_replay(tmp_path):
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    art = tmp_path / "run_crypto"
    art.mkdir()
    (art / "diagnostics.json").write_text(
        json.dumps(
            {
                "event_id": "CRYPTO_SMOKE_run_1",
                "symbol": "BTCUSDT",
                "num_trades": 5,
                "execution_assumptions": "crypto_order_book_replay",
                "latency_authority": "crypto_venue_submit_ack",
                "pnl_by_injection_us": {"0": 12.5},
            }
        ),
        encoding="utf-8",
    )
    (art / "manifest.json").write_text(json.dumps({"data_sufficient": True}), encoding="utf-8")
    (art / "config.yaml").write_text(
        "model_id: crypto_candidate\n"
        "event_id: CRYPTO_SMOKE_run_1\n"
        "symbol: BTCUSDT\n"
        "execution_assumptions: crypto_order_book_replay\n",
        encoding="utf-8",
    )

    packet, skips = build_microstructure_aar_packet(art, REPO)

    assert "AUDIT_INCOMPLETE" not in skips
    assert packet.get("audit_waiver_reason") == "crypto_replay_aggregate_only"
    assert packet.get("simulation_fidelity", {}).get("crypto_order_book_replay") is True
    assert packet["latency_authority"]["authority"] == "crypto_venue_submit_ack"


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
    engine_src = (REPO / "apps" / "workbench" / "src" / "run" / "engine.py").read_text(encoding="utf-8")
    assert "should_run_after_action = (run_after_action or not fast_sweep) and _after_action_allowed()" in engine_src
    assert "run_after_action_report" in engine_src

    calls: list = []

    def _track(*args, **kwargs):
        calls.append(args)

    with patch("data_layer.pipeline.after_action.run_after_action_report", side_effect=_track):
        fast_sweep = True
        run_after_action = False
        allowed = True
        if (run_after_action or not fast_sweep) and allowed:
            from data_layer.pipeline.after_action import run_after_action_report

            run_after_action_report(FIXTURE, REPO)
        assert calls == []

        fast_sweep = False
        if (run_after_action or not fast_sweep) and allowed:
            from data_layer.pipeline.after_action import run_after_action_report

            run_after_action_report(FIXTURE, REPO)
        assert len(calls) == 1

        fast_sweep = True
        run_after_action = True
        if (run_after_action or not fast_sweep) and allowed:
            from data_layer.pipeline.after_action import run_after_action_report

            run_after_action_report(FIXTURE, REPO)
        assert len(calls) == 2


def test_openai_compatible_mocked_pipeline(tmp_path, monkeypatch):
    from data_layer.pipeline.after_action import run_after_action_report

    art = tmp_path / "run_ok"
    art.mkdir()
    for name in ("diagnostics.json", "manifest.json", "config.yaml", "trades.parquet"):
        (art / name).write_bytes((FIXTURE / name).read_bytes())

    monkeypatch.setattr("data_layer.llm.openai_compatible_client.llm_available", lambda **kw: True)
    from data_layer.llm.openai_compatible_client import GenerateResult

    mock_response = json.dumps(
        {
            "schema_version": "1",
            "run_id": "run_ok",
            "input_schema_version": "1",
            "llm_model": "mock",
            "llm_elapsed_s": 0.1,
            "llm_status": "ok",
            "symbolic_passed": True,
            "decision": {"promote_candidate_recommendation": False},
            "kg_annotations": [],
            "narrative_md": "# After-action\n\nOK.",
        }
    )
    monkeypatch.setattr(
        "data_layer.llm.openai_compatible_client.generate",
        lambda *a, **k: GenerateResult(mock_response, model="mock", elapsed_s=0.1),
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
    (tmp_repo / "artifacts" / "research_cards" / "kg").mkdir(parents=True)
    (tmp_repo / "vendor" / "openfoundry" / "domain-packs" / "core").mkdir(parents=True)
    (tmp_repo / "vendor" / "openfoundry" / "domain-packs" / "core" / "pack.yaml").write_text(
        "pack: stub\n", encoding="utf-8"
    )
    (tmp_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry" / "VENDOR.lock").write_bytes(
        (REPO / "integrations" / "openfoundry" / "VENDOR.lock").read_bytes()
    )
    (tmp_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_bytes(
        (REPO / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").read_bytes()
    )
    # Mirror the hft3 ODL pack + sidecar citations into the fake repo so
    # assert_connector_valid() passes the post-phase-5 ontology citation check.
    hft3_pack_src = REPO / "integrations" / "openfoundry" / "domain-packs" / "hft3"
    if hft3_pack_src.is_dir():
        import shutil
        shutil.copytree(hft3_pack_src, tmp_repo / "integrations" / "openfoundry" / "domain-packs" / "hft3")
    (tmp_repo / "docs" / "references").mkdir(parents=True)
    (tmp_repo / "docs" / "references" / "MANIFEST.md").write_bytes(
        (REPO / "docs" / "references" / "MANIFEST.md").read_bytes()
    )
    (tmp_repo / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").write_bytes(
        (REPO / "docs" / "references" / "chicago_cme_microstructure_mathematical_model.pdf").read_bytes()
    )

    meta = run_after_action_report(art, tmp_repo, skip_llm=False)
    assert (art / "after_action_packet.json").is_file()
    assert (art / "after_action_symbolic.json").is_file()
    assert (art / "after_action_meta.json").is_file()
    assert (art / "kg_slice.json").is_file()
    assert meta["llm_status"] == "ok"
    assert (art / "after_action_response.json").is_file()
    assert (art / "after_action_report.md").is_file()
    from data_layer.packet.validate import validate_aar_packet_out

    response = json.loads((art / "after_action_response.json").read_text(encoding="utf-8"))
    assert validate_aar_packet_out(response) == []


def test_after_action_skips_llm_on_pending_vendor_lock(tmp_path, monkeypatch):
    from data_layer.pipeline.after_action import run_after_action_report

    art = tmp_path / "run_pending"
    art.mkdir()
    for name in ("diagnostics.json", "manifest.json", "config.yaml", "trades.parquet"):
        (art / name).write_bytes((FIXTURE / name).read_bytes())

    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    (tmp_repo / "artifacts" / "research_cards" / "kg").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (tmp_repo / "vendor" / "openfoundry" / "domain-packs" / "core").mkdir(parents=True)
    (tmp_repo / "vendor" / "openfoundry" / "domain-packs" / "core" / "pack.yaml").write_text(
        "pack: stub\n", encoding="utf-8"
    )
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
    monkeypatch.setattr(
        "data_layer.packet.microstructure_aar_packet.load_pdf_citations",
        lambda repo: (
            [{"field": "x", "pdf": "algorithmic_trading_strategy_development.pdf", "present_on_disk": True}],
            True,
        ),
    )

    meta = run_after_action_report(art, tmp_repo, skip_llm=False)
    assert meta["llm_status"] == "skipped_connector"
    response = json.loads((art / "after_action_response.json").read_text(encoding="utf-8"))
    assert response["kg_annotations"] == []


@pytest.mark.slow
def test_openai_compatible_live_gpt55_fixture(tmp_path):
    import shutil

    from data_layer.llm import openai_compatible_client as llm_client
    from data_layer.pipeline.after_action import run_after_action_report

    if os.environ.get("HFT3_LIVE_LLM_TESTS") != "1":
        pytest.skip("set HFT3_LIVE_LLM_TESTS=1 to run paid external GPT-5.5 fixture")
    if not llm_client.llm_available():
        pytest.skip("OpenAI-compatible GPT-5.5 endpoint not configured")

    art = tmp_path / "run_live"
    art.mkdir()
    for name in ("diagnostics.json", "manifest.json", "config.yaml", "trades.parquet"):
        (art / name).write_bytes((FIXTURE / name).read_bytes())

    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    (tmp_repo / "artifacts" / "research_cards" / "kg").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry").mkdir(parents=True)
    (tmp_repo / "integrations" / "openfoundry" / "VENDOR.lock").write_bytes(
        (REPO / "integrations" / "openfoundry" / "VENDOR.lock").read_bytes()
    )
    (tmp_repo / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").write_bytes(
        (REPO / "integrations" / "openfoundry" / "hft3-cme-mbo.yaml").read_bytes()
    )
    src_vendor = REPO / "vendor" / "openfoundry"
    dst_vendor = tmp_repo / "vendor" / "openfoundry"
    if src_vendor.is_dir():
        shutil.copytree(str(src_vendor), str(dst_vendor), dirs_exist_ok=True)
    else:
        dst_vendor.mkdir(parents=True, exist_ok=True)
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
