#!/usr/bin/env python3
"""Apply explicit robustness evidence to a VectorBT screening artifact."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.robustness_bridge import compute_robustness_evidence
from backtest_pipeline.src.hftbacktest_realism import validate_candidate_replay_eligibility
from backtest_pipeline.src.vectorbt_adapter import (
    ScreeningArtifactError,
    compute_screening_artifact_hash,
    is_surface_stability_defined,
    persist_screening_artifact,
    screening_status_text,
    validate_screening_artifact,
)

EVIDENCE_SCHEMA = "hft3_robustness_evidence_inputs_v1"
APPLICATION_RECEIPT_SCHEMA = "hft3_robustness_evidence_application_receipt_v1"
REPLAY_STATUS_FIELDS = (
    "wfc_status",
    "dsr_status",
    "pbo_status",
    "cscv_status",
)
ROBUSTNESS_EVIDENCE_FIELDS = (
    *REPLAY_STATUS_FIELDS,
    "robustness_artifact_staleness",
    "bootstrap_ci_or_not_run",
    "dsr_or_not_run",
    "pbo_or_not_run",
    "cscv_count_or_not_run",
    "fee_stress_or_not_run",
    "slippage_stress_or_not_run",
    "latency_stress_or_not_run",
    "holm_bh_or_not_run",
    "null_battery_or_not_run",
    "planted_alpha_or_not_run",
    "adversarial_or_not_run",
    "parameter_perturbation_or_not_run",
    "walk_forward_metrics",
    "wfc_metrics",
)
BINDING_FIELDS = (
    "screening_artifact_hash",
    "candidate_id",
    "parameter_values_hash",
    "feature_recipe_hash",
    "data_manifest_hash",
    "lake_manifest_hash",
)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label}_missing:{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}_invalid_json:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label}_must_be_object:{path}")
    return payload


def _candidate_evidence_map(payload: Mapping[str, Any]) -> dict[str, Any]:
    schema = payload.get("schema")
    if schema != EVIDENCE_SCHEMA:
        raise ValueError(f"unsupported_robustness_evidence_schema:{schema}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, Mapping):
        raise ValueError("robustness_evidence_candidates_must_be_object")
    return {str(candidate_id): value for candidate_id, value in candidates.items()}


def _parse_candidate_ids(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def _split_evidence_entry(entry: Any) -> tuple[dict[str, Any], Mapping[str, Any] | None, Any]:
    if isinstance(entry, Mapping) and "robustness_input" in entry:
        robustness_input = entry.get("robustness_input")
        surface = entry.get("surface_stability_metrics")
        scope = entry.get("robustness_gate_scope")
        return (
            dict(robustness_input) if isinstance(robustness_input, Mapping) else {},
            surface if isinstance(surface, Mapping) else None,
            scope,
        )
    return dict(entry) if isinstance(entry, Mapping) else {}, None, None


def _normalise_robustness_input(raw: Mapping[str, Any]) -> dict[str, Any]:
    robustness_input = copy.deepcopy(dict(raw))
    cscv_matrix = robustness_input.get("cscv_matrix")
    if isinstance(cscv_matrix, list):
        robustness_input["cscv_matrix"] = np.asarray(cscv_matrix)
    return robustness_input


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    if text.startswith("sha256:"):
        text = text.removeprefix("sha256:")
    return len(text) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in text)


def _is_exact_hash_bound_source(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    path, sep, digest = value.rpartition("#sha256:")
    return bool(path) and sep == "#sha256:" and "#" not in path and _is_sha256(digest)


def _source_evidence_errors(entry: Any) -> list[str]:
    if not isinstance(entry, Mapping):
        return ["evidence_entry_must_be_object"]
    source_evidence = entry.get("source_evidence")
    if not isinstance(source_evidence, Mapping) or not source_evidence:
        return ["source_evidence_missing"]
    errors: list[str] = []
    for name, payload in source_evidence.items():
        label = str(name)
        if not isinstance(payload, str):
            errors.append(f"source_evidence_malformed:{label}")
            continue
        if "#sha256:" not in payload:
            errors.append(f"source_evidence_hash_missing:{label}")
        elif not _is_exact_hash_bound_source(payload):
            errors.append(f"source_evidence_hash_invalid:{label}")
    return errors


def _binding_errors(
    *,
    artifact: Mapping[str, Any],
    row: Mapping[str, Any],
    entry: Any,
    candidate_id: str,
) -> list[str]:
    if not isinstance(entry, Mapping):
        return ["evidence_entry_must_be_object"]
    binding = entry.get("binding") or entry.get("artifact_binding")
    if not isinstance(binding, Mapping):
        return ["evidence_binding_missing"]
    expected = {
        "screening_artifact_hash": artifact.get("screening_artifact_hash"),
        "candidate_id": candidate_id,
        "parameter_values_hash": row.get("parameter_values_hash"),
        "feature_recipe_hash": row.get("feature_recipe_hash"),
        "data_manifest_hash": artifact.get("data_manifest_hash"),
        "lake_manifest_hash": artifact.get("lake_manifest_hash"),
    }
    errors: list[str] = []
    for field_name in BINDING_FIELDS:
        observed = binding.get(field_name)
        wanted = expected.get(field_name)
        if observed in (None, ""):
            errors.append(f"evidence_binding_missing:{field_name}")
        elif wanted in (None, ""):
            errors.append(f"screening_row_binding_source_missing:{field_name}")
        elif str(observed) != str(wanted):
            errors.append(f"evidence_binding_mismatch:{field_name}")
    return errors


def _eligibility_rejection_reasons(row: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    screening_status = screening_status_text(row.get("screening_status"))
    if screening_status != "pass":
        reasons.append(f"screening_status={screening_status or 'missing'}")

    for field_name in REPLAY_STATUS_FIELDS:
        status = screening_status_text(row.get(field_name))
        if status != "pass":
            reasons.append(f"{field_name}={status or 'missing'}")

    staleness = screening_status_text(row.get("robustness_artifact_staleness"))
    if staleness != "fresh":
        reasons.append(f"robustness_artifact_staleness={staleness or 'missing'}")

    surface = row.get("surface_stability_metrics")
    if not isinstance(surface, Mapping):
        reasons.append("surface_stability_metrics_missing")
    else:
        surface_status = screening_status_text(surface)
        if surface_status != "pass":
            reasons.append(f"surface_stability_status={surface_status or 'missing'}")
        if not is_surface_stability_defined(surface):
            reasons.append("surface_stability_evidence_incomplete")
    return reasons


def _apply_row_evidence(
    *,
    artifact: Mapping[str, Any],
    row: dict[str, Any],
    entry: Any,
) -> tuple[bool, list[str]]:
    candidate_id = str(row.get("candidate_id") or "")
    binding_reasons = _binding_errors(
        artifact=artifact,
        row=row,
        entry=entry,
        candidate_id=candidate_id,
    )
    binding_reasons.extend(_source_evidence_errors(entry))
    if binding_reasons:
        row["replay_eligibility_status"] = "not_eligible"
        row["rejection_reason_or_null"] = (
            "robustness_evidence_binding_invalid:"
            + ",".join(binding_reasons)
        )
        return False, binding_reasons

    assert isinstance(entry, Mapping)
    binding = entry.get("binding") or entry.get("artifact_binding")
    source_evidence = entry.get("source_evidence")
    row["robustness_evidence_receipt"] = {
        "schema": EVIDENCE_SCHEMA,
        "binding": copy.deepcopy(dict(binding)),
        "source_evidence": copy.deepcopy(dict(source_evidence)),
        "evidence_entry_hash": _evidence_entry_hash(entry),
    }

    raw_input, surface, scope = _split_evidence_entry(entry)
    evidence = compute_robustness_evidence(
        _normalise_robustness_input(raw_input),
        candidate_id=candidate_id,
    )
    for field_name in ROBUSTNESS_EVIDENCE_FIELDS:
        if field_name in evidence:
            row[field_name] = evidence[field_name]
    if surface is not None:
        row["surface_stability_metrics"] = copy.deepcopy(dict(surface))
    if scope not in (None, ""):
        row["robustness_gate_scope"] = str(scope)

    rejection_reasons = _eligibility_rejection_reasons(row)
    if rejection_reasons:
        row["replay_eligibility_status"] = "not_eligible"
        row["rejection_reason_or_null"] = (
            "robustness_evidence_not_replay_eligible:"
            + ",".join(rejection_reasons)
        )
        return False, rejection_reasons

    row["replay_eligibility_status"] = "eligible"
    row["rejection_reason_or_null"] = None
    hbt_reasons = validate_candidate_replay_eligibility(row)
    if hbt_reasons:
        row["replay_eligibility_status"] = "not_eligible"
        row["rejection_reason_or_null"] = (
            "hftbacktest_replay_eligibility_invalid:"
            + ",".join(hbt_reasons)
        )
        return False, hbt_reasons
    return True, []


def _compact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _evidence_entry_hash(entry: Mapping[str, Any]) -> str:
    payload = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _error(reason: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"status": "error", "reason": reason}
    payload.update(extra)
    print(_compact_json(payload), file=sys.stderr)
    return 2


def apply_robustness_evidence(
    *,
    screening_artifact_path: Path,
    robustness_evidence_path: Path,
    out_path: Path,
    candidate_ids: set[str] | None,
    min_eligible: int,
) -> dict[str, Any]:
    if min_eligible < 0:
        raise ValueError("min_eligible_must_be_non_negative")
    if screening_artifact_path.resolve() == out_path.resolve():
        raise ValueError("out_must_not_overwrite_screening_artifact")

    artifact = _load_json_object(screening_artifact_path, "screening_artifact")
    validate_screening_artifact(artifact)

    evidence_payload = _load_json_object(robustness_evidence_path, "robustness_evidence")
    evidence_by_candidate = _candidate_evidence_map(evidence_payload)

    updated = copy.deepcopy(artifact)
    promoted = updated.get("promoted")
    if not isinstance(promoted, list):
        raise ValueError("screening_artifact_promoted_must_be_list")

    matched_ids: list[str] = []
    eligible_ids: list[str] = []
    ineligible_reasons: dict[str, list[str]] = {}
    row_receipt_hashes: dict[str, str] = {}

    for row in promoted:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        if candidate_ids is not None and candidate_id not in candidate_ids:
            continue
        if candidate_id not in evidence_by_candidate:
            continue
        matched_ids.append(candidate_id)
        eligible, reasons = _apply_row_evidence(
            artifact=artifact,
            row=row,
            entry=evidence_by_candidate[candidate_id],
        )
        if eligible:
            eligible_ids.append(candidate_id)
            receipt = row.get("robustness_evidence_receipt")
            if isinstance(receipt, Mapping):
                row_receipt_hashes[candidate_id] = _evidence_entry_hash(receipt)
        else:
            ineligible_reasons[candidate_id] = reasons

    if not matched_ids:
        raise ValueError("no_evidence_rows_matched_promoted_candidates")
    if len(eligible_ids) < min_eligible:
        raise ValueError(
            "eligible_count_below_min:"
            f"eligible_count={len(eligible_ids)}:min_eligible={min_eligible}"
        )

    updated["robustness_evidence_receipt"] = {
        "schema": APPLICATION_RECEIPT_SCHEMA,
        "input_screening_artifact_hash": str(artifact.get("screening_artifact_hash") or ""),
        "robustness_evidence_schema": EVIDENCE_SCHEMA,
        "matched_candidate_ids": matched_ids,
        "eligible_candidate_ids": eligible_ids,
        "row_receipt_hashes": row_receipt_hashes,
    }
    updated["screening_artifact_hash"] = compute_screening_artifact_hash(updated)
    validate_screening_artifact(updated)
    persist_screening_artifact(updated, out_path)

    persisted = _load_json_object(out_path, "written_screening_artifact")
    return {
        "status": "ok",
        "screening_artifact": str(screening_artifact_path),
        "robustness_evidence": str(robustness_evidence_path),
        "out": str(out_path),
        "matched_count": len(matched_ids),
        "eligible_count": len(eligible_ids),
        "matched_candidate_ids": matched_ids,
        "eligible_candidate_ids": eligible_ids,
        "screening_artifact_hash": str(persisted.get("screening_artifact_hash") or ""),
        "ineligible_reasons": ineligible_reasons,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply explicit robustness evidence to a VectorBT screening artifact.",
    )
    parser.add_argument("--screening-artifact", required=True, type=Path)
    parser.add_argument("--robustness-evidence", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidate-ids", default=None)
    parser.add_argument("--min-eligible", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = apply_robustness_evidence(
            screening_artifact_path=args.screening_artifact,
            robustness_evidence_path=args.robustness_evidence,
            out_path=args.out,
            candidate_ids=_parse_candidate_ids(args.candidate_ids),
            min_eligible=args.min_eligible,
        )
    except (ScreeningArtifactError, ValueError) as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed.
        return _error("apply_robustness_evidence_failed", detail=str(exc))

    print(_compact_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
