"""Unit tests for src/verify.py — offline, no network, no ollama.

Covers:
- Accept path: valid label, tape consistent with model claim
- Calm override: z-score above z_stress -> conflict_review
- Calm override: gap_flags > 0 -> conflict_review
- Calm override: reconnects > 2 -> conflict_review
- Stress-but-quiet: war_escalation with all-quiet tape -> conflict_review
- Stress-but-quiet: macro_event with all-quiet tape -> conflict_review
- Stress-but-quiet does NOT fire when calendar hits present
- Stress-but-quiet does NOT fire when GDELT present
- Schema reject: invalid label
- Schema reject: drivers list > 5
- Schema reject: confidence out of range
- Parse error passthrough
"""

from __future__ import annotations

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
from llm_slow_tier.src.labeler import LabelResult
from llm_slow_tier.src.verify import VerifierResult, verify_label_result


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> SlowTierConfig:
    cfg = SlowTierConfig()
    for k, v in overrides.items():
        object.__setattr__(cfg, k, v)
    return cfg


def _make_digest(
    *,
    trade_date: str = "2026-06-10",
    symbols: list[str] | None = None,
    total_gap_flags: int = 0,
    total_reconnects: int = 0,
    zscores: dict[str, float] | None = None,
) -> Digest:
    symbols = symbols or ["MES"]
    zscores = zscores or {s: 0.0 for s in symbols}

    per_symbol = {}
    for sym in symbols:
        per_symbol[sym] = SymbolDigest(
            symbol=sym,
            records=1000,
            trades=500,
            quotes=500,
            gap_flags=total_gap_flags if sym == symbols[0] else 0,
            drops=0,
            reconnects=total_reconnects if sym == symbols[0] else 0,
            z_score=zscores.get(sym, 0.0),
            insufficient_baseline=False,
        )

    return Digest(
        trade_date=trade_date,
        symbols=symbols,
        total_records=1000,
        total_trades=500,
        total_quotes=500,
        total_gap_flags=total_gap_flags,
        total_drops=0,
        total_reconnects=total_reconnects,
        per_symbol=per_symbol,
        trade_count_zscores=zscores,
        insufficient_baseline=[],
        n_symbols=len(symbols),
    )


def _make_label_result(
    label: str = "calm",
    drivers: list[str] | None = None,
    confidence: float = 0.8,
    parse_error: str | None = None,
) -> LabelResult:
    return LabelResult(
        label=label,
        drivers=drivers or ["no notable driver"],
        confidence=confidence,
        raw_json='{"label": "calm"}',
        parse_error=parse_error,
    )


def _empty_sources() -> dict:
    return {
        "calendar_hits": [],
        "gdelt_top_events": [],
        "gdelt_error": None,
        "offline": True,
    }


# ---------------------------------------------------------------------------
# Accept path
# ---------------------------------------------------------------------------

class TestAcceptPath:

    def test_calm_with_quiet_tape_accepted(self):
        cfg = SlowTierConfig()
        digest = _make_digest(zscores={"MES": 0.5}, total_gap_flags=0, total_reconnects=0)
        result = _make_label_result(label="calm")
        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "accept"
        assert vr.final_label == "calm"
        assert vr.reasons == []

    def test_mixed_label_accepted(self):
        cfg = SlowTierConfig()
        digest = _make_digest(zscores={"MES": 2.0})
        result = _make_label_result(label="mixed")
        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "accept"
        assert vr.final_label == "mixed"

    def test_macro_event_with_calendar_hit_accepted(self):
        cfg = SlowTierConfig()
        digest = _make_digest(zscores={"MES": 2.5})
        result = _make_label_result(label="macro_event")
        sources = {"calendar_hits": ["NFP"], "gdelt_top_events": [], "offline": False, "gdelt_error": None}
        vr = verify_label_result(result, digest, sources, cfg)

        assert vr.verdict == "accept"
        assert vr.final_label == "macro_event"

    def test_war_escalation_with_gdelt_accepted(self):
        cfg = SlowTierConfig()
        digest = _make_digest(zscores={"MES": 0.5})
        result = _make_label_result(label="war_escalation")
        sources = {
            "calendar_hits": [],
            "gdelt_top_events": [{"actor": "RUS", "event_code": "19", "goldstein_scale": -10.0, "avg_tone": -5.0, "num_mentions": 200}],
            "offline": False,
            "gdelt_error": None,
        }
        vr = verify_label_result(result, digest, sources, cfg)

        assert vr.verdict == "accept"
        assert vr.final_label == "war_escalation"


