#!/usr/bin/env python3
"""Package raw robustness inputs for the VectorBT -> HftBacktest handoff."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.vectorbt_adapter import (
    ScreeningArtifactError,
    is_surface_stability_defined,
    screening_status_text,
    validate_screening_artifact,
)

RAW_SCHEMA = "hft3_robustness_raw_inputs_v1"
EVIDENCE_SCHEMA = "hft3_robustness_evidence_inputs_v1"
BINDING_FIELDS = (
    "screening_artifact_hash",
    "candidate_id",
    "parameter_values_hash",
    "feature_recipe_hash",
    "data_manifest_hash",
    "lake_manifest_hash",
)


def _compact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _error(reason: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"status": "error", "reason": reason}
    payload.update(extra)
    print(_compact_json(payload), file=sys.stderr)
    return 2


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


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_candidate_ids(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def _candidate_input_map(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        raise ValueError("robustness_inputs_required")
    schema = payload.get("schema")
    if schema != RAW_SCHEMA:
        raise ValueError(f"unsupported_robustness_raw_schema:{schema}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, Mapping):
        raise ValueError("robustness_inputs_candidates_must_be_object")
    return {str(candidate_id): value for candidate_id, value in candidates.items()}


def _default_source_evidence(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    source = payload.get("source_evidence")
    return dict(source) if isinstance(source, Mapping) else {}


def _default_scope(payload: Mapping[str, Any] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("robustness_gate_scope")
    return str(value) if value not in (None, "") else None


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_source_path(source_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return source_root / path


def _normalise_source_evidence(
    raw_source: Any,
    *,
    source_root: Path,
) -> dict[str, Any]:
    if not isinstance(raw_source, Mapping) or not raw_source:
        raise ValueError("source_evidence_missing")
    normalised: dict[str, Any] = {}
    for name, payload in raw_source.items():
        label = str(name)
        if isinstance(payload, str):
            if "#sha256:" in payload:
                digest = payload.rsplit("#sha256:", 1)[-1]
                if not _is_sha256(digest) or not _is_exact_hash_bound_source(payload):
                    raise ValueError(f"source_evidence_hash_invalid:{label}")
                normalised[label] = payload
                continue
            path = _resolve_source_path(source_root, payload)
            if not path.is_file():
                raise ValueError(f"source_evidence_hash_missing:{label}")
            normalised[label] = f"{payload}#sha256:{_sha256_file(path)}"
            continue
        if not isinstance(payload, Mapping):
            raise ValueError(f"source_evidence_malformed:{label}")
        entry = copy.deepcopy(dict(payload))
        source_path = str(entry.get("path") or entry.get("uri") or entry.get("source") or "")
        if not source_path:
            raise ValueError(f"source_evidence_path_missing:{label}")
        digest = entry.get("sha256") or entry.get("hash")
        if digest in (None, ""):
            if "path" not in entry:
                raise ValueError(f"source_evidence_hash_invalid:{label}")
            resolved = _resolve_source_path(source_root, str(entry["path"]))
            if not resolved.is_file():
                raise ValueError(f"source_evidence_hash_missing:{label}")
            digest = _sha256_file(resolved)
        elif not _is_sha256(digest):
            raise ValueError(f"source_evidence_hash_invalid:{label}")
        normalised[label] = f"{source_path}#sha256:{str(digest).removeprefix('sha256:')}"
    return normalised


def _entry_mapping(raw: Any, candidate_id: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"robustness_input_entry_must_be_object:{candidate_id}")
    return dict(raw)


def _surface_is_replay_ready(surface: Any) -> bool:
    return (
        isinstance(surface, Mapping)
        and screening_status_text(surface) == "pass"
        and is_surface_stability_defined(surface)
    )


def _binding_for(
    *,
    artifact: Mapping[str, Any],
    row: Mapping[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    binding = {
        "screening_artifact_hash": artifact.get("screening_artifact_hash"),
        "candidate_id": candidate_id,
        "parameter_values_hash": row.get("parameter_values_hash"),
        "feature_recipe_hash": row.get("feature_recipe_hash"),
        "data_manifest_hash": artifact.get("data_manifest_hash"),
        "lake_manifest_hash": artifact.get("lake_manifest_hash"),
    }
    missing = [field for field in BINDING_FIELDS if binding.get(field) in (None, "")]
    if missing:
        raise ValueError(f"binding_source_missing:{candidate_id}:{','.join(missing)}")
    return binding


def package_robustness_evidence_inputs(
    *,
    screening_artifact_path: Path,
    robustness_inputs_path: Path | None,
    out_path: Path,
    candidate_ids: set[str] | None,
    min_packaged: int,
    source_root: Path,
    robustness_gate_scope: str,
) -> dict[str, Any]:
    if min_packaged < 0:
        raise ValueError("min_packaged_must_be_non_negative")
    if screening_artifact_path.resolve() == out_path.resolve():
        raise ValueError("out_must_not_overwrite_screening_artifact")
    if robustness_inputs_path is not None and robustness_inputs_path.resolve() == out_path.resolve():
        raise ValueError("out_must_not_overwrite_robustness_inputs")

    artifact = _load_json_object(screening_artifact_path, "screening_artifact")
    validate_screening_artifact(artifact)

    if robustness_inputs_path is None:
        raise ValueError("robustness_inputs_required")
    raw_payload = _load_json_object(robustness_inputs_path, "robustness_inputs")
    raw_artifact_hash = raw_payload.get("screening_artifact_hash")
    artifact_hash = artifact.get("screening_artifact_hash")
    if raw_artifact_hash in (None, ""):
        raise ValueError("robustness_inputs_screening_artifact_hash_missing")
    if str(raw_artifact_hash) != str(artifact_hash):
        raise ValueError("robustness_inputs_screening_artifact_hash_mismatch")
    raw_by_candidate = _candidate_input_map(raw_payload)
    global_source = _default_source_evidence(raw_payload)
    global_scope = _default_scope(raw_payload)

    promoted = artifact.get("promoted")
    if not isinstance(promoted, list):
        raise ValueError("screening_artifact_promoted_must_be_list")

    packaged: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    matched_ids: list[str] = []
    for row in promoted:
        if not isinstance(row, Mapping):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        if candidate_ids is not None and candidate_id not in candidate_ids:
            continue
        matched_ids.append(candidate_id)
        raw_entry = _entry_mapping(raw_by_candidate.get(candidate_id), candidate_id)
        robustness_input = raw_entry.get("robustness_input")
        if not isinstance(robustness_input, Mapping) or not robustness_input:
            skipped[candidate_id] = "robustness_input_missing"
            continue
        surface = raw_entry.get("surface_stability_metrics")
        if not _surface_is_replay_ready(surface):
            skipped[candidate_id] = "surface_stability_metrics_not_replay_ready"
            continue
        source_evidence = dict(global_source)
        entry_source = raw_entry.get("source_evidence")
        if isinstance(entry_source, Mapping):
            source_evidence.update(dict(entry_source))
        try:
            packaged_source = _normalise_source_evidence(
                source_evidence,
                source_root=source_root,
            )
        except ValueError as exc:
            skipped[candidate_id] = str(exc)
            continue
        scope = (
            str(raw_entry.get("robustness_gate_scope"))
            if raw_entry.get("robustness_gate_scope") not in (None, "")
            else (global_scope or robustness_gate_scope)
        )
        packaged[candidate_id] = {
            "binding": _binding_for(
                artifact=artifact,
                row=row,
                candidate_id=candidate_id,
            ),
            "source_evidence": packaged_source,
            "robustness_input": copy.deepcopy(dict(robustness_input)),
            "surface_stability_metrics": copy.deepcopy(dict(surface)),
            "robustness_gate_scope": scope,
        }

    if not matched_ids:
        raise ValueError("no_promoted_candidates_matched")
    if len(packaged) < min_packaged:
        raise ValueError(
            "packaged_count_below_min:"
            f"packaged_count={len(packaged)}:min_packaged={min_packaged}:"
            f"skipped={skipped}"
        )

    payload = {
        "schema": EVIDENCE_SCHEMA,
        "candidates": packaged,
    }
    _write_json(out_path, payload)
    persisted = _load_json_object(out_path, "written_robustness_evidence")
    return {
        "status": "ok",
        "screening_artifact": str(screening_artifact_path),
        "robustness_inputs": str(robustness_inputs_path) if robustness_inputs_path else None,
        "out": str(out_path),
        "matched_count": len(matched_ids),
        "packaged_count": len(packaged),
        "matched_candidate_ids": matched_ids,
        "packaged_candidate_ids": sorted(persisted.get("candidates", {}).keys()),
        "skipped": skipped,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package raw robustness inputs into the HBT handoff evidence schema.",
    )
    parser.add_argument("--screening-artifact", required=True, type=Path)
    parser.add_argument("--robustness-inputs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidate-ids", default=None)
    parser.add_argument("--min-packaged", type=int, default=1)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument(
        "--robustness-gate-scope",
        default="packaged_robustness_evidence",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = package_robustness_evidence_inputs(
            screening_artifact_path=args.screening_artifact,
            robustness_inputs_path=args.robustness_inputs,
            out_path=args.out,
            candidate_ids=_parse_candidate_ids(args.candidate_ids),
            min_packaged=args.min_packaged,
            source_root=args.source_root,
            robustness_gate_scope=args.robustness_gate_scope,
        )
    except (ScreeningArtifactError, ValueError) as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed.
        return _error("package_robustness_evidence_failed", detail=str(exc))

    print(_compact_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
