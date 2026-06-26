"""One-input/one-output research pipeline runner.

This runner owns the handoff contract between existing hft3 stages. It does not
replace VectorBT, robustness, HftBacktest, workbench, or lifecycle engines; it
wraps them with stage receipts and fail-closed validation so a completed run
cannot masquerade as a promotable run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backtest_pipeline.src.research_pipeline_stages import (
    PIPELINE_STAGES,
    STAGE_0_ONTOLOGY,
    STAGE_1_VECTORBT_SCREEN,
    STAGE_2_PROMOTED_AGGREGATION,
    STAGE_3_HFTBACKTEST_REALISM,
    STAGE_4_WORKBENCH_ROBUSTNESS,
    STAGE_5_LIFECYCLE_BEHAVIOR,
    pipeline_stage_stamp,
)

VERSION = 1
DEFAULT_BUNDLE_ROOT = Path("research_cards") / "pipeline_runs"
STAGE_2_ROBUSTNESS_EVIDENCE = "stage_2_robustness_evidence"
RUN_STAGE_IDS: tuple[str, ...] = (
    STAGE_0_ONTOLOGY,
    STAGE_1_VECTORBT_SCREEN,
    STAGE_2_PROMOTED_AGGREGATION,
    STAGE_2_ROBUSTNESS_EVIDENCE,
    STAGE_3_HFTBACKTEST_REALISM,
    STAGE_4_WORKBENCH_ROBUSTNESS,
    STAGE_5_LIFECYCLE_BEHAVIOR,
)
RUN_STAGE_NAMES = {
    STAGE_2_ROBUSTNESS_EVIDENCE: "Robustness evidence bridge before HftBacktest eligibility",
}
RUN_STAGE_LIFECYCLE_STATES = {
    STAGE_2_ROBUSTNESS_EVIDENCE: "SCREENING -> replay_eligibility_status=eligible",
}
FORBIDDEN_FEATURE_PLANE_STATUSES = {
    "bar_stub_research_only",
    "incomplete_feature_plane",
}
FORBIDDEN_FEATURE_SET_IDS = {
    "fs_v1_pilot_unknown",
}
FORBIDDEN_BAR_CONSTRUCTION_IDS = {
    "ohlcv_1m_from_npz_or_supplied_array",
}
FAILED_STATUS_VALUES = {"failed", "blocked", "error", "aborted", "stalled"}
OFFICIAL_TRADE_COUNT_KEY = "Total Trades"


class PipelineBlocked(RuntimeError):
    """Raised when a pipeline stage cannot safely advance."""

    def __init__(self, stage_id: str, errors: Sequence[str]):
        super().__init__(f"{stage_id} blocked: {'; '.join(errors)}")
        self.stage_id = stage_id
        self.errors = list(errors)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_path(value: str | os.PathLike[str], *, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def resolve_output_paths(outputs: Mapping[str, Any], *, base: Path) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in outputs.items():
        if isinstance(value, str):
            resolved[key] = str(as_path(value, base=base))
        elif isinstance(value, list):
            resolved[key] = [
                str(as_path(item, base=base)) if isinstance(item, str) else item for item in value
            ]
        elif isinstance(value, dict):
            resolved[key] = {
                sub_key: str(as_path(sub_value, base=base)) if isinstance(sub_value, str) else sub_value
                for sub_key, sub_value in value.items()
            }
        else:
            resolved[key] = value
    return resolved


def flatten_output_files(outputs: Mapping[str, Any]) -> list[Path]:
    files: list[Path] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            files.append(Path(value))
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)

    visit(outputs)
    return files


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def collect_key_values(obj: Any, keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if key in keys:
                found.append(value)
            found.extend(collect_key_values(value, keys))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(collect_key_values(item, keys))
    return found


def promoted_count(payload: Mapping[str, Any]) -> int:
    counts = []
    promoted_ids = payload.get("promoted_ids")
    if isinstance(promoted_ids, list):
        counts.append(len(promoted_ids))
    promoted = payload.get("promoted")
    if isinstance(promoted, list):
        counts.append(len(promoted))
    count = numeric(payload.get("promoted_count"))
    if count is not None:
        counts.append(int(count))
    return max(counts) if counts else 0


def has_positive_trade_count(row: Mapping[str, Any]) -> bool:
    metric_values = row.get("metric_values")
    stats: Any = {}
    if isinstance(metric_values, Mapping):
        stats = metric_values.get("vbt_stats") or {}
    if not isinstance(stats, Mapping) or not stats:
        stats = row.get("vectorbt_results") or {}
    if not isinstance(stats, Mapping):
        return False
    value = numeric(stats.get(OFFICIAL_TRADE_COUNT_KEY))
    return value is not None and value > 0


def positive_trade_rows(obj: Any) -> int:
    if isinstance(obj, Mapping):
        row_sections: list[Any] = []
        for section in ("promoted", "rejected"):
            rows = obj.get(section)
            if isinstance(rows, list):
                row_sections.extend(rows)
        if row_sections:
            return sum(
                1
                for row in row_sections
                if isinstance(row, Mapping) and has_positive_trade_count(row)
            )
        if has_positive_trade_count(obj):
            return 1
        return sum(positive_trade_rows(value) for value in obj.values())
    if isinstance(obj, list):
        return sum(positive_trade_rows(item) for item in obj)
    return 0


def stage_ids_through(target_stage: str) -> list[str]:
    if target_stage not in RUN_STAGE_IDS:
        raise ValueError(f"unknown target_stage: {target_stage}")
    end = RUN_STAGE_IDS.index(target_stage) + 1
    return list(RUN_STAGE_IDS[:end])


def default_target_stage(spec: Mapping[str, Any]) -> str:
    if "target_stage" in spec:
        return str(spec["target_stage"])
    stages = spec.get("stages")
    if isinstance(stages, Mapping) and stages:
        ordered = [stage for stage in RUN_STAGE_IDS if stage in stages]
        if ordered:
            return ordered[-1]
    return STAGE_5_LIFECYCLE_BEHAVIOR


def stage_name(stage_id: str) -> str:
    if stage_id in PIPELINE_STAGES:
        return PIPELINE_STAGES[stage_id].name
    return RUN_STAGE_NAMES.get(stage_id, stage_id)


def stage_lifecycle_state(stage_id: str) -> str:
    if stage_id in PIPELINE_STAGES:
        return PIPELINE_STAGES[stage_id].lifecycle_state
    return RUN_STAGE_LIFECYCLE_STATES.get(stage_id, "")


def stage_stamp(stage_id: str) -> dict[str, Any]:
    if stage_id in PIPELINE_STAGES:
        return pipeline_stage_stamp(stage_id)
    return {
        "research_pipeline_stage_id": stage_id,
        "research_pipeline_stage_ordinal": RUN_STAGE_IDS.index(stage_id),
        "research_pipeline_stage_name": stage_name(stage_id),
        "research_pipeline_lifecycle_state": stage_lifecycle_state(stage_id),
        "research_pipeline_ontology_doc": "docs/vault/UNIFIED_RESEARCH_PIPELINE.md",
        "research_pipeline_vault_notes": [
            "wiki/hot.md",
            "docs/project/AUTORESEARCH_PIPELINE_UPGRADE_PLAN.md",
            "docs/project/ROBUSTNESS_PIPELINE_SOURCE_OF_TRUTH.md",
        ],
        "research_pipeline_literature_refs": [
            "docs/project/ROBUSTNESS_TESTING_SPEC.md",
            "docs/references/Ultimate_Quantitative_Finance_Researcher.pdf",
        ],
        "research_pipeline_trade_manager_hook": (
            "robustness evidence receipt gates replay eligibility before HftBacktest"
        ),
    }


def build_context(spec_path: Path, spec: Mapping[str, Any]) -> tuple[Path, Path, str, str]:
    repo_root = as_path(str(spec.get("repo_root") or Path.cwd()), base=spec_path.parent)
    run_id = str(spec.get("run_id") or "").strip()
    if not run_id:
        run_id = "research_pipeline_" + utc_now().replace(":", "").replace("-", "")
    bundle_root = as_path(str(spec.get("bundle_root") or DEFAULT_BUNDLE_ROOT), base=repo_root)
    bundle_dir = bundle_root / run_id
    target_stage = default_target_stage(spec)
    return repo_root, bundle_dir, run_id, target_stage


def existing_stage_receipt(bundle_dir: Path, stage_id: str) -> Mapping[str, Any] | None:
    receipt_path = bundle_dir / "receipts" / f"{stage_id}.json"
    if not receipt_path.exists():
        return None
    try:
        receipt = read_json(receipt_path)
    except json.JSONDecodeError:
        return None
    if isinstance(receipt, Mapping):
        return receipt
    return None


def existing_passed_receipt(bundle_dir: Path, stage_id: str) -> Mapping[str, Any] | None:
    receipt = existing_stage_receipt(bundle_dir, stage_id)
    if receipt is not None and receipt.get("status") == "passed":
        return receipt
    return None


def write_status(
    bundle_dir: Path,
    *,
    run_id: str,
    target_stage: str,
    status: str,
    stage_receipts: Sequence[Mapping[str, Any]],
    failures: Sequence[str] = (),
) -> dict[str, Any]:
    passed_ids = [str(r["stage_id"]) for r in stage_receipts if r.get("status") == "passed"]
    remaining = [s for s in stage_ids_through(target_stage) if s not in passed_ids]
    payload = {
        "version": VERSION,
        "run_id": run_id,
        "status": status,
        "target_stage": target_stage,
        "updated_at": utc_now(),
        "last_passed_stage": passed_ids[-1] if passed_ids else None,
        "next_stage": remaining[0] if remaining else None,
        "failures": list(failures),
    }
    write_json(bundle_dir / "status.json", payload)
    return payload


def write_bundle(
    bundle_dir: Path,
    *,
    run_id: str,
    target_stage: str,
    spec_path: Path,
    stage_receipts: Sequence[Mapping[str, Any]],
    status: str,
    failures: Sequence[str] = (),
) -> dict[str, Any]:
    status_payload = write_status(
        bundle_dir,
        run_id=run_id,
        target_stage=target_stage,
        status=status,
        stage_receipts=stage_receipts,
        failures=failures,
    )
    payload = {
        "version": VERSION,
        "run_id": run_id,
        "status": status,
        "target_stage": target_stage,
        "spec_path": str(spec_path),
        "updated_at": status_payload["updated_at"],
        "research_pipeline_ontology_doc": "docs/vault/UNIFIED_RESEARCH_PIPELINE.md",
        "stage_receipts": list(stage_receipts),
        "failures": list(failures),
    }
    write_json(bundle_dir / "run_bundle.json", payload)
    return payload


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout_seconds: int | None,
    log_dir: Path,
) -> tuple[int, str, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        returncode = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        stderr += f"\ncommand_timeout_seconds={timeout_seconds}\n"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (log_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    return returncode, stdout, stderr


def validate_preflight(spec: Mapping[str, Any], *, repo_root: Path) -> tuple[list[str], dict[str, Any]]:
    preflight = spec.get("preflight") or {}
    if not isinstance(preflight, Mapping):
        return ["preflight_must_be_object"], {}
    errors: list[str] = []
    metrics: dict[str, Any] = {}

    required_paths = preflight.get("required_paths") or {}
    if isinstance(required_paths, Mapping):
        for label, raw_path in required_paths.items():
            path = as_path(str(raw_path), base=repo_root)
            metrics[f"required_path:{label}"] = str(path)
            if not path.exists():
                errors.append(f"required_path_missing:{label}:{path}")
    elif isinstance(required_paths, list):
        for raw_path in required_paths:
            path = as_path(str(raw_path), base=repo_root)
            metrics[f"required_path:{path.name}"] = str(path)
            if not path.exists():
                errors.append(f"required_path_missing:{path}")
    else:
        errors.append("preflight.required_paths_must_be_object_or_list")

    required_env = preflight.get("required_env") or []
    if not isinstance(required_env, list):
        errors.append("preflight.required_env_must_be_list")
    else:
        for name in required_env:
            value = os.environ.get(str(name), "")
            metrics[f"required_env:{name}"] = bool(value)
            if not value:
                errors.append(f"required_env_missing:{name}")

    vault_stamp = repo_root / "runtime" / "vault-gate" / ".last-vault-gate.json"
    metrics["vault_gate_stamp"] = str(vault_stamp)
    if preflight.get("require_vault_gate", True) and not vault_stamp.exists():
        errors.append(f"vault_gate_stamp_missing:{vault_stamp}")
    return errors, metrics


def iter_artifacts_from_manifest(manifest_path: Path, manifest: Mapping[str, Any]) -> Iterable[Path]:
    out_dir_raw = manifest.get("out_dir") or manifest_path.parent
    out_dir = as_path(str(out_dir_raw), base=manifest_path.parent)
    unit_results = manifest.get("unit_results")
    yielded: set[Path] = set()
    if isinstance(unit_results, list):
        for row in unit_results:
            if not isinstance(row, Mapping):
                continue
            raw = row.get("screening_artifact_path") or row.get("screening_artifact_relpath")
            if not raw:
                continue
            path = as_path(str(raw), base=out_dir)
            if path not in yielded:
                yielded.add(path)
                yield path
    if not yielded:
        for path in out_dir.rglob("screening_artifact.json"):
            if path not in yielded:
                yielded.add(path)
                yield path


def inspect_screening_artifacts(paths: Iterable[Path], *, limit: int | None = None) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    metrics: dict[str, Any] = {
        "artifact_count": 0,
        "promoted_ids": 0,
        "positive_trade_rows": 0,
        "feature_plane_status_counts": {},
        "feature_set_id_counts": {},
        "bar_construction_id_counts": {},
    }
    feature_counts: Counter[str] = Counter()
    feature_set_counts: Counter[str] = Counter()
    bar_counts: Counter[str] = Counter()
    missing = 0

    for idx, path in enumerate(paths):
        if limit is not None and idx >= limit:
            break
        if not path.exists():
            missing += 1
            continue
        payload = read_json(path)
        if not isinstance(payload, Mapping):
            errors.append(f"screening_artifact_not_object:{path}")
            continue
        metrics["artifact_count"] += 1
        metrics["promoted_ids"] += promoted_count(payload)
        metrics["positive_trade_rows"] += positive_trade_rows(payload)
        for value in collect_key_values(payload, {"feature_plane_status"}):
            if isinstance(value, str) and value:
                feature_counts[value] += 1
        for value in collect_key_values(payload, {"feature_set_id"}):
            if isinstance(value, str) and value:
                feature_set_counts[value] += 1
        for value in collect_key_values(payload, {"bar_construction_id"}):
            if isinstance(value, str) and value:
                bar_counts[value] += 1

    metrics["missing_artifacts"] = missing
    metrics["feature_plane_status_counts"] = dict(feature_counts)
    metrics["feature_set_id_counts"] = dict(feature_set_counts)
    metrics["bar_construction_id_counts"] = dict(bar_counts)

    if missing:
        errors.append(f"screening_artifacts_missing:n={missing}")
    if metrics["artifact_count"] <= 0:
        errors.append("screening_artifacts_missing")
    if metrics["promoted_ids"] <= 0:
        errors.append(f"zero_promoted_ids:n={metrics['artifact_count']}")
    if metrics["positive_trade_rows"] <= 0:
        errors.append(f"zero_positive_trade_rows:n={metrics['artifact_count']}")
    for status in sorted(FORBIDDEN_FEATURE_PLANE_STATUSES & set(feature_counts)):
        errors.append(f"forbidden_feature_plane_status:{status}:n={feature_counts[status]}")
    for feature_set_id in sorted(FORBIDDEN_FEATURE_SET_IDS & set(feature_set_counts)):
        errors.append(
            f"forbidden_feature_set_id:{feature_set_id}:n={feature_set_counts[feature_set_id]}"
        )
    for bar_id in sorted(FORBIDDEN_BAR_CONSTRUCTION_IDS & set(bar_counts)):
        errors.append(f"forbidden_bar_construction_id:{bar_id}:n={bar_counts[bar_id]}")
    return errors, metrics


def validate_vectorbt(outputs: Mapping[str, Any], stage_spec: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    artifacts: list[Path] = []
    if outputs.get("screening_artifact"):
        artifacts.append(Path(str(outputs["screening_artifact"])))
    if outputs.get("screening_artifacts"):
        artifacts.extend(Path(str(path)) for path in outputs["screening_artifacts"])
    if outputs.get("paid_run_manifest"):
        manifest_path = Path(str(outputs["paid_run_manifest"]))
        manifest = read_json(manifest_path)
        if not isinstance(manifest, Mapping):
            return [f"paid_run_manifest_not_object:{manifest_path}"], {}
        artifacts.extend(iter_artifacts_from_manifest(manifest_path, manifest))

    validation = stage_spec.get("validation") if isinstance(stage_spec, Mapping) else {}
    limit = None
    if isinstance(validation, Mapping) and validation.get("artifact_scan_limit") is not None:
        limit = int(validation["artifact_scan_limit"])
    return inspect_screening_artifacts(artifacts, limit=limit)


def validate_promoted(outputs: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    candidates_path = outputs.get("promoted_candidates") or outputs.get("promoted_ids")
    if not candidates_path:
        return ["promoted_candidates_output_missing"], {}
    path = Path(str(candidates_path))
    payload = read_json(path)
    metrics: dict[str, Any] = {}
    count = 0
    if isinstance(payload, Mapping):
        value = numeric(payload.get("promoted_id_count"))
        if value is not None:
            count = int(value)
        ids = payload.get("promoted_ids")
        if isinstance(ids, list):
            count = max(count, len(ids))
        promoted = payload.get("promoted")
        if isinstance(promoted, list):
            count = max(count, len(promoted))
    elif isinstance(payload, list):
        count = len(payload)
    else:
        return [f"promoted_candidates_not_object_or_list:{path}"], {}
    metrics["promoted_candidate_count"] = count
    if count <= 0:
        return ["zero_promoted_candidates"], metrics
    return [], metrics


def validate_robustness_bridge(outputs: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors, metrics = validate_status_outputs(outputs)
    eligibility_path = (
        outputs.get("applied_screening_artifact")
        or outputs.get("replay_eligibility_artifact")
        or outputs.get("screening_artifact")
    )
    if not eligibility_path:
        errors.append("robustness_bridge_missing_applied_screening_artifact")
        return errors, metrics
    payload = read_json(Path(str(eligibility_path)))
    eligible = sum(
        1
        for value in collect_key_values(payload, {"replay_eligibility_status"})
        if str(value) == "eligible"
    )
    metrics["replay_eligible_rows"] = eligible
    if eligible <= 0:
        errors.append("zero_replay_eligible_rows_after_robustness")
    if not outputs.get("robustness_evidence_receipt"):
        errors.append("robustness_evidence_receipt_output_missing")
    return errors, metrics


def validate_status_outputs(outputs: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    metrics: dict[str, Any] = {}
    errors: list[str] = []
    for path in flatten_output_files(outputs):
        if path.suffix.lower() != ".json" or not path.exists():
            continue
        payload = read_json(path)
        if isinstance(payload, Mapping):
            status = str(payload.get("status") or payload.get("state") or "").lower()
            metrics[f"{path.name}:status"] = status or None
            if status in FAILED_STATUS_VALUES:
                errors.append(f"output_status_failed:{path}:{status}")
    return errors, metrics


def validate_stage(
    stage_id: str,
    stage_spec: Mapping[str, Any],
    *,
    outputs: Mapping[str, Any],
    repo_root: Path,
    full_spec: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    metrics: dict[str, Any] = {}

    for path in flatten_output_files(outputs):
        if not path.exists():
            errors.append(f"declared_output_missing:{path}")

    if errors:
        return errors, metrics

    if stage_id == STAGE_0_ONTOLOGY:
        return validate_preflight(full_spec, repo_root=repo_root)
    if stage_id == STAGE_1_VECTORBT_SCREEN:
        return validate_vectorbt(outputs, stage_spec)
    if stage_id == STAGE_2_PROMOTED_AGGREGATION:
        return validate_promoted(outputs)
    if stage_id == STAGE_2_ROBUSTNESS_EVIDENCE:
        return validate_robustness_bridge(outputs)
    if stage_id in {
        STAGE_3_HFTBACKTEST_REALISM,
        STAGE_4_WORKBENCH_ROBUSTNESS,
        STAGE_5_LIFECYCLE_BEHAVIOR,
    }:
        return validate_status_outputs(outputs)
    return errors, metrics


def run_stage(
    stage_id: str,
    stage_spec: Mapping[str, Any],
    *,
    repo_root: Path,
    bundle_dir: Path,
    run_id: str,
    full_spec: Mapping[str, Any],
    dry_run: bool,
) -> Mapping[str, Any]:
    run_ordinal = RUN_STAGE_IDS.index(stage_id)
    stage_dir = bundle_dir / f"{run_ordinal:02d}_{stage_id}"
    receipt_path = bundle_dir / "receipts" / f"{stage_id}.json"
    started_at = utc_now()
    outputs = resolve_output_paths(stage_spec.get("outputs") or {}, base=repo_root)
    command = stage_spec.get("command")
    commands = stage_spec.get("commands")
    receipt: dict[str, Any] = {
        "version": VERSION,
        "run_id": run_id,
        "stage_id": stage_id,
        "stage_ordinal": run_ordinal,
        "stage_name": stage_name(stage_id),
        "status": "running",
        "started_at": started_at,
        "completed_at": None,
        "command": command,
        "commands": commands,
        "outputs": outputs,
        "output_hashes": {},
        "validation_errors": [],
        "metrics": {},
    }
    receipt.update(stage_stamp(stage_id))
    write_json(receipt_path, receipt)

    if dry_run:
        receipt["status"] = "planned"
        receipt["completed_at"] = utc_now()
        write_json(receipt_path, receipt)
        return receipt

    command_list: list[list[str]] = []
    if commands is not None:
        if (
            not isinstance(commands, list)
            or not commands
            or not all(isinstance(item, list) and item for item in commands)
        ):
            receipt["status"] = "failed"
            receipt["validation_errors"] = ["stage_commands_must_be_non_empty_list_of_string_lists"]
            receipt["completed_at"] = utc_now()
            write_json(receipt_path, receipt)
            raise PipelineBlocked(stage_id, receipt["validation_errors"])
        command_list = [list(item) for item in commands]
    elif command is not None:
        if not isinstance(command, list) or not command:
            receipt["status"] = "failed"
            receipt["validation_errors"] = ["stage_command_must_be_non_empty_string_list"]
            receipt["completed_at"] = utc_now()
            write_json(receipt_path, receipt)
            raise PipelineBlocked(stage_id, receipt["validation_errors"])
        command_list = [list(command)]

    if command_list:
        if not all(all(isinstance(item, str) for item in cmd) for cmd in command_list):
            receipt["status"] = "failed"
            receipt["validation_errors"] = ["stage_command_must_be_string_list"]
            receipt["completed_at"] = utc_now()
            write_json(receipt_path, receipt)
            raise PipelineBlocked(stage_id, receipt["validation_errors"])
        cwd = as_path(str(stage_spec.get("cwd") or repo_root), base=repo_root)
        timeout = stage_spec.get("timeout_seconds")
        command_results = []
        for idx, cmd in enumerate(command_list):
            returncode, _stdout, stderr = run_command(
                cmd,
                cwd=cwd,
                env=stage_spec.get("env") if isinstance(stage_spec.get("env"), Mapping) else None,
                timeout_seconds=int(timeout) if timeout is not None else None,
                log_dir=stage_dir / f"cmd_{idx}",
            )
            command_results.append({"index": idx, "command": cmd, "returncode": returncode})
            if returncode != 0:
                receipt["status"] = "failed"
                receipt["command_results"] = command_results
                receipt["validation_errors"] = [f"command_failed:index={idx}:returncode={returncode}"]
                if stderr:
                    receipt["stderr_tail"] = stderr[-2000:]
                receipt["completed_at"] = utc_now()
                write_json(receipt_path, receipt)
                raise PipelineBlocked(stage_id, receipt["validation_errors"])
        receipt["command_results"] = command_results
    elif stage_id != STAGE_0_ONTOLOGY and not outputs:
        receipt["status"] = "blocked"
        receipt["validation_errors"] = ["stage_not_configured:no_command_or_outputs"]
        receipt["completed_at"] = utc_now()
        write_json(receipt_path, receipt)
        raise PipelineBlocked(stage_id, receipt["validation_errors"])

    try:
        errors, metrics = validate_stage(
            stage_id,
            stage_spec,
            outputs=outputs,
            repo_root=repo_root,
            full_spec=full_spec,
        )
    except Exception as exc:
        error = f"stage_validation_error:{type(exc).__name__}:{str(exc)[:500]}"
        receipt["status"] = "error"
        receipt["validation_errors"] = [error]
        for path in flatten_output_files(outputs):
            if path.exists() and path.is_file():
                receipt["output_hashes"][str(path)] = sha256_file(path)
        receipt["completed_at"] = utc_now()
        write_json(receipt_path, receipt)
        raise PipelineBlocked(stage_id, receipt["validation_errors"]) from exc
    receipt["validation_errors"] = errors
    receipt["metrics"] = metrics
    for path in flatten_output_files(outputs):
        if path.exists() and path.is_file():
            receipt["output_hashes"][str(path)] = sha256_file(path)
    receipt["status"] = "passed" if not errors else "blocked"
    receipt["completed_at"] = utc_now()
    write_json(receipt_path, receipt)
    if errors:
        raise PipelineBlocked(stage_id, errors)
    return receipt


def run_pipeline(
    spec_path: Path,
    *,
    resume: bool = False,
    dry_run: bool = False,
    force_rerun_failed: bool = False,
) -> Mapping[str, Any]:
    spec_path = spec_path.resolve()
    spec = read_json(spec_path)
    if not isinstance(spec, Mapping):
        raise ValueError("pipeline spec must be a JSON object")
    repo_root, bundle_dir, run_id, target_stage = build_context(spec_path, spec)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    stages_spec = spec.get("stages") or {}
    if not isinstance(stages_spec, Mapping):
        raise ValueError("pipeline spec 'stages' must be an object")

    stage_receipts: list[Mapping[str, Any]] = []
    try:
        for stage_id in stage_ids_through(target_stage):
            if resume:
                existing = existing_stage_receipt(bundle_dir, stage_id)
                if existing is not None and existing.get("status") == "passed":
                    stage_receipts.append(existing)
                    continue
                if (
                    existing is not None
                    and existing.get("status") in FAILED_STATUS_VALUES
                    and not force_rerun_failed
                ):
                    stage_receipts.append(existing)
                    status = str(existing.get("status") or "unknown")
                    errors = [
                        f"resume_refuses_existing_{status}_receipt:use_--force-rerun-failed_after_fix"
                    ]
                    receipt_errors = existing.get("validation_errors")
                    if isinstance(receipt_errors, list):
                        errors.extend(str(error) for error in receipt_errors)
                    raise PipelineBlocked(stage_id, errors)
            stage_spec = stages_spec.get(stage_id) or {}
            if not isinstance(stage_spec, Mapping):
                raise ValueError(f"stage spec must be an object: {stage_id}")
            receipt = run_stage(
                stage_id,
                stage_spec,
                repo_root=repo_root,
                bundle_dir=bundle_dir,
                run_id=run_id,
                full_spec=spec,
                dry_run=dry_run,
            )
            stage_receipts.append(receipt)
            if dry_run:
                break
    except PipelineBlocked as exc:
        return write_bundle(
            bundle_dir,
            run_id=run_id,
            target_stage=target_stage,
            spec_path=spec_path,
            stage_receipts=stage_receipts,
            status="blocked",
            failures=[f"{exc.stage_id}:{error}" for error in exc.errors],
        )

    status = "planned" if dry_run else "ready"
    return write_bundle(
        bundle_dir,
        run_id=run_id,
        target_stage=target_stage,
        spec_path=spec_path,
        stage_receipts=stage_receipts,
        status=status,
    )


def load_status(spec_path: Path) -> Mapping[str, Any]:
    spec_path = spec_path.resolve()
    spec = read_json(spec_path)
    if not isinstance(spec, Mapping):
        raise ValueError("pipeline spec must be a JSON object")
    _repo_root, bundle_dir, _run_id, _target_stage = build_context(spec_path, spec)
    status_path = bundle_dir / "status.json"
    if not status_path.exists():
        return {"status": "not_started", "bundle_dir": str(bundle_dir)}
    return read_json(status_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="Path to ResearchRunSpec JSON")
    parser.add_argument("--resume", action="store_true", help="Continue from passed stage receipts")
    parser.add_argument(
        "--force-rerun-failed",
        action="store_true",
        help="With --resume, rerun a failed/blocked/error stage after its root cause was fixed",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write the first planned receipt only")
    parser.add_argument("--status", action="store_true", help="Print current bundle status and exit")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    try:
        if args.status:
            print(json.dumps(load_status(spec_path), indent=2, sort_keys=True))
            return 0
        bundle = run_pipeline(
            spec_path,
            resume=args.resume,
            dry_run=args.dry_run,
            force_rerun_failed=args.force_rerun_failed,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(bundle, indent=2, sort_keys=True))
    return 0 if bundle.get("status") in {"ready", "planned"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
