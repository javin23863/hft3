"""F3 — Hypothesis Intake (flag-gated, strict weekly quota).

Template-fills a TestableHypothesis candidate from a fixed taxonomy family
using the local Gemma model.  Grounded in vault doctrine; no free generation.

Gating:
  - config key ``intake.enabled`` (default False) — if False, exits 3.
  - Weekly quota: maximum 5 candidates per 7-day trailing window.  Any attempt
    beyond the quota is rejected with exit 4.

Flow:
  1. Load taxonomy family spec from config/hypothesis_taxonomy.yaml.
  2. Build a prompt embedding the family spec verbatim + instruction to propose
     ONE candidate as JSON.
  3. Parse and validate the model's output against TestableHypothesis
     (packages/research_pipeline/intake_schema.py).
  4. Run detect_intake_quarantine() to flag structural problems.
  5. Write accepted candidate to research_inputs/slow_tier/{hypothesis_id}.json.
  6. Append a record to artifacts/research_cards/slow_tier/intake_log.jsonl.

IMPORTANT: Promotion into features_engine/src/hypotheses/registry.py is a
HUMAN-ONLY action through the normal gauntlet.  This flow NEVER touches the
features_engine package and NEVER auto-registers hypotheses.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import SlowTierConfig
from .llm_call import ollama_call

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Taxonomy loader
# ---------------------------------------------------------------------------

def _taxonomy_path() -> Path:
    """Return the path to hypothesis_taxonomy.yaml bundled with this package."""
    return Path(__file__).resolve().parent.parent / "config" / "hypothesis_taxonomy.yaml"


def load_taxonomy() -> Dict[str, Any]:
    """Load the hypothesis taxonomy YAML.  Returns the 'families' dict.

    Raises RuntimeError if the file is missing or unparseable.
    """
    path = _taxonomy_path()
    if not path.is_file():
        raise RuntimeError(f"hypothesis_taxonomy.yaml not found at {path}")
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise RuntimeError(f"Failed to parse hypothesis_taxonomy.yaml: {exc}") from exc
    families = raw.get("families")
    if not isinstance(families, dict):
        raise RuntimeError("hypothesis_taxonomy.yaml missing 'families' key")
    return families


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------

_SECONDS_PER_WEEK = 7 * 24 * 3600


def count_recent_candidates(log_path: Path, weekly_quota: int = 5) -> int:
    """Count candidates written in the trailing 7 days from the intake log.

    Returns the count of log records whose ``generated_utc`` is within the
    past 7 days.  Records with unparseable timestamps are skipped.
    """
    if not log_path.is_file():
        return 0

    now_utc = datetime.now(timezone.utc)
    count = 0
    try:
        with log_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                generated = rec.get("generated_utc", "")
                if not generated:
                    continue
                try:
                    ts = datetime.fromisoformat(generated)
                    # Normalise to UTC-aware
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    delta = (now_utc - ts).total_seconds()
                    if 0 <= delta <= _SECONDS_PER_WEEK:
                        count += 1
                except (ValueError, TypeError):
                    continue
    except OSError:
        return 0
    return count


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_INTAKE = """\
You are an expert quantitative researcher filling a hypothesis template.
You will be given a strategy family specification and you must propose EXACTLY
ONE testable hypothesis candidate as a JSON object.

RULES:
- Use ONLY the feature inputs listed in allowed_feature_inputs.
- Do NOT reference features outside the allowed list.
- Do NOT produce anything that violates the forbidden list.
- Respond with a single JSON object and nothing else.
- The JSON object must have these exact keys:
    statement      : string — the testable claim (one sentence)
    metric         : string — the primary evaluation metric
    threshold      : number — numeric threshold that separates pass from fail
    pass_criteria  : string — precise condition for the hypothesis to be supported
    fail_criteria  : string — precise condition for the hypothesis to be rejected
    products       : list of strings — CME futures symbols to test on
    rationale      : string — why this hypothesis is worth testing (max 3 sentences)
"""

_USER_INTAKE = """\
FAMILY: {family_name}

FAMILY SPECIFICATION:
{family_spec_json}

Propose ONE hypothesis candidate for this family. Respond with JSON only.
"""


def build_intake_prompt(family_name: str, family_spec: Dict[str, Any]) -> Tuple[str, str]:
    """Return (system_text, user_text) for the intake model call."""
    family_spec_json = json.dumps(family_spec, indent=2, ensure_ascii=False)
    user = _USER_INTAKE.format(
        family_name=family_name,
        family_spec_json=family_spec_json,
    )
    return _SYSTEM_INTAKE, user


# ---------------------------------------------------------------------------
# Parse and validate model output
# ---------------------------------------------------------------------------

_CANDIDATE_REQUIRED_KEYS = {
    "statement", "metric", "threshold", "pass_criteria",
    "fail_criteria", "products", "rationale",
}


def parse_candidate_json(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse the model's JSON output into a candidate dict.

    Returns (candidate_dict, error_message).  On error, candidate_dict is None.
    """
    raw = text.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"json_decode_error: {exc}"

    if not isinstance(obj, dict):
        return None, "response_not_a_json_object"

    missing = _CANDIDATE_REQUIRED_KEYS - set(obj.keys())
    if missing:
        return None, f"missing_keys: {sorted(missing)}"

    return obj, None


