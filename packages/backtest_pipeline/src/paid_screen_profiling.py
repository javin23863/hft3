"""Stage-level profiling, failure diagnostics, and run-manifest status for the
VectorBT paid-screen redesign.

Phase 1 deliverable: instrumentation and correctness baseline.
This module is import-safe: no heavy dependencies (vectorbt, numpy) are imported
at module level.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import os
import sys
import traceback
import time
import json
import hashlib
from pathlib import Path
from typing import Any, Mapping, Optional

from backtest_pipeline.src.paid_screen_types import (
    PaidScreenUnit,
    RESEARCH_CLOCK_SCHEDULED_EVENT,
    TARGET_ONLY_CONTEXT_SET_ID,
)
from backtest_pipeline.src.research_clock import ResearchClockError, validate_research_clock

DEFAULT_RESEARCH_SPLIT = "discovery_confirmation"

NATIVE_THREAD_LIMIT_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


@dataclass
class PaidScreenPerformanceCounters:
    """Stage 1 paid-screen throughput counters (manifest + benchmark proof)."""

    feature_store_load_count: int = 0
    feature_store_cache_hits: int = 0
    feature_store_cache_misses: int = 0
    models_evaluated_per_load: list[int] = field(default_factory=list)
    raw_signal_computations: int = 0
    trials_evaluated: int = 0
    signal_reuse_ratio: float = 0.0
    portfolio_call_count: int = 0
    trials_per_portfolio_call: list[int] = field(default_factory=list)
    matrix_chunk_size: int = 0
    native_thread_limits: dict[str, str] = field(default_factory=dict)

    def record_models_for_last_load(self, model_count: int) -> None:
        if model_count > 0:
            self.models_evaluated_per_load.append(int(model_count))

    def finalize_signal_reuse(self) -> None:
        if self.trials_evaluated > 0 and self.raw_signal_computations > 0:
            self.signal_reuse_ratio = float(self.trials_evaluated) / float(
                self.raw_signal_computations
            )

    def to_dict(self) -> dict[str, Any]:
        self.finalize_signal_reuse()
        return {
            "feature_store_load_count": self.feature_store_load_count,
            "feature_store_cache_hits": self.feature_store_cache_hits,
            "feature_store_cache_misses": self.feature_store_cache_misses,
            "models_evaluated_per_load": list(self.models_evaluated_per_load),
            "raw_signal_computations": self.raw_signal_computations,
            "trials_evaluated": self.trials_evaluated,
            "signal_reuse_ratio": round(self.signal_reuse_ratio, 4),
            "portfolio_call_count": self.portfolio_call_count,
            "trials_per_portfolio_call": list(self.trials_per_portfolio_call),
            "matrix_chunk_size": self.matrix_chunk_size,
            "native_thread_limits": dict(self.native_thread_limits),
        }


def apply_native_thread_limits(threads: int = 1) -> dict[str, str]:
    """Pin BLAS/OpenMP thread pools for paid-screen worker processes."""
    applied: dict[str, str] = {}
    val = str(max(1, int(threads)))
    for var in NATIVE_THREAD_LIMIT_ENV_VARS:
        os.environ[var] = val
        applied[var] = val
    return applied


@dataclass(frozen=True)
class StageTimer:
    """Records timing for a single pipeline stage."""
    stage_name: str
    start_ts: float
    end_ts: float
    elapsed_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FailureDiagnostic:
    """Complete diagnostic record for a failed unit or batch."""
    unit_or_batch_id: str
    stage_name: str
    exception_type: str
    exception_message: str
    full_traceback: str
    worker_pid: int
    start_ts_utc: str
    finish_ts_utc: str
    elapsed_seconds: float
    cache_state: dict[str, Any]
    memory_usage_mb: Optional[float] = None
    input_hashes: dict[str, str] = field(default_factory=dict)
    relevant_config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunProfiler:
    """Accumulates stage timings and failure diagnostics across a run."""
    stage_timings: list[StageTimer] = field(default_factory=list)
    failures: list[FailureDiagnostic] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    performance: PaidScreenPerformanceCounters = field(
        default_factory=PaidScreenPerformanceCounters
    )
    _stage_starts: dict[str, float] = field(default_factory=dict)

    def start_stage(self, stage_name: str) -> None:
        self._stage_starts[stage_name] = time.monotonic()

    def end_stage(self, stage_name: str, metadata: dict | None = None) -> float:
        start = self._stage_starts.pop(stage_name, time.monotonic())
        end = time.monotonic()
        elapsed = end - start
        self.stage_timings.append(StageTimer(
            stage_name=stage_name,
            start_ts=start,
            end_ts=end,
            elapsed_seconds=elapsed,
            metadata=metadata or {},
        ))
        return elapsed

    def record_failure(self, stage_name: str, exc: Exception,
                        unit_or_batch_id: str,
                        cache_state: dict | None = None,
                        input_hashes: dict | None = None,
                        config: dict | None = None) -> FailureDiagnostic:
        diag = FailureDiagnostic(
            unit_or_batch_id=unit_or_batch_id,
            stage_name=stage_name,
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            full_traceback=traceback.format_exc(),
            worker_pid=os.getpid(),
            start_ts_utc=datetime.now(timezone.utc).isoformat(),
            finish_ts_utc=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=0.0,
            cache_state=cache_state or {},
            memory_usage_mb=_get_memory_mb(),
            input_hashes=input_hashes or {},
            relevant_config=config or {},
        )
        self.failures.append(diag)
        return diag

    def manifest_summary(self) -> dict:
        by_stage: dict[str, list[float]] = {}
        for t in self.stage_timings:
            by_stage.setdefault(t.stage_name, []).append(t.elapsed_seconds)
        stage_summary = {}
        for stage, times in sorted(by_stage.items()):
            times_sorted = sorted(times)
            p50 = times_sorted[len(times_sorted) // 2]
            p95_idx = int(len(times_sorted) * 0.95)
            p95 = times_sorted[min(p95_idx, len(times_sorted) - 1)]
            stage_summary[stage] = {
                "count": len(times),
                "total_seconds": sum(times),
                "p50_seconds": p50,
                "p95_seconds": p95,
            }
        perf = self.performance.to_dict()
        return {
            "time_by_stage": stage_summary,
            "total_failures": len(self.failures),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": self.cache_hits / max(self.cache_hits + self.cache_misses, 1),
            **perf,
        }


def _get_memory_mb() -> Optional[float]:
    """Get peak resident memory in MB, if available.

    Tries the Unix-only ``resource`` module first, then falls back to
    ``psutil`` (cross-platform) and finally ``tracemalloc``.  On Windows
    the ``resource`` module is unavailable, so the psutil fallback is the
    primary path.  Returns ``None`` when no backend can report memory.
    """
    # Unix: resource.getrusage gives ru_maxrss in KB (divide by 1024).
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        pass
    # Cross-platform (incl. Windows): psutil reports RSS in bytes.
    try:
        import psutil  # type: ignore[import-not-found]
        proc = psutil.Process()
        mem = proc.memory_info()
        rss = getattr(mem, "rss", None)
        if rss is not None:
            return float(rss) / (1024.0 * 1024.0)
    except Exception:
        pass
    return None


def derive_run_research_split(units_raw: list[dict[str, Any]]) -> str:
    """Return the canonical research split for a run's JSONL units."""
    splits = sorted(
        {
            str(row["research_split"]).strip()
            for row in units_raw
            if isinstance(row.get("research_split"), str) and str(row["research_split"]).strip()
        }
    )
    if not splits:
        return DEFAULT_RESEARCH_SPLIT
    if len(splits) > 1:
        raise ValueError(
            "mixed research_split values in unit manifest: "
            + ", ".join(splits)
        )
    return splits[0]


