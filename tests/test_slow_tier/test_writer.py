"""Unit tests for src/writer.py — offline, no network, no ollama.

Covers:
- append_session_label writes a valid JSONL record
- record has all required fields
- advisory field is always True
- multiple appends produce multiple lines (one per call)
- record fields match input parameters
- JSON is valid and parseable
- prompt_template_hash has correct format (12 hex chars)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.config import SlowTierConfig
from llm_slow_tier.src.digest import Digest, SymbolDigest
from llm_slow_tier.src.labeler import LabelResult, PROMPT_TEMPLATE_HASH
from llm_slow_tier.src.verify import VerifierResult
from llm_slow_tier.src.writer import append_session_label


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> SlowTierConfig:
    return SlowTierConfig(
        artifact_root=str(tmp_path / "slow_tier"),
        model="test-model",
    )


def _make_digest(trade_date: str = "2026-06-10") -> Digest:
    sym_digest = SymbolDigest(
        symbol="MES",
        records=1000,
        trades=500,
        quotes=500,
        gap_flags=0,
        drops=0,
        reconnects=0,
        z_score=0.5,
        insufficient_baseline=False,
    )
    return Digest(
        trade_date=trade_date,
        symbols=["MES"],
        total_records=1000,
        total_trades=500,
        total_quotes=500,
        total_gap_flags=0,
        total_drops=0,
        total_reconnects=0,
        per_symbol={"MES": sym_digest},
        trade_count_zscores={"MES": 0.5},
        insufficient_baseline=[],
        n_symbols=1,
    )


def _make_label_result(label: str = "calm") -> LabelResult:
    return LabelResult(
        label=label,
        drivers=["low volume session"],
        confidence=0.85,
        raw_json='{"label": "calm", "drivers": ["low volume session"], "confidence": 0.85}',
    )


def _make_verifier_result(verdict: str = "accept", final_label: str = "calm") -> VerifierResult:
    return VerifierResult(
        verdict=verdict,
        final_label=final_label,
        reasons=[],
    )


def _empty_sources() -> dict:
    return {
        "calendar_hits": [],
        "gdelt_top_events": [],
        "gdelt_error": None,
        "offline": True,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAppendSessionLabel:

    def test_writes_valid_jsonl_line(self, tmp_path):
        cfg = _make_config(tmp_path)
        digest = _make_digest()
        label_result = _make_label_result()
        verifier_result = _make_verifier_result()

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=label_result,
            verifier_result=verifier_result,
            digest=digest,
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        assert out_path.is_file()
        lines = [l for l in out_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert isinstance(record, dict)

    def test_required_fields_present(self, tmp_path):
        cfg = _make_config(tmp_path)

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=_make_label_result(),
            verifier_result=_make_verifier_result(),
            digest=_make_digest(),
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        record = json.loads(out_path.read_text(encoding="utf-8").strip())
        required_keys = {
            "trade_date",
            "label",
            "drivers",
            "confidence",
            "verifier_verdict",
            "verifier_reasons",
            "model",
            "prompt_template_hash",
            "digest",
            "sources_summary",
            "generated_utc",
            "advisory",
        }
        missing = required_keys - set(record.keys())
        assert not missing, f"Missing fields: {missing}"

    def test_advisory_always_true(self, tmp_path):
        cfg = _make_config(tmp_path)

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=_make_label_result(),
            verifier_result=_make_verifier_result(),
            digest=_make_digest(),
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        record = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert record["advisory"] is True

    def test_multiple_appends_produce_multiple_lines(self, tmp_path):
        cfg = _make_config(tmp_path)

        for date in ("2026-06-10", "2026-06-11", "2026-06-12"):
            append_session_label(
                trade_date=date,
                label_result=_make_label_result(),
                verifier_result=_make_verifier_result(),
                digest=_make_digest(date),
                sources_summary=_empty_sources(),
                model="test-model",
                cfg=cfg,
            )

        out_path = Path(cfg.artifact_root) / "session_labels.jsonl"
        lines = [l for l in out_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 3
        dates = [json.loads(l)["trade_date"] for l in lines]
        assert dates == ["2026-06-10", "2026-06-11", "2026-06-12"]

    def test_label_matches_verifier_final_label(self, tmp_path):
        """The written 'label' field comes from verifier_result.final_label, not label_result.label."""
        cfg = _make_config(tmp_path)
        label_result = _make_label_result(label="calm")
        verifier_result = _make_verifier_result(verdict="conflict_review", final_label="conflict_review")

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=label_result,
            verifier_result=verifier_result,
            digest=_make_digest(),
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        record = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert record["label"] == "conflict_review"
        assert record["verifier_verdict"] == "conflict_review"

    def test_prompt_template_hash_format(self, tmp_path):
        """prompt_template_hash must be a 12-character hex string."""
        cfg = _make_config(tmp_path)

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=_make_label_result(),
            verifier_result=_make_verifier_result(),
            digest=_make_digest(),
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        record = json.loads(out_path.read_text(encoding="utf-8").strip())
        h = record["prompt_template_hash"]
        assert isinstance(h, str)
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h.lower())

    def test_prompt_template_hash_matches_module_constant(self, tmp_path):
        cfg = _make_config(tmp_path)

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=_make_label_result(),
            verifier_result=_make_verifier_result(),
            digest=_make_digest(),
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        record = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert record["prompt_template_hash"] == PROMPT_TEMPLATE_HASH

    def test_model_field_matches_argument(self, tmp_path):
        cfg = _make_config(tmp_path)
        model_name = "hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL"

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=_make_label_result(),
            verifier_result=_make_verifier_result(),
            digest=_make_digest(),
            sources_summary=_empty_sources(),
            model=model_name,
            cfg=cfg,
        )

        record = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert record["model"] == model_name

    def test_digest_serialized_in_record(self, tmp_path):
        cfg = _make_config(tmp_path)
        digest = _make_digest()

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=_make_label_result(),
            verifier_result=_make_verifier_result(),
            digest=digest,
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        record = json.loads(out_path.read_text(encoding="utf-8").strip())
        d = record["digest"]
        assert isinstance(d, dict)
        assert d["trade_date"] == "2026-06-10"
        assert d["n_symbols"] == 1
        assert "per_symbol" in d
        assert "trade_count_zscores" in d

    def test_creates_parent_directories(self, tmp_path):
        """append_session_label should create the artifact_root directory if missing."""
        cfg = SlowTierConfig(
            artifact_root=str(tmp_path / "deep" / "nested" / "slow_tier"),
            model="test-model",
        )

        out_path = append_session_label(
            trade_date="2026-06-10",
            label_result=_make_label_result(),
            verifier_result=_make_verifier_result(),
            digest=_make_digest(),
            sources_summary=_empty_sources(),
            model="test-model",
            cfg=cfg,
        )

        assert out_path.is_file()
