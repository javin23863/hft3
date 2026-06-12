"""Unit tests for golden auto-seeding in src/golden.py.

Covers:
- Rule (a): macro_event + calendar hit → corroborated → golden file seeded
- Rule (a) does NOT fire without calendar hits
- Rule (b): war_escalation + high-z symbol + GDELT → corroborated → golden file seeded
- Rule (b) does NOT fire without GDELT
- Rule (b) does NOT fire without high-z symbol
- Rule (c): calm + all-quiet z + no gaps + no reconnects + sufficient baseline → seeded
- Rule (c) does NOT fire when z exceeds z_quiet
- Rule (c) does NOT fire when gap_flags > 0
- Rule (c) does NOT fire when reconnects > 0
- Rule (c) does NOT fire when too many symbols have insufficient_baseline
- Rule (d): mixed label → not corroborated → no file written
- Human seniority: auto never overwrites a file with source != "auto_corroborated"
- Auto-overwrite allowed: auto can overwrite an existing auto_corroborated file
- conflict_review → review_queue.jsonl appended (not a golden file)
- conflict_review → no golden file written
- verdict != accept → no seeding
- run_eval composition breakdown includes n_human and n_auto
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.config import SlowTierConfig
from llm_slow_tier.src.golden import (
    _check_corroboration,
    maybe_auto_seed_golden,
    run_eval,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> SlowTierConfig:
    return SlowTierConfig(
        artifact_root=str(tmp_path / "slow_tier"),
        model="test-model",
        z_stress=3.0,
        z_quiet=1.0,
    )


def _make_digest(
    *,
    trade_date: str = "2026-06-10",
    symbols: Optional[List[str]] = None,
    zscores: Optional[Dict[str, float]] = None,
    total_gap_flags: int = 0,
    total_reconnects: int = 0,
    insufficient_baseline: Optional[List[str]] = None,
) -> Any:
    """Return a duck-typed digest namespace (avoids heavy Digest construction)."""
    symbols = symbols or ["MES"]
    zscores = zscores or {s: 0.0 for s in symbols}
    insufficient_baseline = insufficient_baseline or []
    return SimpleNamespace(
        trade_date=trade_date,
        symbols=symbols,
        n_symbols=len(symbols),
        trade_count_zscores=zscores,
        total_gap_flags=total_gap_flags,
        total_reconnects=total_reconnects,
        insufficient_baseline=insufficient_baseline,
    )


def _make_sources(
    *,
    calendar_hits: Optional[List[str]] = None,
    gdelt_top_events: Optional[List[Any]] = None,
    gdelt_error: Optional[str] = None,
    offline: bool = False,
) -> Dict[str, Any]:
    return {
        "calendar_hits": calendar_hits or [],
        "gdelt_top_events": gdelt_top_events or [],
        "gdelt_error": gdelt_error,
        "offline": offline,
    }


# ---------------------------------------------------------------------------
# Tests: _check_corroboration
# ---------------------------------------------------------------------------

class TestCheckCorroboration:

    def test_rule_a_fires_macro_event_with_calendar_hit(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        sources = _make_sources(calendar_hits=["FOMC_2026-06-10"])
        corroborated, rule, evidence = _check_corroboration("macro_event", digest, sources, cfg)
        assert corroborated is True
        assert rule == "a"
        assert any("rule_a" in e for e in evidence)

    def test_rule_a_does_not_fire_without_calendar_hits(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        sources = _make_sources(calendar_hits=[])
        corroborated, rule, _ = _check_corroboration("macro_event", digest, sources, cfg)
        assert corroborated is False
        assert rule == "d"

    def test_rule_b_fires_war_escalation_high_z_and_gdelt(self):
        cfg = SlowTierConfig(z_stress=3.0)
        digest = _make_digest(zscores={"MES": 4.0, "NQ": 0.5})
        sources = _make_sources(gdelt_top_events=[{"actor": "RUS", "event_code": "190", "goldstein_scale": -10.0, "avg_tone": -5.0, "num_mentions": 100}])
        corroborated, rule, evidence = _check_corroboration("war_escalation", digest, sources, cfg)
        assert corroborated is True
        assert rule == "b"
        assert any("rule_b" in e for e in evidence)

    def test_rule_b_does_not_fire_without_gdelt(self):
        cfg = SlowTierConfig(z_stress=3.0)
        digest = _make_digest(zscores={"MES": 5.0})
        sources = _make_sources(gdelt_top_events=[])
        corroborated, rule, _ = _check_corroboration("war_escalation", digest, sources, cfg)
        assert corroborated is False
        assert rule == "d"

    def test_rule_b_does_not_fire_without_high_z(self):
        cfg = SlowTierConfig(z_stress=3.0)
        digest = _make_digest(zscores={"MES": 1.0, "NQ": 2.0})  # all below z_stress=3.0
        sources = _make_sources(gdelt_top_events=[{"actor": "RUS"}])
        corroborated, rule, _ = _check_corroboration("war_escalation", digest, sources, cfg)
        assert corroborated is False
        assert rule == "d"

    def test_rule_c_fires_calm_all_quiet(self):
        cfg = SlowTierConfig(z_quiet=1.0)
        digest = _make_digest(
            zscores={"MES": 0.5, "NQ": -0.3},
            total_gap_flags=0,
            total_reconnects=0,
            insufficient_baseline=[],
        )
        sources = _make_sources()
        corroborated, rule, evidence = _check_corroboration("calm", digest, sources, cfg)
        assert corroborated is True
        assert rule == "c"
        assert any("rule_c" in e for e in evidence)

    def test_rule_c_does_not_fire_z_exceeds_z_quiet(self):
        cfg = SlowTierConfig(z_quiet=1.0)
        digest = _make_digest(zscores={"MES": 1.5}, total_gap_flags=0, total_reconnects=0)
        sources = _make_sources()
        corroborated, rule, _ = _check_corroboration("calm", digest, sources, cfg)
        assert corroborated is False

    def test_rule_c_does_not_fire_with_gap_flags(self):
        cfg = SlowTierConfig(z_quiet=1.0)
        digest = _make_digest(zscores={"MES": 0.0}, total_gap_flags=1, total_reconnects=0)
        sources = _make_sources()
        corroborated, rule, _ = _check_corroboration("calm", digest, sources, cfg)
        assert corroborated is False

    def test_rule_c_does_not_fire_with_reconnects(self):
        cfg = SlowTierConfig(z_quiet=1.0)
        digest = _make_digest(zscores={"MES": 0.0}, total_gap_flags=0, total_reconnects=1)
        sources = _make_sources()
        corroborated, rule, _ = _check_corroboration("calm", digest, sources, cfg)
        assert corroborated is False

    def test_rule_c_does_not_fire_too_many_insufficient_baseline(self):
        """More than half of symbols have insufficient_baseline -> rule (c) blocked."""
        cfg = SlowTierConfig(z_quiet=1.0)
        # 2 symbols, both insufficient -> 2/2 > 0.5 threshold
        digest = _make_digest(
            symbols=["MES", "NQ"],
            zscores={"MES": 0.0, "NQ": 0.0},
            total_gap_flags=0,
            total_reconnects=0,
            insufficient_baseline=["MES", "NQ"],
        )
        sources = _make_sources()
        corroborated, rule, _ = _check_corroboration("calm", digest, sources, cfg)
        assert corroborated is False

    def test_rule_d_for_mixed_label(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        sources = _make_sources(calendar_hits=["something"])
        corroborated, rule, _ = _check_corroboration("mixed", digest, sources, cfg)
        assert corroborated is False
        assert rule == "d"

    def test_rule_d_for_conflict_review_label(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        sources = _make_sources()
        corroborated, rule, _ = _check_corroboration("conflict_review", digest, sources, cfg)
        assert corroborated is False
        assert rule == "d"


# ---------------------------------------------------------------------------
# Tests: maybe_auto_seed_golden — file writing and seniority
# ---------------------------------------------------------------------------

class TestMaybeAutoSeedGolden:

    def test_seeds_macro_event_with_calendar_hit(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        digest = _make_digest(zscores={"MES": 0.0})
        sources = _make_sources(calendar_hits=["NFP_2026-06-10"])

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="macro_event",
            verifier_verdict="accept",
            verifier_reasons=[],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "seeded"
        golden_path = Path(cfg.artifact_root) / "golden" / "2026-06-10.json"
        assert golden_path.is_file()
        data = json.loads(golden_path.read_text(encoding="utf-8"))
        assert data["label"] == "macro_event"
        assert data["source"] == "auto_corroborated"
        assert "evidence" in data

    def test_seeds_war_escalation(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        digest = _make_digest(zscores={"MES": 4.5})
        sources = _make_sources(gdelt_top_events=[{"actor": "RUS"}])

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="war_escalation",
            verifier_verdict="accept",
            verifier_reasons=[],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "seeded"
        golden_path = Path(cfg.artifact_root) / "golden" / "2026-06-10.json"
        data = json.loads(golden_path.read_text(encoding="utf-8"))
        assert data["label"] == "war_escalation"
        assert data["source"] == "auto_corroborated"

    def test_seeds_calm(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        digest = _make_digest(zscores={"MES": 0.2}, total_gap_flags=0, total_reconnects=0)
        sources = _make_sources()

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="calm",
            verifier_verdict="accept",
            verifier_reasons=[],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "seeded"

    def test_not_corroborated_mixed_label(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        digest = _make_digest()
        sources = _make_sources()

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="mixed",
            verifier_verdict="accept",
            verifier_reasons=[],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "not_corroborated"
        golden_path = Path(cfg.artifact_root) / "golden" / "2026-06-10.json"
        assert not golden_path.exists()

    def test_human_file_is_senior_no_overwrite(self, tmp_path):
        """Auto must NOT overwrite a human-authored golden file."""
        cfg = _make_cfg(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        golden_dir.mkdir(parents=True, exist_ok=True)
        golden_path = golden_dir / "2026-06-10.json"
        # Write a human file (no "source" field = human)
        human_data = {"date": "2026-06-10", "label": "calm", "notes": "hand-labeled"}
        golden_path.write_text(json.dumps(human_data), encoding="utf-8")

        digest = _make_digest(zscores={"MES": 0.1})
        sources = _make_sources(calendar_hits=["NFP_2026-06-10"])

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="macro_event",
            verifier_verdict="accept",
            verifier_reasons=[],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "skipped_human_senior"
        # File must be unchanged
        data = json.loads(golden_path.read_text(encoding="utf-8"))
        assert data.get("notes") == "hand-labeled"
        assert "source" not in data

    def test_auto_can_overwrite_existing_auto_file(self, tmp_path):
        """Auto is allowed to overwrite a previous auto_corroborated file."""
        cfg = _make_cfg(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        golden_dir.mkdir(parents=True, exist_ok=True)
        golden_path = golden_dir / "2026-06-10.json"
        # Write an existing auto file
        old_data = {"date": "2026-06-10", "label": "macro_event", "source": "auto_corroborated", "evidence": ["old"]}
        golden_path.write_text(json.dumps(old_data), encoding="utf-8")

        digest = _make_digest(zscores={"MES": 0.1})
        sources = _make_sources(calendar_hits=["CPI_2026-06-10"])

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="macro_event",
            verifier_verdict="accept",
            verifier_reasons=[],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "seeded"
        data = json.loads(golden_path.read_text(encoding="utf-8"))
        # Should contain updated evidence
        assert data["source"] == "auto_corroborated"
        assert any("CPI" in e or "calendar" in e for e in data["evidence"])

    def test_conflict_review_queued_not_seeded(self, tmp_path):
        """conflict_review verdict → review_queue.jsonl written, no golden file."""
        cfg = _make_cfg(tmp_path)
        digest = _make_digest()
        sources = _make_sources(calendar_hits=["FOMC"])

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="conflict_review",
            verifier_verdict="conflict_review",
            verifier_reasons=["tape_contradicts_calm"],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "review_queued"
        # No golden file
        golden_path = Path(cfg.artifact_root) / "golden" / "2026-06-10.json"
        assert not golden_path.exists()
        # Review queue has entry
        queue_path = Path(cfg.artifact_root) / "golden" / "review_queue.jsonl"
        assert queue_path.is_file()
        entries = [json.loads(l) for l in queue_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(entries) == 1
        assert entries[0]["date"] == "2026-06-10"
        assert "tape_contradicts_calm" in entries[0]["verifier_reasons"]

    def test_review_queue_appends_multiple_entries(self, tmp_path):
        """Multiple conflict_review calls append to the same queue file."""
        cfg = _make_cfg(tmp_path)
        digest = _make_digest()
        sources = _make_sources()

        for date in ["2026-06-10", "2026-06-11", "2026-06-12"]:
            maybe_auto_seed_golden(
                trade_date=date,
                label="conflict_review",
                verifier_verdict="conflict_review",
                verifier_reasons=["some_reason"],
                digest=digest,
                sources_summary=sources,
                cfg=cfg,
            )

        queue_path = Path(cfg.artifact_root) / "golden" / "review_queue.jsonl"
        entries = [json.loads(l) for l in queue_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(entries) == 3
        dates = {e["date"] for e in entries}
        assert dates == {"2026-06-10", "2026-06-11", "2026-06-12"}

    def test_non_accept_non_conflict_verdict_not_seeded(self, tmp_path):
        """An unexpected verdict does not seed or queue anything."""
        cfg = _make_cfg(tmp_path)
        digest = _make_digest()
        sources = _make_sources(calendar_hits=["X"])

        status = maybe_auto_seed_golden(
            trade_date="2026-06-10",
            label="macro_event",
            verifier_verdict="unknown_verdict",  # not 'accept' or 'conflict_review'
            verifier_reasons=[],
            digest=digest,
            sources_summary=sources,
            cfg=cfg,
        )

        assert status == "not_corroborated"


# ---------------------------------------------------------------------------
# Tests: run_eval composition breakdown
# ---------------------------------------------------------------------------

class TestRunEvalComposition:

    def _write_golden_file(self, golden_dir: Path, date: str, label: str, source: Optional[str] = None) -> None:
        golden_dir.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {"date": date, "label": label}
        if source is not None:
            data["source"] = source
        (golden_dir / f"{date}.json").write_text(json.dumps(data), encoding="utf-8")

    def _write_session_label(self, labels_path: Path, trade_date: str, label: str, verdict: str) -> None:
        labels_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"trade_date": trade_date, "label": label, "verifier_verdict": verdict, "drivers": [], "confidence": 0.8, "model": "test"}
        with open(labels_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def test_composition_all_human(self, tmp_path, monkeypatch):
        cfg = SlowTierConfig(artifact_root=str(tmp_path / "slow_tier"), model="test")
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
        monkeypatch.chdir(tmp_path)

        for i in range(1, 4):
            date = f"2026-06-0{i}"
            self._write_golden_file(golden_dir, date, "calm")  # no source field → human
            self._write_session_label(labels_path, date, "calm", "accept")

        result = run_eval(golden_dir, cfg)
        assert result["n_human"] == 3
        assert result["n_auto"] == 0

    def test_composition_mixed(self, tmp_path, monkeypatch):
        cfg = SlowTierConfig(artifact_root=str(tmp_path / "slow_tier"), model="test")
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
        monkeypatch.chdir(tmp_path)

        # 2 human + 1 auto
        for i in range(1, 3):
            date = f"2026-06-0{i}"
            self._write_golden_file(golden_dir, date, "calm")  # human
            self._write_session_label(labels_path, date, "calm", "accept")

        auto_date = "2026-06-03"
        self._write_golden_file(golden_dir, auto_date, "macro_event", source="auto_corroborated")
        self._write_session_label(labels_path, auto_date, "macro_event", "accept")

        result = run_eval(golden_dir, cfg)
        assert result["n_human"] == 2
        assert result["n_auto"] == 1
        assert "n_human" in result
        assert "n_auto" in result