def artifact_hash_fields(payload: Mapping[str, Any]) -> dict[str, str]:
    """Extract resume-critical hash fields from a screening artifact."""
    return {
        "events_csv_hash": str(
            payload.get("events_csv_hash_or_not_applicable")
            or payload.get("events_csv_hash")
            or ""
        ),
        "lake_manifest_hash": str(payload.get("lake_manifest_hash") or ""),
        "data_manifest_hash": str(payload.get("data_manifest_hash") or ""),
    }


def artifact_provenance_fields(payload: Mapping[str, Any]) -> dict[str, str]:
    """Extract code/registry/signal/feature provenance from a screening artifact."""
    feature_recipe_hash = str(payload.get("feature_recipe_hash") or "")
    if not feature_recipe_hash:
        for row in _candidate_rows(payload):
            meta = row.get("base_candidate_metadata")
            if not isinstance(meta, Mapping):
                meta = {}
            feature_recipe_hash = str(
                row.get("feature_recipe_hash")
                or meta.get("feature_recipe_hash")
                or ""
            )
            if feature_recipe_hash:
                break
    return {
        "code_commit": str(payload.get("code_commit") or ""),
        "model_registry_hash": str(payload.get("model_registry_hash") or ""),
        "signal_implementation_hash": str(payload.get("signal_implementation_hash") or ""),
        "feature_set_hash": str(payload.get("feature_set_hash") or ""),
        "feature_recipe_hash": feature_recipe_hash,
    }