# ---------------------------------------------------------------------------
# Calm override
# ---------------------------------------------------------------------------

class TestCalmOverride:

    def test_calm_with_high_zscore_forced_to_conflict_review(self):
        cfg = SlowTierConfig()  # z_stress = 3.0
        digest = _make_digest(zscores={"MES": 4.0})  # above z_stress
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert vr.final_label == "conflict_review"
        assert "tape_contradicts_calm" in vr.reasons

    def test_calm_with_gap_flags_forced_to_conflict_review(self):
        cfg = SlowTierConfig()
        digest = _make_digest(total_gap_flags=1, zscores={"MES": 0.0})
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert "tape_contradicts_calm" in vr.reasons

    def test_calm_with_too_many_reconnects_forced_to_conflict_review(self):
        cfg = SlowTierConfig()
        digest = _make_digest(total_reconnects=3, zscores={"MES": 0.0})
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert "tape_contradicts_calm" in vr.reasons

    def test_calm_exactly_2_reconnects_not_overridden(self):
        """Reconnects == 2 does not trigger (threshold is > 2)."""
        cfg = SlowTierConfig()
        digest = _make_digest(total_reconnects=2, zscores={"MES": 0.0})
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)
        assert vr.verdict == "accept"

    def test_calm_z_exactly_at_z_stress_not_overridden(self):
        """z == z_stress (3.0) is not above it — should accept."""
        cfg = SlowTierConfig()  # z_stress=3.0
        # z_score=3.0 exactly; condition is > not >=
        digest = _make_digest(zscores={"MES": 3.0})
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)
        assert vr.verdict == "accept"

    def test_calm_override_uses_configured_z_stress(self):
        """Custom z_stress threshold is respected."""
        cfg = _make_config(z_stress=1.5)
        digest = _make_digest(zscores={"MES": 2.0})  # > 1.5
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)
        assert vr.verdict == "conflict_review"


# ---------------------------------------------------------------------------
# Stress-but-quiet override
# ---------------------------------------------------------------------------

class TestStressButQuietOverride:

    def test_war_escalation_all_quiet_forced_to_conflict_review(self):
        cfg = SlowTierConfig()  # z_quiet=1.0
        digest = _make_digest(zscores={"MES": 0.5})  # all < z_quiet
        result = _make_label_result(label="war_escalation")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert vr.final_label == "conflict_review"
        assert "tape_does_not_support_stress_label" in vr.reasons

    def test_macro_event_all_quiet_forced_to_conflict_review(self):
        cfg = SlowTierConfig()
        digest = _make_digest(zscores={"MES": 0.3})
        result = _make_label_result(label="macro_event")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert "tape_does_not_support_stress_label" in vr.reasons

    def test_stress_not_overridden_when_calendar_hit_present(self):
        cfg = SlowTierConfig()
        digest = _make_digest(zscores={"MES": 0.5})
        result = _make_label_result(label="macro_event")
        sources = {"calendar_hits": ["NFP"], "gdelt_top_events": [], "offline": False, "gdelt_error": None}

        vr = verify_label_result(result, digest, sources, cfg)
        assert vr.verdict == "accept"

    def test_stress_not_overridden_when_gdelt_present(self):
        cfg = SlowTierConfig()
        digest = _make_digest(zscores={"MES": 0.5})
        result = _make_label_result(label="war_escalation")
        sources = {
            "calendar_hits": [],
            "gdelt_top_events": [{"actor": "RUS", "event_code": "19"}],
            "offline": False,
            "gdelt_error": None,
        }

        vr = verify_label_result(result, digest, sources, cfg)
        assert vr.verdict == "accept"

    def test_stress_not_overridden_when_z_above_z_quiet(self):
        """If any z-score >= z_quiet, tape shows some activity -> accept stress label."""
        cfg = SlowTierConfig()  # z_quiet=1.0
        digest = _make_digest(zscores={"MES": 1.5, "NQ": 0.5})
        result = _make_label_result(label="war_escalation")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)
        assert vr.verdict == "accept"

    def test_stress_override_uses_configured_z_quiet(self):
        """Custom z_quiet threshold is respected."""
        cfg = _make_config(z_quiet=2.0)
        # z=1.5 is below new z_quiet=2.0 -> conflict
        digest = _make_digest(zscores={"MES": 1.5})
        result = _make_label_result(label="macro_event")

        vr = verify_label_result(result, digest, _empty_sources(), cfg)
        assert vr.verdict == "conflict_review"