def _next_hypothesis_seq(log_path: Path) -> int:
    """Return the next sequence number for today by counting log records."""
    if not log_path.is_file():
        return 1
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    try:
        with log_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("date", "") == today:
                        count += 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return count + 1


# ---------------------------------------------------------------------------
# TestableHypothesis construction and quarantine
# ---------------------------------------------------------------------------

def build_and_validate_hypothesis(
    candidate: Dict[str, Any],
    hypothesis_id: str,
) -> Tuple[Any, List[str]]:
    """Map candidate dict to a TestableHypothesis and run detect_intake_quarantine.

    Returns (hypothesis, quarantine_flags).  ``hypothesis`` is a
    TestableHypothesis pydantic model.  ``quarantine_flags`` is a list of
    reason strings (empty = no quarantine).

    Note: detect_intake_quarantine() from intake_schema.py expects a full
    bundle (thesis, signal, parameters, hypotheses, notes).  We construct
    minimal stubs for the surrounding fields so the function can execute its
    checks; the core fields come from the model's candidate JSON.

    IMPORTANT: This function imports from research_pipeline only.
    It NEVER imports from features_engine — promotion is human-only.
    """
    from research_pipeline.intake_schema import (
        TestableHypothesis,
        ThesisSummary,
        SignalLogic,
        ParameterRange,
        ExperimentTranslationNotes,
        detect_intake_quarantine,
    )

    # Map model candidate to TestableHypothesis fields.
    threshold_val = candidate.get("threshold")
    try:
        threshold_float = float(threshold_val) if threshold_val is not None else None
    except (TypeError, ValueError):
        threshold_float = None

    hypothesis = TestableHypothesis(
        hypothesis_id=hypothesis_id,
        statement=str(candidate.get("statement", "")),
        metric=str(candidate.get("metric", "")),
        threshold=threshold_float,
        pass_criteria=str(candidate.get("pass_criteria", "")),
        fail_criteria=str(candidate.get("fail_criteria", "")),
    )

    # Build minimal stubs for the quarantine checker.
    products: List[str] = candidate.get("products") or []
    rationale: str = str(candidate.get("rationale", ""))

    thesis = ThesisSummary(
        main_thesis=f"{hypothesis.statement}  {rationale}",
        instrument_scope=products if products else ["unknown"],
    )

    signal = SignalLogic(
        signal=hypothesis.metric or hypothesis.statement,
        entry=[hypothesis.pass_criteria],
        exit=[hypothesis.fail_criteria],
    )

    # Build a minimal ParameterRange from the threshold.
    if threshold_float is not None:
        lo = threshold_float * 0.5 if threshold_float > 0 else threshold_float * 1.5
        hi = threshold_float * 1.5 if threshold_float > 0 else threshold_float * 0.5
        if lo >= hi:
            lo, hi = hi - 1.0, hi
        params = [ParameterRange(
            param="threshold",
            min=lo,
            max=hi,
            default=threshold_float,
        )]
    else:
        params = []

    notes = ExperimentTranslationNotes(
        hft3_implementation_reqs=[
            f"Implement {hypothesis.metric} feature in the feature store",
            f"Backtest on {', '.join(products) if products else 'target symbols'}",
        ],
        missing_info=[],
    )

    quarantine_flags = detect_intake_quarantine(
        thesis=thesis,
        signal=signal,
        parameters=params,
        hypotheses=[hypothesis],
        notes=notes,
    )

    return hypothesis, quarantine_flags


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