def resolve_events_csv_hash(
    *,
    explicit_hash: str | None,
    events_csv: Path | None,
    repo_root: Path,
) -> str:
    """Resolve events CSV content hash; fail closed when source is unavailable."""
    if explicit_hash and str(explicit_hash).strip():
        return str(explicit_hash).strip()
    csv_path = events_csv
    if csv_path is None:
        env_path = os.environ.get("DATA_SYSTEM_EVENTS_CSV", "").strip()
        if env_path:
            csv_path = Path(env_path)
        else:
            csv_path = repo_root / "packages" / "data_system" / "config" / "events.csv"
    if not csv_path.is_absolute():
        csv_path = repo_root / csv_path
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"events CSV unavailable for hash: {csv_path} "
            "(pass --events-csv-hash or --events-csv)"
        )
    return hashlib.sha256(csv_path.read_bytes()).hexdigest()[:32]


def resolve_lake_manifest_hash(
    *,
    explicit_hash: str | None,
    repo_root: Path,
) -> str:
    """Resolve lake manifest content hash; fail closed when source is unavailable."""
    if explicit_hash and str(explicit_hash).strip():
        return str(explicit_hash).strip()
    manifest_env = os.environ.get("HFT3_MANIFEST_PATH", "").strip()
    if not manifest_env:
        default = repo_root / "data" / "manifest.parquet"
        if default.is_file():
            manifest_path = default
        else:
            raise ValueError(
                "HFT3_MANIFEST_PATH not set and --lake-manifest-hash omitted "
                "(no default lake manifest at data/manifest.parquet)"
            )
    else:
        manifest_path = Path(manifest_env)
        if not manifest_path.is_absolute():
            manifest_path = repo_root / manifest_path
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"lake manifest unavailable for hash: {manifest_path} "
            "(pass --lake-manifest-hash or set HFT3_MANIFEST_PATH)"
        )
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()[:32]


def _candidate_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for collection in ("promoted", "rejected"):
        items = payload.get(collection)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping):
                rows.append(item)
    return rows


def _pipe_identity(value: Any) -> tuple[str, str, str] | None:
    text = str(value or "").strip()
    if "|" not in text:
        return None
    parts = text.split("|")
    if len(parts) < 3:
        return None
    model_id, symbol, event_id = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if model_id and symbol and event_id:
        return model_id, symbol, event_id
    return None


