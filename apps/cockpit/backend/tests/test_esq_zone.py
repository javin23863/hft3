"""esq zone — cross-repo, read-only. Graceful-missing + stats-from-fixture.

Run from repo root:  python -m pytest apps/cockpit/backend/tests -q
"""
import json
from pathlib import Path

from apps.cockpit.backend import paths
from apps.cockpit.backend.aggregate import esq as esq_agg


def _write_jsonl(path: Path, *records: dict) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_esq_build_is_gray_when_esq_paths_missing(monkeypatch, tmp_path):
    missing_root = tmp_path / "no-such-esq"
    monkeypatch.setattr(paths, "ESQ_ROOT", missing_root)
    monkeypatch.setattr(paths, "ESQ_SHADOW_TRADES", missing_root / "runtime" / "shadow" / "trades.jsonl")
    monkeypatch.setattr(paths, "ESQ_SHADOW_LOG", missing_root / "runtime" / "shadow" / "log.jsonl")
    monkeypatch.setattr(paths, "ESQ_VALIDATION_DIR", missing_root / "runtime" / "validation")

    d = esq_agg.build()

    assert "health" in d
    assert d["health"] == esq_agg.GRAY
    assert d["heartbeat"] is None
    assert d["heartbeat_age_min"] is None
    assert d["stats"]["n_trades"] == 0
    assert d["equity_curve"] == []
    assert d["recent_trades"] == []
    assert d["audit"] is None
    assert d["sizing"] == {"trades_per_year": None}
    assert d["xmkt"] is None


def test_esq_build_computes_stats_from_fixture_trades(monkeypatch, tmp_path):
    shadow_dir = tmp_path / "runtime" / "shadow"
    shadow_dir.mkdir(parents=True)
    trades_path = shadow_dir / "trades.jsonl"
    _write_jsonl(
        trades_path,
        {
            "entry_ts": "2025-04-07 03:00:00", "exit_ts": "2025-04-07 09:00:00",
            "entry_px": 5218.75, "exit_px": 5200.0, "exit_reason": "LongSL",
            "ev": 0.1, "pnl_usd": -100.0,
        },
        {
            "entry_ts": "2025-04-08 03:00:00", "exit_ts": "2025-04-08 09:00:00",
            "entry_px": 5200.0, "exit_px": 5250.0, "exit_reason": "LongPT",
            "ev": 0.2, "pnl_usd": 250.0,
        },
        {
            "entry_ts": "2025-04-09 03:00:00", "exit_ts": "2025-04-09 09:00:00",
            "entry_px": 5250.0, "exit_px": 5230.0, "exit_reason": "LongSL2BE",
            "ev": 0.15, "pnl_usd": -20.0,
        },
    )
    log_path = shadow_dir / "log.jsonl"
    _write_jsonl(
        log_path,
        {
            "run_utc": paths.now_iso(), "last_bar": "2025-04-09 13:00:00", "close": 5230.0,
            "entry_signal": False, "ensemble_ev": None, "action": "none",
            "n_new_closed_trades": 3, "shadow_total_net": 130.0,
        },
    )
    validation_dir = tmp_path / "runtime" / "validation"
    validation_dir.mkdir(parents=True)
    (validation_dir / "20260101T000000-shadow-audit.json").write_text(
        json.dumps({"verdict": "PASS", "dormant": False, "bands": {"net_p5": -100.0}}),
        encoding="utf-8",
    )
    (validation_dir / "20260101T000000-sizing-memo.json").write_text(
        json.dumps({"trades_per_year": 89}), encoding="utf-8",
    )

    monkeypatch.setattr(paths, "ESQ_ROOT", tmp_path)
    monkeypatch.setattr(paths, "ESQ_SHADOW_TRADES", trades_path)
    monkeypatch.setattr(paths, "ESQ_SHADOW_LOG", log_path)
    monkeypatch.setattr(paths, "ESQ_VALIDATION_DIR", validation_dir)

    d = esq_agg.build()

    assert d["health"] == "green"
    assert d["stats"]["n_trades"] == 3
    assert d["stats"]["net_usd"] == 130.0
    assert d["stats"]["win_rate"] == round(1 / 3, 4)
    assert d["stats"]["profit_factor"] == round(250.0 / 120.0, 4)
    assert d["stats"]["max_dd_usd"] == 100.0  # peak 150 after trade2, trough -100 after trade1
    assert len(d["equity_curve"]) == 3
    assert d["equity_curve"][-1] == {"ts": "2025-04-09 09:00:00", "equity": 130.0}
    assert [t["exit_reason"] for t in d["recent_trades"]] == ["LongSL2BE", "LongPT", "LongSL"]  # newest first
    assert d["audit"]["verdict"] == "PASS"
    assert d["sizing"]["trades_per_year"] == 89
    assert d["xmkt"] is None
    assert d["heartbeat"]["shadow_total_net"] == 130.0
    assert d["heartbeat_age_min"] is not None and d["heartbeat_age_min"] < 1.0


def test_esq_build_is_red_when_audit_verdict_fail(monkeypatch, tmp_path):
    shadow_dir = tmp_path / "runtime" / "shadow"
    shadow_dir.mkdir(parents=True)
    trades_path = shadow_dir / "trades.jsonl"
    _write_jsonl(trades_path, {"exit_ts": "2025-04-07 09:00:00", "pnl_usd": 10.0})
    log_path = shadow_dir / "log.jsonl"
    _write_jsonl(log_path, {"run_utc": paths.now_iso(), "action": "none"})
    validation_dir = tmp_path / "runtime" / "validation"
    validation_dir.mkdir(parents=True)
    (validation_dir / "20260101T000000-shadow-audit.json").write_text(
        json.dumps({"verdict": "FAIL", "dormant": False}), encoding="utf-8",
    )

    monkeypatch.setattr(paths, "ESQ_ROOT", tmp_path)
    monkeypatch.setattr(paths, "ESQ_SHADOW_TRADES", trades_path)
    monkeypatch.setattr(paths, "ESQ_SHADOW_LOG", log_path)
    monkeypatch.setattr(paths, "ESQ_VALIDATION_DIR", validation_dir)

    d = esq_agg.build()

    assert d["health"] == "red"


def test_esq_build_is_red_when_heartbeat_stale(monkeypatch, tmp_path):
    shadow_dir = tmp_path / "runtime" / "shadow"
    shadow_dir.mkdir(parents=True)
    trades_path = shadow_dir / "trades.jsonl"
    _write_jsonl(trades_path, {"exit_ts": "2025-04-07 09:00:00", "pnl_usd": 10.0})
    log_path = shadow_dir / "log.jsonl"
    _write_jsonl(log_path, {"run_utc": "2020-01-01T00:00:00Z", "action": "none"})
    validation_dir = tmp_path / "runtime" / "validation"

    monkeypatch.setattr(paths, "ESQ_ROOT", tmp_path)
    monkeypatch.setattr(paths, "ESQ_SHADOW_TRADES", trades_path)
    monkeypatch.setattr(paths, "ESQ_SHADOW_LOG", log_path)
    monkeypatch.setattr(paths, "ESQ_VALIDATION_DIR", validation_dir)

    d = esq_agg.build()

    assert d["health"] == "red"
    assert d["heartbeat_age_min"] > 120
