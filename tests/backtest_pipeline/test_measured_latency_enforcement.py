"""Paid-campaign replay latency law: measured CHI404 by default, never silent constants."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from hbt_stub import _ensure_hftbacktest_stub

_ensure_hftbacktest_stub()

from backtest_pipeline.src.chi404_latency import (
    DEFAULT_CHI404_SUMMARY,
    resolve_latency_model,
    resolve_offensive_tick_to_send_us,
    resolve_order_ack_ms,
)
from backtest_pipeline.src.hftbacktest_only_pipeline import (
    CANCEL_LATENCY_POLICY,
    HftBacktestOnlyPipelineError,
    HftBacktestOnlyRunConfig,
    _latency_report,
    _resolve_latency_ns,
)

REPO = Path(__file__).resolve().parents[2]


def _load_campaign_module():
    script = REPO / "scripts" / "run_hftbacktest_only_campaign.py"
    spec = importlib.util.spec_from_file_location("run_hbt_only_campaign_latency_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(**overrides) -> HftBacktestOnlyRunConfig:
    base = dict(
        run_id="latency_enforcement_test",
        symbol="MES",
        contract="MESH6",
        event_id="CPI_2024_09_11_TIGHT",
        normalized_npz=Path("missing.npz"),
        initial_snapshot=Path("missing_snapshot.npz"),
        strategy_id="smoke_limit_order",
        strategy_params={},
    )
    base.update(overrides)
    return HftBacktestOnlyRunConfig(**base)


def test_campaign_runner_defaults_to_chi404_measured(tmp_path: Path, monkeypatch) -> None:
    module = _load_campaign_module()
    captured: dict[str, object] = {}

    def fake_run_campaign(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"failed_count": 0}

    monkeypatch.setattr(module, "run_campaign", fake_run_campaign)
    exit_code = module.main(["--campaign-manifest", str(tmp_path / "campaign.jsonl")])
    assert exit_code == 0
    assert captured["latency_model"] == "chi404_measured"
    assert captured["entry_latency_ns"] is None
    assert captured["response_latency_ns"] is None


def test_default_chi404_resolution_matches_measured_artifacts() -> None:
    # The default (stress regime) resolves the owner-measured CHI404 model and
    # ADDS the measured offensive tick->send p99 to the entry leg (our fire path).
    entry_ns, resp_ns = _resolve_latency_ns(_config(latency_model="chi404_measured"))
    model = resolve_latency_model(regime="stress")
    tick_us = resolve_offensive_tick_to_send_us()
    assert entry_ns == round(
        (float(model["order_entry_latency_ms"]) + tick_us / 1000.0) * 1_000_000
    )
    assert resp_ns == round(float(model["order_response_latency_ms"]) * 1_000_000)
    # Round-trip authority the model derives from must itself be measured.
    summary = json.loads(DEFAULT_CHI404_SUMMARY.read_text(encoding="utf-8"))
    p99_ms, measured, _source = resolve_order_ack_ms(summary)
    assert measured is True
    assert isinstance(p99_ms, float) and p99_ms > 0
    # Band law [0.5, 10] ms on both resolved legs.
    assert 500_000 <= entry_ns <= 10_000_000
    assert 500_000 <= resp_ns <= 10_000_000


def test_campaign_constant_mode_requires_explicit_values(tmp_path: Path) -> None:
    module = _load_campaign_module()
    with pytest.raises(ValueError, match="requires BOTH"):
        module.run_campaign(
            manifest_path=tmp_path / "campaign.jsonl",
            out_root=tmp_path / "runs",
            latency_model="constant_order_latency",
        )
    # Explicit ns without constant mode is refused too: no half-declared runs.
    with pytest.raises(ValueError, match="only apply to"):
        module.run_campaign(
            manifest_path=tmp_path / "campaign.jsonl",
            out_root=tmp_path / "runs",
            entry_latency_ns=2_000_000,
        )
    # Out-of-band constants refuse the whole campaign up front, not row-by-row.
    with pytest.raises(ValueError, match="outside BLUEPRINT band"):
        module.run_campaign(
            manifest_path=tmp_path / "campaign.jsonl",
            out_root=tmp_path / "runs",
            latency_model="constant_order_latency",
            entry_latency_ns=100_000,
            response_latency_ns=2_000_000,
        )


def test_constant_mode_out_of_band_raises() -> None:
    # 100_000 ns (0.1 ms) — the old silent default — is below the band floor.
    with pytest.raises(ValueError, match="outside BLUEPRINT band"):
        _resolve_latency_ns(_config(entry_latency_ns=100_000, response_latency_ns=2_000_000))
    with pytest.raises(ValueError, match="outside BLUEPRINT band"):
        _resolve_latency_ns(_config(entry_latency_ns=2_000_000, response_latency_ns=20_000_000))


def test_constant_mode_without_values_fails_closed() -> None:
    with pytest.raises(HftBacktestOnlyPipelineError, match="requires explicit"):
        _resolve_latency_ns(_config())


def test_latency_report_carries_measured_receipts() -> None:
    config = _config(latency_model="chi404_measured")
    report = _latency_report(config)
    entry_ns, resp_ns = _resolve_latency_ns(config)
    assert report["latency_model"] == "chi404_measured"
    assert report["order_entry_latency_ns"] == entry_ns
    assert report["order_response_latency_ns"] == resp_ns
    assert report["order_entry_latency_source"].endswith("+offensive_tick_to_send_p99")
    assert report["order_response_latency_source"]
    assert report["offensive_tick_to_send_us_added"] == resolve_offensive_tick_to_send_us()
    assert report["cancel_latency_policy"] == "send_to_ack_proxy_until_cancel_ack_measured"
    assert report["cancel_latency_policy"] == CANCEL_LATENCY_POLICY


def test_latency_report_constant_receipts() -> None:
    report = _latency_report(_config(entry_latency_ns=2_000_000, response_latency_ns=3_000_000))
    assert report["latency_model"] == "constant_order_latency"
    assert report["order_entry_latency_ns"] == 2_000_000
    assert report["order_response_latency_ns"] == 3_000_000
    assert report["order_entry_latency_source"] == "cli_constant"
    assert report["order_response_latency_source"] == "cli_constant"
    assert report["offensive_tick_to_send_us_added"] == 0.0
    assert report["cancel_latency_policy"] == CANCEL_LATENCY_POLICY


def test_economics_stamp_includes_latency_model() -> None:
    module = _load_campaign_module()
    default = module._economics_stamp(None, None, "cpp")
    assert ":latency=chi404_measured:" in default
    constant = module._economics_stamp(
        None,
        None,
        "cpp",
        latency_model="constant_order_latency",
        entry_latency_ns=2_000_000,
        response_latency_ns=3_000_000,
    )
    assert ":latency=constant_order_latency@2000000/3000000:" in constant
    assert default != constant