def _row_unit_identity(row: Mapping[str, Any]) -> tuple[str, str, str] | None:
    meta = row.get("base_candidate_metadata")
    if not isinstance(meta, Mapping):
        meta = {}
    model_id = str(
        row.get("model_id") or row.get("hypothesis_id") or meta.get("model_id") or ""
    ).strip()
    symbol = str(row.get("symbol") or meta.get("symbol") or "").strip()
    event_id = str(meta.get("event_id") or "").strip()
    if not event_id:
        parsed = _pipe_identity(row.get("base_candidate_id"))
        if parsed is not None:
            model_id = model_id or parsed[0]
            symbol = symbol or parsed[1]
            event_id = parsed[2]
    if not model_id or not symbol or not event_id:
        parsed = _pipe_identity(row.get("candidate_id"))
        if parsed is not None:
            model_id = model_id or parsed[0]
            symbol = symbol or parsed[1]
            event_id = event_id or parsed[2]
    if model_id and symbol and event_id:
        return model_id, symbol, event_id
    return None


def _canonical_clock(value: Any) -> str:
    try:
        return validate_research_clock(str(value))
    except (ResearchClockError, TypeError):
        return ""


def _row_context_set(row: Mapping[str, Any]) -> str:
    meta = row.get("base_candidate_metadata")
    if not isinstance(meta, Mapping):
        meta = {}
    return str(
        row.get("context_set_id")
        or row.get("allowed_context_set_id")
        or row.get("allowed_context_set_id_or_null")
        or meta.get("context_set_id")
        or meta.get("allowed_context_set_id")
        or meta.get("allowed_context_set_id_or_null")
        or ""
    ).strip()


def _context_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _artifact_unit_context_matches(payload: Mapping[str, Any], unit: PaidScreenUnit) -> bool:
    expected_clock = _canonical_clock(unit.research_clock)
    artifact_clock = _canonical_clock(payload.get("research_clock"))
    if artifact_clock and expected_clock and artifact_clock != expected_clock:
        return False
    if not artifact_clock and expected_clock != RESEARCH_CLOCK_SCHEDULED_EVENT:
        return False

    expected_context = str(unit.context_set_id or TARGET_ONLY_CONTEXT_SET_ID).strip()
    artifact_context = str(
        payload.get("allowed_context_set_id_or_null")
        or payload.get("context_set_id")
        or payload.get("allowed_context_set_id")
        or ""
    ).strip()
    if not artifact_context:
        for row in _candidate_rows(payload):
            artifact_context = _row_context_set(row)
            if artifact_context:
                break
    if artifact_context and artifact_context != expected_context:
        return False
    if not artifact_context and expected_context != TARGET_ONLY_CONTEXT_SET_ID:
        return False

    artifact_declared = _context_list(payload.get("declared_context_sets"))
    expected_declared = list(unit.declared_context_sets)
    if artifact_declared and sorted(artifact_declared) != sorted(expected_declared):
        return False
    if not artifact_declared and expected_context != TARGET_ONLY_CONTEXT_SET_ID:
        return False
    return True


def artifact_unit_identity_matches(payload: Mapping[str, Any], unit: PaidScreenUnit) -> bool:
    """Return True when artifact candidate rows match the unit identity."""
    identities = [
        identity
        for row in _candidate_rows(payload)
        if (identity := _row_unit_identity(row)) is not None
    ]
    for pipe_field in ("candidate_ids",):
        items = payload.get(pipe_field)
        if not isinstance(items, list):
            continue
        for item in items:
            parsed = _pipe_identity(item)
            if parsed is not None:
                identities.append(parsed)
    if not identities:
        return False
    target = (unit.model_id, unit.symbol, unit.event_id)
    return any(identity == target for identity in identities)