def write_candidate(
    hypothesis: Any,
    candidate: Dict[str, Any],
    family_name: str,
    quarantine_flags: List[str],
    *,
    artifact_root: str,
    model: str,
    trade_date: str,
) -> Tuple[Path, Path]:
    """Write accepted candidate to disk and append a log record.

    Output files:
      research_inputs/slow_tier/{hypothesis_id}.json
      artifacts/research_cards/slow_tier/intake_log.jsonl

    Returns (candidate_path, log_path).

    IMPORTANT: This function NEVER writes to features_engine — promotion is
    a human-only action.  The candidate lands in the quarantine zone only.
    """
    # Candidate output dir: research_inputs/slow_tier/ relative to repo root
    repo_root = Path(__file__).resolve().parents[3]
    candidate_dir = repo_root / "research_inputs" / "slow_tier"
    candidate_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = candidate_dir / f"{hypothesis.hypothesis_id}.json"
    candidate_payload = {
        "hypothesis_id": hypothesis.hypothesis_id,
        "family": family_name,
        "statement": hypothesis.statement,
        "metric": hypothesis.metric,
        "threshold": hypothesis.threshold,
        "pass_criteria": hypothesis.pass_criteria,
        "fail_criteria": hypothesis.fail_criteria,
        "products": candidate.get("products") or [],
        "rationale": candidate.get("rationale", ""),
        "quarantine_flags": quarantine_flags,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "status": "quarantine",  # promotion to registry is human-only
    }
    candidate_path.write_text(
        json.dumps(candidate_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Log record
    log_dir = Path(artifact_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "intake_log.jsonl"
    log_record = {
        "hypothesis_id": hypothesis.hypothesis_id,
        "family": family_name,
        "date": trade_date,
        "quarantine_flags": quarantine_flags,
        "model": model,
        "generated_utc": candidate_payload["generated_utc"],
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    return candidate_path, log_path


# ---------------------------------------------------------------------------
# Main intake runner (called from CLI)
# ---------------------------------------------------------------------------

def run_intake(
    family_name: str,
    trade_date: str,
    *,
    model: str,
    cfg: SlowTierConfig,
) -> Tuple[int, str]:
    """Execute the full intake flow for a given family.

    Returns (exit_code, message).
      0 = success
      3 = intake disabled in config
      4 = weekly quota reached
      1 = other error

    NOTE: The enabled/quota gate is checked here, but the caller (CLI) is
    responsible for emitting the appropriate exit code.  The gate on
    ``cfg.intake.enabled`` is enforced here so that tests can call this
    function directly.
    """
    # Gate: intake must be explicitly enabled
    intake_cfg = getattr(cfg, "intake", None) or {}
    if isinstance(intake_cfg, dict):
        enabled = intake_cfg.get("enabled", False)
        weekly_quota = int(intake_cfg.get("weekly_quota", 5))
    else:
        enabled = getattr(intake_cfg, "enabled", False)
        weekly_quota = getattr(intake_cfg, "weekly_quota", 5)

    if not enabled:
        return 3, "F3 disabled in config"

    # Quota check
    log_path = Path(cfg.artifact_root) / "intake_log.jsonl"
    recent = count_recent_candidates(log_path)
    if recent >= weekly_quota:
        return 4, f"weekly quota reached: {recent}/{weekly_quota}"

    # Load taxonomy
    try:
        families = load_taxonomy()
    except RuntimeError as exc:
        return 1, f"taxonomy load failed: {exc}"

    if family_name not in families:
        available = sorted(families.keys())
        return 1, f"unknown family {family_name!r}; available: {available}"

    family_spec = families[family_name]

    # Build prompt and call model
    system_text, user_text = build_intake_prompt(family_name, family_spec)
    log.info("calling ollama for hypothesis intake, family=%s, model=%s", family_name, model)
    text, error = ollama_call(
        system_text,
        user_text,
        model=model,
        cfg=cfg,
        format_json=True,
    )
    if error or not text:
        return 1, f"ollama error: {error}"

    # Parse candidate
    candidate, parse_error = parse_candidate_json(text)
    if parse_error or candidate is None:
        return 1, f"candidate parse error: {parse_error}"

    # Build hypothesis_id
    seq = _next_hypothesis_seq(log_path)
    hypothesis_id = f"SLOWTIER-{trade_date}-{seq:03d}"

    # Validate and run quarantine
    try:
        hypothesis, quarantine_flags = build_and_validate_hypothesis(candidate, hypothesis_id)
    except Exception as exc:
        return 1, f"hypothesis validation failed: {exc}"

    # Write candidate and log
    try:
        candidate_path, _ = write_candidate(
            hypothesis,
            candidate,
            family_name,
            quarantine_flags,
            artifact_root=cfg.artifact_root,
            model=model,
            trade_date=trade_date,
        )
    except Exception as exc:
        return 1, f"write failed: {exc}"

    quarantine_notice = ""
    if quarantine_flags:
        quarantine_notice = f"  QUARANTINE flags: {quarantine_flags}"

    msg = (
        f"Candidate written: {candidate_path}\n"
        f"  hypothesis_id: {hypothesis_id}\n"
        f"  family: {family_name}\n"
        f"  statement: {hypothesis.statement[:80]}"
        + quarantine_notice
        + "\n\n"
        "NOTE: promotion into features_engine/src/hypotheses/registry.py is a "
        "human-only action through the normal gauntlet.  This candidate is in "
        "the quarantine zone only."
    )
    return 0, msg
