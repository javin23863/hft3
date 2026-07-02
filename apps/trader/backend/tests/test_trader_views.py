from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
for _p in (str(_REPO), str(_REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import apps.trader.backend.data as data
import apps.trader.backend.views as views
from scripts.build_run_index import build_run_index  # noqa: E402  (repo root on path)


def _write_run_dir(root: Path, run_id: str, model: str, event: str, realized, *, gate3="", gate4="", promo=False) -> None:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"run_id": run_id, "canonical_model_id": model, "symbol": "MES",
                    "event_id": event, "strategy_params": {"signal_threshold": 0.05}}),
        encoding="utf-8",
    )
    stats = {
        "run_id": run_id,
        "mechanical_validity_status": "pass",
        "economic_result_status": "pass" if isinstance(realized, (int, float)) and realized > 0 else "observe",
        "realized_closed_trade_pnl": realized,
        "exit_reason": "take_profit",
        "exit_leg_enabled": True,
        "orders_submitted": 1,
        "fills_count": 2,
        "fill_rate": 1.0,
    }
    (run_dir / "stats_summary.json").write_text(json.dumps(stats), encoding="utf-8")
    (run_dir / "data_validation.json").write_text(
        json.dumps({"data_validation_status": "pass"}), encoding="utf-8"
    )
    if gate3:
        (run_dir / "gate3_sensitivity.json").write_text(
            json.dumps({"status": gate3, "min_realized_closed_trade_pnl": realized,
                        "axes_unavailable_upstream": []}),
            encoding="utf-8",
        )
    if gate4:
        (run_dir / "robustness_report.json").write_text(
            json.dumps({"status": gate4, "psr": 0.78, "dsr": 0.47, "cscv": {"pbo": 0.5}}),
            encoding="utf-8",
        )
    (run_dir / "promotion_decision.json").write_text(
        json.dumps({"decision": "promote" if promo else "observe", "promotion_allowed": promo}),
        encoding="utf-8",
    )


def _build_index(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "hbt_runs"
    _write_run_dir(root, "run_a", "SECOND_WAVE_CONTINUATION", "CPI_2024_09_11_TIGHT", 20.75, gate3="pass", gate4="fail")
    _write_run_dir(root, "run_b", "SECOND_WAVE_CONTINUATION", "CPI_2025_09_11_TIGHT", -0.75)
    _write_run_dir(root, "run_c", "STOP_RUN_EXHAUSTION_FADE", "NFP_2025_01_10_TIGHT", 3.5, gate3="pass", gate4="pass", promo=True)
    out = tmp_path / "index.jsonl"
    build_run_index([root], out)
    monkeypatch.setattr(data, "RUN_INDEX_PATH", out)


def test_funnel_counts_chain_stages(tmp_path: Path, monkeypatch) -> None:
    _build_index(tmp_path, monkeypatch)
    funnel = views.build_funnel()
    assert funnel["blocked"] is False
    assert funnel["totals"]["runs"] == 3
    assert funnel["totals"]["realized_pnl"] == 3
    assert funnel["totals"]["economic_pass"] == 2
    assert funnel["totals"]["gate3_pass"] == 2
    assert funnel["totals"]["gate4_pass"] == 1
    assert funnel["totals"]["promotion_allowed"] == 1
    assert funnel["evidence"]["sha256"]


def test_models_aggregates_per_model(tmp_path: Path, monkeypatch) -> None:
    _build_index(tmp_path, monkeypatch)
    payload = views.build_models()
    assert payload["blocked"] is False
    by_id = {m["canonical_model_id"]: m for m in payload["models"]}
    swc = by_id["SECOND_WAVE_CONTINUATION"]
    assert swc["runs"] == 2
    assert swc["realized_total"] == 20.0
    assert swc["gate4_any_pass"] is False
    assert by_id["STOP_RUN_EXHAUSTION_FADE"]["promotion_any_allowed"] is True


def test_model_detail_carries_receipts(tmp_path: Path, monkeypatch) -> None:
    _build_index(tmp_path, monkeypatch)
    detail = views.build_model_detail("SECOND_WAVE_CONTINUATION")
    assert detail["blocked"] is False
    assert len(detail["runs"]) == 2
    for run in detail["runs"]:
        assert run["receipt"]["stats_summary_sha256"]
        assert run["receipt"]["artifact_dir"]


def test_missing_index_renders_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(data, "RUN_INDEX_PATH", tmp_path / "absent.jsonl")
    funnel = views.build_funnel()
    assert funnel["blocked"] is True
    assert "run index not built" in funnel["reason"]


def test_tampered_index_renders_blocked(tmp_path: Path, monkeypatch) -> None:
    _build_index(tmp_path, monkeypatch)
    index_path = tmp_path / "index.jsonl"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    # Tamper a data row without regenerating the self-check hash.
    lines[0] = lines[0].replace("20.75", "99999.0")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    funnel = views.build_funnel()
    assert funnel["blocked"] is True
    assert funnel["reason"].startswith("index_self_check_hash_mismatch")


def test_unknown_model_blocked(tmp_path: Path, monkeypatch) -> None:
    _build_index(tmp_path, monkeypatch)
    detail = views.build_model_detail("NO_SUCH_MODEL")
    assert detail["blocked"] is True


def test_lifecycle_empty_state_honest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(data, "LIFECYCLE_REGISTRY", tmp_path / "absent.json")
    monkeypatch.setattr(data, "LIFECYCLE_TRANSITIONS", tmp_path / "absent.jsonl")
    payload = views.build_lifecycle()
    assert payload["blocked"] is False
    assert payload["empty_state"] is True
    assert "not created yet" in payload["note"]