def artifact_matches_resume_unit(
    payload: Mapping[str, Any],
    unit: PaidScreenUnit,
    *,
    events_csv_hash: str,
    lake_manifest_hash: str,
    research_split: str,
    screening_scope: str | None = None,
    code_commit: str | None = None,
    model_registry_hash: str | None = None,
    signal_implementation_hash: str | None = None,
    feature_set_hash: str | None = None,
    feature_recipe_hash: str | None = None,
) -> bool:
    """Fail-closed resume predicate: schema-valid artifact must match unit + run hashes."""
    hashes = artifact_hash_fields(payload)
    if hashes["events_csv_hash"] != events_csv_hash:
        return False
    if hashes["lake_manifest_hash"] != lake_manifest_hash:
        return False
    if not artifact_unit_identity_matches(payload, unit):
        return False
    if not _artifact_unit_context_matches(payload, unit):
        return False
    if unit.research_split and unit.research_split != research_split:
        return False
    artifact_split = payload.get("research_split")
    if isinstance(artifact_split, str) and artifact_split.strip():
        if artifact_split.strip() != research_split:
            return False
    if screening_scope:
        artifact_scope = str(payload.get("screening_scope") or "").strip()
        if artifact_scope and artifact_scope != screening_scope:
            return False

    provenance_checks = {
        "code_commit": code_commit,
        "model_registry_hash": model_registry_hash,
        "signal_implementation_hash": signal_implementation_hash,
        "feature_set_hash": feature_set_hash,
        "feature_recipe_hash": feature_recipe_hash,
    }
    if any(value is not None for value in provenance_checks.values()):
        artifact_prov = artifact_provenance_fields(payload)
        for field, expected in provenance_checks.items():
            if expected is None:
                continue
            actual = artifact_prov.get(field, "")
            if not actual or actual != expected:
                return False
    return True


def determine_manifest_status(completed: int, failed: int,
                               aborted: bool, expected: int) -> str:
    """Determine the correct run manifest status.

    Returns one of: complete, partial_failed, failed, aborted.

    The run manifest must NOT report "complete" when failed units exist.
    The process exit code and manifest status must agree.
    """
    if aborted:
        return "aborted"
    if failed == 0 and completed == expected:
        return "complete"
    if failed == 0 and completed != expected and not aborted:
        # Incomplete run with no failures is partial, not failed.
        return "partial_failed"
    if failed > 0 and completed > 0:
        return "partial_failed"
    if failed > 0 and completed == 0:
        return "failed"
    return "failed"


def write_failure_diagnostics(out_dir: str, failures: list[FailureDiagnostic]) -> str:
    """Persist failure diagnostic records to a JSON file."""
    path = os.path.join(out_dir, "failure_diagnostics.json")
    payload = [f.to_dict() for f in failures]
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    return path