# ---------------------------------------------------------------------------
# Schema rejects
# ---------------------------------------------------------------------------

class TestSchemaRejects:

    def test_invalid_label_rejected(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        result = LabelResult(
            label="INVALID_LABEL",
            drivers=["something"],
            confidence=0.7,
            raw_json="{}",
        )
        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert any("invalid_label" in r for r in vr.reasons)

    def test_too_many_drivers_rejected(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        result = LabelResult(
            label="calm",
            drivers=["a", "b", "c", "d", "e", "f"],  # 6 > max 5
            confidence=0.5,
            raw_json="{}",
        )
        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert any("drivers_too_many" in r for r in vr.reasons)

    def test_confidence_below_zero_rejected(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        result = LabelResult(
            label="calm",
            drivers=["x"],
            confidence=-0.1,
            raw_json="{}",
        )
        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert any("confidence_out_of_range" in r for r in vr.reasons)

    def test_confidence_above_one_rejected(self):
        cfg = SlowTierConfig()
        digest = _make_digest()
        result = LabelResult(
            label="calm",
            drivers=["x"],
            confidence=1.1,
            raw_json="{}",
        )
        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"

    def test_parse_error_propagated_to_conflict_review(self):
        """A parse_error on LabelResult -> schema check catches it -> conflict_review."""
        cfg = SlowTierConfig()
        digest = _make_digest()
        result = LabelResult(
            label="conflict_review",
            drivers=[],
            confidence=0.0,
            raw_json="garbage text",
            parse_error="json_decode_error: ...",
        )
        vr = verify_label_result(result, digest, _empty_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert any("parse_error" in r for r in vr.reasons)


# ---------------------------------------------------------------------------
# Insufficient-evidence override (Rule 4)
# ---------------------------------------------------------------------------

class TestInsufficientEvidenceOverride:
    """Rule 4: fires when > threshold fraction of symbols lack baseline AND
    there is no calendar hit AND (no GDELT records OR a gdelt_error note).

    Fires regardless of the model label.
    """

    def _make_all_insufficient_digest(self, symbols: list[str] | None = None) -> "Digest":
        """Build a digest where every symbol has the insufficient_baseline flag."""
        symbols = symbols or ["MES", "NQ"]
        zscores = {s: 0.0 for s in symbols}

        per_symbol = {}
        for sym in symbols:
            per_symbol[sym] = SymbolDigest(
                symbol=sym,
                records=1000,
                trades=500,
                quotes=500,
                gap_flags=0,
                drops=0,
                reconnects=0,
                z_score=0.0,
                insufficient_baseline=True,
            )

        return Digest(
            trade_date="2026-06-10",
            symbols=symbols,
            total_records=1000,
            total_trades=500,
            total_quotes=500,
            total_gap_flags=0,
            total_drops=0,
            total_reconnects=0,
            per_symbol=per_symbol,
            trade_count_zscores=zscores,
            insufficient_baseline=list(symbols),  # all flagged
            n_symbols=len(symbols),
        )

    def _no_sources(self) -> dict:
        """No calendar, no GDELT records, no error — represents pure offline/empty case."""
        return {
            "calendar_hits": [],
            "gdelt_top_events": [],
            "gdelt_error": None,
            "offline": True,
        }

    def _sources_with_gdelt_error(self) -> dict:
        """GDELT fetch failed — counts as 'no GDELT' for the rule."""
        return {
            "calendar_hits": [],
            "gdelt_top_events": [],
            "gdelt_error": "gdelt_error: HTTP 404 Not Found",
            "offline": False,
        }

    # --- rule fires ---

    def test_fires_for_calm_label_no_baseline_no_sources(self):
        """Rule fires even when model says 'calm'."""
        cfg = SlowTierConfig()
        digest = self._make_all_insufficient_digest()
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert vr.final_label == "conflict_review"
        assert any("insufficient_evidence" in r for r in vr.reasons)

    def test_fires_for_war_escalation_no_baseline_no_sources(self):
        """Conflict_review is produced for war_escalation with all-insufficient
        baseline and no external sources.

        For war_escalation, Rule 3 (stress-but-quiet) fires first because
        z-scores are all 0 (below z_quiet), so the conflict_review reason is
        'tape_does_not_support_stress_label'.  The session is still correctly
        rejected — we assert only on verdict here, not on the specific rule.
        """
        cfg = SlowTierConfig()
        digest = self._make_all_insufficient_digest()
        result = _make_label_result(label="war_escalation")

        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert vr.final_label == "conflict_review"

    def test_fires_for_war_escalation_with_elevated_z_no_baseline_no_sources(self):
        """Rule 4 fires for war_escalation when z-scores are elevated (Rule 3
        cannot pre-empt) but all symbols lack baseline and no external sources."""
        cfg = SlowTierConfig()  # z_quiet=1.0

        symbols = ["MES", "NQ"]
        # z-scores above z_quiet so Rule 3 (stress-but-quiet) does NOT fire
        zscores = {s: 2.0 for s in symbols}

        per_symbol = {}
        for sym in symbols:
            per_symbol[sym] = SymbolDigest(
                symbol=sym,
                records=1000,
                trades=500,
                quotes=500,
                gap_flags=0,
                drops=0,
                reconnects=0,
                z_score=2.0,
                insufficient_baseline=True,
            )

        digest = Digest(
            trade_date="2026-06-10",
            symbols=symbols,
            total_records=2000,
            total_trades=1000,
            total_quotes=1000,
            total_gap_flags=0,
            total_drops=0,
            total_reconnects=0,
            per_symbol=per_symbol,
            trade_count_zscores=zscores,
            insufficient_baseline=list(symbols),
            n_symbols=2,
        )

        result = _make_label_result(label="war_escalation")
        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert any("insufficient_evidence" in r for r in vr.reasons)

    def test_fires_when_gdelt_error_present(self):
        """gdelt_error note counts as 'no GDELT' — rule still fires."""
        cfg = SlowTierConfig()
        digest = self._make_all_insufficient_digest()
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, self._sources_with_gdelt_error(), cfg)

        assert vr.verdict == "conflict_review"
        assert any("insufficient_evidence" in r for r in vr.reasons)

    def test_reason_string_exact_text(self):
        """The reason string must match the specified exact text."""
        cfg = SlowTierConfig()
        digest = self._make_all_insufficient_digest()
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        assert "insufficient_evidence: no baseline, no news corroboration" in vr.reasons

    # --- rule does NOT fire ---

    def test_does_not_fire_when_calendar_hits_exist(self):
        """Calendar hit is external corroboration — rule must not fire."""
        cfg = SlowTierConfig()
        digest = self._make_all_insufficient_digest()
        result = _make_label_result(label="calm")
        sources = {
            "calendar_hits": ["NFP"],
            "gdelt_top_events": [],
            "gdelt_error": None,
            "offline": False,
        }

        vr = verify_label_result(result, digest, sources, cfg)

        assert vr.verdict == "accept"

    def test_does_not_fire_when_gdelt_records_exist(self):
        """GDELT records present — rule must not fire."""
        cfg = SlowTierConfig()
        digest = self._make_all_insufficient_digest()
        result = _make_label_result(label="calm")
        sources = {
            "calendar_hits": [],
            "gdelt_top_events": [
                {"title": "Oil surge", "domain": "reuters.com", "seendate": "20260610T120000Z"}
            ],
            "gdelt_error": None,
            "offline": False,
        }

        vr = verify_label_result(result, digest, sources, cfg)

        assert vr.verdict == "accept"

    def test_does_not_fire_when_baseline_sufficient(self):
        """Fewer than threshold fraction of symbols lack baseline — rule must not fire."""
        cfg = SlowTierConfig()  # verify_insufficient_baseline_fraction=0.5

        # 1 of 3 symbols has insufficient_baseline: fraction=0.33 < 0.5 -> no fire
        symbols = ["MES", "NQ", "RTY"]
        zscores = {s: 0.0 for s in symbols}

        per_symbol = {}
        for i, sym in enumerate(symbols):
            per_symbol[sym] = SymbolDigest(
                symbol=sym,
                records=1000,
                trades=500,
                quotes=500,
                gap_flags=0,
                drops=0,
                reconnects=0,
                z_score=0.0,
                insufficient_baseline=(i == 0),  # only MES is flagged
            )

        digest = Digest(
            trade_date="2026-06-10",
            symbols=symbols,
            total_records=3000,
            total_trades=1500,
            total_quotes=1500,
            total_gap_flags=0,
            total_drops=0,
            total_reconnects=0,
            per_symbol=per_symbol,
            trade_count_zscores=zscores,
            insufficient_baseline=["MES"],  # only 1/3
            n_symbols=3,
        )

        result = _make_label_result(label="calm")
        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        assert vr.verdict == "accept"

    def test_does_not_fire_when_no_symbols(self):
        """Empty digest (n_symbols=0) -> rule cannot compute fraction -> no fire."""
        cfg = SlowTierConfig()
        digest = _make_digest(symbols=[], zscores={})
        result = _make_label_result(label="calm")

        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        # With no symbols the fraction check is skipped; the calm override also
        # does not fire (no z-scores, no gaps, no reconnects) -> accept.
        assert vr.verdict == "accept"

    def test_threshold_boundary_exactly_half_does_not_fire(self):
        """Fraction equal to threshold (0.5) does NOT fire — condition is strictly >."""
        cfg = SlowTierConfig()  # verify_insufficient_baseline_fraction=0.5

        # 1 of 2 symbols flagged: fraction=0.5, exactly at threshold -> no fire
        symbols = ["MES", "NQ"]
        per_symbol = {}
        for i, sym in enumerate(symbols):
            per_symbol[sym] = SymbolDigest(
                symbol=sym,
                records=1000,
                trades=500,
                quotes=500,
                gap_flags=0,
                drops=0,
                reconnects=0,
                z_score=0.0,
                insufficient_baseline=(i == 0),
            )

        digest = Digest(
            trade_date="2026-06-10",
            symbols=symbols,
            total_records=2000,
            total_trades=1000,
            total_quotes=1000,
            total_gap_flags=0,
            total_drops=0,
            total_reconnects=0,
            per_symbol=per_symbol,
            trade_count_zscores={s: 0.0 for s in symbols},
            insufficient_baseline=["MES"],  # 1/2 = 0.5, not > 0.5
            n_symbols=2,
        )

        result = _make_label_result(label="calm")
        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        assert vr.verdict == "accept"

    def test_custom_threshold_respected(self):
        """cfg.verify_insufficient_baseline_fraction is read from config."""
        # Lower the threshold so that 1/2 (0.5 > 0.4) now triggers the rule
        cfg = SlowTierConfig()
        object.__setattr__(cfg, "verify_insufficient_baseline_fraction", 0.4)

        symbols = ["MES", "NQ"]
        per_symbol = {}
        for i, sym in enumerate(symbols):
            per_symbol[sym] = SymbolDigest(
                symbol=sym,
                records=1000,
                trades=500,
                quotes=500,
                gap_flags=0,
                drops=0,
                reconnects=0,
                z_score=0.0,
                insufficient_baseline=(i == 0),
            )

        digest = Digest(
            trade_date="2026-06-10",
            symbols=symbols,
            total_records=2000,
            total_trades=1000,
            total_quotes=1000,
            total_gap_flags=0,
            total_drops=0,
            total_reconnects=0,
            per_symbol=per_symbol,
            trade_count_zscores={s: 0.0 for s in symbols},
            insufficient_baseline=["MES"],  # 1/2 = 0.5 > 0.4 -> fires
            n_symbols=2,
        )

        result = _make_label_result(label="calm")
        vr = verify_label_result(result, digest, self._no_sources(), cfg)

        assert vr.verdict == "conflict_review"
        assert any("insufficient_evidence" in r for r in vr.reasons)