def _merge_unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def _merge_reason_maps(maps: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for mapping in maps:
        if isinstance(mapping, dict):
            merged.update(mapping)
    return merged


def _aggregate_provenance_hash(values: list[Any]) -> str:
    """Derive a run-level hash from one or many child provenance values."""
    unique = sorted({str(v) for v in values if v not in (None, "")})
    if not unique:
        return "no_unit_provenance"
    if len(unique) == 1:
        return unique[0]
    payload = json.dumps({"values": unique}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _build_aggregate_provenance(unit_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect honest child provenance for a run-level aggregate artifact."""
    unit_artifact_hashes: list[str] = []
    unit_data_manifest_hashes: list[str] = []
    for artifact in unit_artifacts:
        child_hash = artifact.get("screening_artifact_hash")
        if child_hash:
            unit_artifact_hashes.append(str(child_hash))
        data_hash = artifact.get("data_manifest_hash")
        if data_hash:
            unit_data_manifest_hashes.append(str(data_hash))
    return {
        "scope": "run_level_merge",
        "unit_count": len(unit_artifacts),
        "unit_artifact_hashes": unit_artifact_hashes,
        "unit_data_manifest_hashes": sorted(set(unit_data_manifest_hashes)),
    }


def _merge_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        merged.append(dict(row))
    return merged


def merge_unit_screening_artifacts(
    unit_artifacts: list[dict[str, Any]],
    *,
    run_id: str,
    finished_at_utc: str | None = None,
) -> dict[str, Any]:
    """Merge per-unit screening artifacts into one run-level payload."""
    if not unit_artifacts:
        raise ValueError("unit_artifacts must not be empty")

    base = dict(unit_artifacts[0])
    base["run_id"] = run_id
    base["created_at_utc"] = finished_at_utc or base.get("created_at_utc") or datetime.now(timezone.utc).isoformat()

    candidate_ids: list[str] = []
    promoted_ids: list[str] = []
    rejected_ids: list[str] = []
    stop_reasons: list[str] = []
    promoted_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    candidate_reasons: list[dict[str, Any]] = []
    promoted_reasons: list[dict[str, Any]] = []
    rejected_reasons: list[dict[str, Any]] = []
    trials_run = 0

    for artifact in unit_artifacts:
        candidate_ids.extend(str(x) for x in artifact.get("candidate_ids") or [])
        promoted_ids.extend(str(x) for x in artifact.get("promoted_ids") or [])
        rejected_ids.extend(str(x) for x in artifact.get("rejected_ids") or [])
        stop_reasons.extend(str(x) for x in artifact.get("stop_reasons") or [])
        promoted_rows.extend(_merge_candidate_rows(list(artifact.get("promoted") or [])))
        rejected_rows.extend(_merge_candidate_rows(list(artifact.get("rejected") or [])))
        if isinstance(artifact.get("candidate_reasons"), dict):
            candidate_reasons.append(artifact["candidate_reasons"])
        if isinstance(artifact.get("promoted_reasons"), dict):
            promoted_reasons.append(artifact["promoted_reasons"])
        if isinstance(artifact.get("rejected_reasons"), dict):
            rejected_reasons.append(artifact["rejected_reasons"])
        trials_run += int(artifact.get("trials_run") or 0)

    base["candidate_ids"] = _merge_unique_strings(candidate_ids)
    base["promoted_ids"] = _merge_unique_strings(promoted_ids)
    base["rejected_ids"] = _merge_unique_strings(rejected_ids)
    base["stop_reasons"] = _merge_unique_strings(stop_reasons)
    base["promoted"] = _merge_candidate_rows(promoted_rows)
    base["rejected"] = _merge_candidate_rows(rejected_rows)
    base["candidate_reasons"] = _merge_reason_maps(candidate_reasons)
    base["promoted_reasons"] = _merge_reason_maps(promoted_reasons)
    base["rejected_reasons"] = _merge_reason_maps(rejected_reasons)
    base["trials_run"] = trials_run
    base["max_total_trials"] = max(
        int(base.get("max_total_trials") or 0),
        trials_run,
        max(int(artifact.get("max_total_trials") or 0) for artifact in unit_artifacts),
    )
    base["total_candidates"] = len(base["candidate_ids"])
    base["promoted_count"] = len(base["promoted_ids"])
    base["rejected_count"] = len(base["rejected_ids"])
    provenance = _build_aggregate_provenance(unit_artifacts)
    base["aggregate_provenance"] = provenance
    base["data_manifest_hash"] = _aggregate_provenance_hash(
        [artifact.get("data_manifest_hash") for artifact in unit_artifacts]
    )
    base.pop("screening_artifact_hash", None)
    return base


def write_aggregate_screening_artifact(
    out_dir: str | os.PathLike[str],
    unit_result_rows: list[dict[str, Any]],
    *,
    finished_at_utc: str | None = None,
) -> str | None:
    """Write ``screening_artifact.json`` at the run root from unit artifacts."""
    out = os.fspath(out_dir)
    payloads: list[dict[str, Any]] = []
    for row in unit_result_rows:
        if row.get("status") not in {"OK", "OK_CACHED"}:
            continue
        rel = row.get("screening_artifact_relpath")
        if not rel:
            continue
        path = os.path.join(out, rel)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            payloads.append(data)
    if not payloads:
        return None

    merged = merge_unit_screening_artifacts(
        payloads,
        run_id=os.path.basename(os.path.normpath(out)),
        finished_at_utc=finished_at_utc,
    )
    from backtest_pipeline.src.vectorbt_adapter import persist_screening_artifact

    dest = Path(out) / "screening_artifact.json"
    persist_screening_artifact(merged, dest)
    return str(dest)
