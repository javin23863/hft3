"""Batch entry point for the paid-screen execution path.

Phase 2 deliverable: structured execution path.
screen_paid_batch() replaces the thesis-based NL routing with typed fields.
The thesis field is preserved as descriptive metadata but does NOT determine
which model or symbol is executed.

Phase 5: Wired to run_vectorbt_simulation_matrix() for chunked parameter execution.
"""
from __future__ import annotations

import os
import time
import json
import hashlib
from pathlib import Path
from typing import Callable, Optional

from backtest_pipeline.src.paid_screen_types import (
    PaidScreenUnit, WorkerContext, UnitScreeningResult, BatchingKey,
)
from backtest_pipeline.src.paid_screen_profiling import RunProfiler, DEFAULT_RESEARCH_SPLIT
from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
from backtest_pipeline.src.paid_screen_matrix import run_vectorbt_simulation_matrix
from backtest_pipeline.src.vectorbt_adapter import (
    ScreeningArtifactError,
    apply_filter_result_provenance_metadata,
    apply_promotion_gates,
)


def _cache_get(cache, key):
    """Retrieve a value from either a BoundedLRUCache or a plain dict.

    Returns the value if present, or None if absent. The caller is
    responsible for distinguishing a genuine None value from a miss —
    the NPZ cache only stores non-None OHLCV arrays, so None always
    means a miss here.
    """
    if isinstance(cache, BoundedLRUCache):
        return cache.get(key)
    return cache.get(key)


def _cache_put(cache, key, value):
    """Store a value in either a BoundedLRUCache or a plain dict."""
    if isinstance(cache, BoundedLRUCache):
        cache.put(key, value)
    else:
        cache[key] = value


def _is_bounded_lru_cache(cache) -> bool:
    """True when *cache* is a BoundedLRUCache (has observable hit/miss counters)."""
    return isinstance(cache, BoundedLRUCache)


def _worker_scratch_artifact_dir(
    repo_root: str | os.PathLike[str],
    unit_id: str,
    scratch_root: str | os.PathLike[str] | None = None,
) -> str:
    """Return a worker-local scratch directory for per-unit screening artifacts.

    Scratch paths must not live under ``research_cards/pipeline_runs`` — that
    tree is reserved for orchestrator run directories and cockpit discovery.
    """
    if scratch_root is not None:
        base = os.fspath(scratch_root)
    else:
        base = os.path.join(os.fspath(repo_root), "runtime", "paid_screen_scratch")
    return os.path.join(base, unit_id)


def _should_skip_matrix_screening(
    run_screening: bool,
    ohlcv_from_cache: bool,
    data_cache: dict | BoundedLRUCache,
) -> bool:
    """Return True when the batch should resolve models but skip matrix screening.

    Screening is skipped when ``run_screening`` is False (cache-wiring tests) or
    when a legacy plain-dict ``data_cache`` already supplied OHLCV (cache hit).
    Production workers use ``BoundedLRUCache`` with ``run_screening=True`` and
    still run the matrix after an NPZ cache hit.
    """
    if not run_screening:
        return True
    return ohlcv_from_cache and isinstance(data_cache, dict)


def _is_paid_scope(scope: str) -> bool:
    return str(scope or "").strip().lower() in {
        "paid",
        "paid-compute",
        "paid_compute",
        "broad",
        "broad-screen",
        "broad_screen",
        "all-models",
        "all_model",
        "all_models",
    }


def _ohlcv_row_count(ohlcv) -> int:
    try:
        return int(ohlcv.shape[0])
    except (AttributeError, IndexError, TypeError, ValueError):
        try:
            return len(ohlcv)
        except TypeError:
            return 0


def _resolve_models_without_screening(
    units: list[PaidScreenUnit],
    context: WorkerContext,
    profiler: RunProfiler,
) -> list[UnitScreeningResult]:
    """Resolve registry models per unit group; return SKIPPED/ERROR without VBT."""
    results: list[UnitScreeningResult] = []
    failed_unit_ids: set[str] = set()

    for model_id in {u.model_id for u in units}:
        try:
            resolve_model_from_registry(model_id, context.repo_root)
        except Exception as e:
            profiler.record_failure(
                "model_resolution", e, f"model_{model_id}",
                cache_state={"hit": False})
            for unit in units:
                if unit.model_id == model_id and unit.unit_id not in failed_unit_ids:
                    failed_unit_ids.add(unit.unit_id)
                    results.append(UnitScreeningResult(
                        unit_id=unit.unit_id,
                        status="ERROR",
                        error=str(e),
                    ))

    for unit in units:
        if unit.unit_id in failed_unit_ids:
            continue
        results.append(UnitScreeningResult(
            unit_id=unit.unit_id,
            status="SKIPPED",
            error="screening_disabled",
            elapsed_seconds=0.0,
        ))
    return results


def resolve_model_from_registry(model_id: str, repo_root: str) -> dict:
    """Resolve a model directly from the model registry by model_id.

    This replaces thesis-based NL parsing. The model_id is already structured.
    Returns the model definition dict, or raises ValueError if not found.
    """
    try:
        from features_engine.src.model_registry import (
            _models, resolve_model_id, get_hyp_id_for_slug,
        )
        try:
            resolved = resolve_model_id(model_id)
            entry = _models().get(resolved)
            if entry is None:
                raise ValueError(f"Model {model_id} not found in registry")
            return {
                "model_id": resolved,
                "slug": resolved,
                "display_name": entry.get("display_name", resolved),
                "kind": entry.get("kind", "hypothesis"),
                "hyp_id": entry.get("hyp_id"),
                "legacy_id": entry.get("legacy_id"),
            }
        except KeyError:
            raise ValueError(f"Model {model_id} not found in registry")
    except ImportError as exc:
        raise RuntimeError(
            f"model registry unavailable; cannot resolve {model_id!r} for paid screening"
        ) from exc


def split_scheme_id_for_research_split(research_split: str | None) -> str:
    """Map walk-forward research split labels to split_scheme_id (BLUEPRINT §8)."""
    split = (research_split or DEFAULT_RESEARCH_SPLIT).strip()
    mapping = {
        "discovery_confirmation": "wf_2018_2024",
        "discovery": "wf_discovery",
        "confirmation": "wf_confirmation",
        "holdout": "wf_holdout",
        "recent_holdout": "wf_recent_holdout",
        "all": "wf_all",
    }
    return mapping.get(split, "wf_2018_2024")


def build_batching_key(unit: PaidScreenUnit, ctx: WorkerContext,
                        data_manifest_hash: str,
                        feature_set_hash: str,
                        signal_implementation_hash: str,
                        model_registry_hash: str) -> BatchingKey:
    """Build a batching key for a unit given its execution context."""
    return BatchingKey(
        symbol=unit.symbol,
        event_id=unit.event_id,
        event_type=unit.event_type,
        data_manifest_hash=data_manifest_hash,
        lake_manifest_hash=ctx.lake_manifest_hash,
        events_csv_hash=ctx.events_csv_hash,
        bar_construction_id="ohlcv_1m_from_npz_or_supplied_array",
        feature_set_id=unit.feature_set_id,
        feature_set_hash=feature_set_hash,
        research_clock=unit.research_clock,
        context_set_id=unit.context_set_id,
        split_scheme_id=split_scheme_id_for_research_split(unit.research_split),
        fees_model_id="cme_fees_v1",
        slippage_model_id="slip_v1",
        signal_implementation_hash=signal_implementation_hash,
        model_registry_hash=model_registry_hash,
    )


def hash_file_content(path: Path) -> str:
    """Return SHA-256 digest (32 hex chars) for an on-disk file."""
    if not path.is_file():
        raise FileNotFoundError(f"missing file for content hash: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()[:32]


def _content_hash_file(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:32]


def _resolve_model_registry_hash(repo_root: str) -> str:
    reg = Path(repo_root) / "packages" / "features_engine" / "config" / "model_registry.yaml"
    return _content_hash_file(reg)


def _signal_implementation_hash_paths(repo_root: str) -> list[Path]:
    """Deterministic dependency set for paid-screen signal implementation provenance."""
    root = Path(repo_root)
    paths: list[Path] = []

    hypotheses_dir = root / "packages" / "features_engine" / "src" / "hypotheses"
    if hypotheses_dir.is_dir():
        paths.extend(sorted(hypotheses_dir.glob("*.py")))

    for rel in (
        "packages/features_engine/src/pipeline/market_state_pipeline.py",
        "packages/features_engine/src/model_registry.py",
        "packages/features_engine/config/model_registry.yaml",
        "packages/research_pipeline/feature_recipe.py",
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/fs_v1_screen_path.py",
        "packages/backtest_pipeline/src/paid_screen_matrix.py",
        "packages/replay/cross_asset_assembly.py",
    ):
        candidate = root / rel
        if candidate.is_file():
            paths.append(candidate)

    structural_dir = root / "packages" / "features_engine" / "src" / "structural_models"
    if structural_dir.is_dir():
        paths.extend(sorted(structural_dir.glob("*.py")))

    return paths


def _resolve_signal_implementation_hash(repo_root: str) -> str:
    root = Path(repo_root)
    digest = hashlib.sha256()
    found = False
    for path in sorted(_signal_implementation_hash_paths(repo_root)):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix().encode("utf-8")
        except ValueError:
            rel = str(path).encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        found = True
    return digest.hexdigest()[:32] if found else "unknown"


def resolve_batching_hashes(
    unit: PaidScreenUnit,
    ctx: WorkerContext,
    *,
    data_manifest_hash: str | None = None,
    feature_set_hash: str | None = None,
    signal_implementation_hash: str | None = None,
    model_registry_hash: str | None = None,
) -> tuple[str, str, str, str]:
    """Resolve content hashes used to build a full ``BatchingKey``."""
    dm = data_manifest_hash or hashlib.sha256(json.dumps({
        "event_id": unit.event_id,
        "lake_manifest_hash": ctx.lake_manifest_hash,
        "events_csv_hash": ctx.events_csv_hash,
    }, sort_keys=True).encode()).hexdigest()[:32]
    fs_id = unit.feature_set_id or "default"
    fs_hash = feature_set_hash or hashlib.sha256(json.dumps({
        "feature_set_id": fs_id,
    }, sort_keys=True).encode()).hexdigest()[:32]
    sig_hash = signal_implementation_hash or _resolve_signal_implementation_hash(ctx.repo_root)
    reg_hash = model_registry_hash or _resolve_model_registry_hash(ctx.repo_root)
    return dm, fs_hash, sig_hash, reg_hash


def batching_key_for_unit(
    unit: PaidScreenUnit,
    ctx: WorkerContext,
    *,
    signal_implementation_hash: str | None = None,
    model_registry_hash: str | None = None,
) -> BatchingKey:
    """Build the full batching key for *unit* under *ctx*."""
    dm, fs_hash, sig_hash, reg_hash = resolve_batching_hashes(
        unit,
        ctx,
        signal_implementation_hash=signal_implementation_hash,
        model_registry_hash=model_registry_hash,
    )
    return build_batching_key(
        unit, ctx,
        data_manifest_hash=dm,
        feature_set_hash=fs_hash,
        signal_implementation_hash=sig_hash,
        model_registry_hash=reg_hash,
    )


def group_units_by_batch_key(units: list[PaidScreenUnit],
                               ctx: WorkerContext) -> dict[str, list[PaidScreenUnit]]:
    """Group units by their full ``BatchingKey`` for safe batch execution."""
    if not units:
        return {}
    sig_hash = _resolve_signal_implementation_hash(ctx.repo_root)
    reg_hash = _resolve_model_registry_hash(ctx.repo_root)
    groups: dict[str, list[PaidScreenUnit]] = {}
    for unit in units:
        key = batching_key_for_unit(
            unit,
            ctx,
            signal_implementation_hash=sig_hash,
            model_registry_hash=reg_hash,
        )
        groups.setdefault(key.group_id(), []).append(unit)
    return groups


def _assert_batch_key_compatible(units: list[PaidScreenUnit], ctx: WorkerContext) -> None:
    """Fail closed when a batch mixes incompatible batching keys."""
    if len(units) <= 1:
        return
    keys = {batching_key_for_unit(unit, ctx) for unit in units}
    if len(keys) != 1:
        raise ValueError(
            "incompatible BatchingKey fields in single batch: "
            f"{len(keys)} distinct keys for {len(units)} units"
        )


def ohlcv_data_cache_key(unit: PaidScreenUnit, context: WorkerContext) -> str:
    """Symbol-aware OHLCV cache key aligned with ``BatchingKey.cache_key()``."""
    return batching_key_for_unit(unit, context).cache_key()


def _fs_v1_screening_available(repo_root: Path) -> bool:
    """True when the repo layout supports fs_v1 imports and store resolution."""
    return (repo_root / "packages").is_dir()


def _ohlcv_aligns_with_fs_v1_store(ohlcv, fs_ctx) -> bool:
    """Guard cache/OHLCV against pairing stub bars with fs_v1 PIT signals."""
    store_ts = fs_ctx.store.get("ts")
    if store_ts is None:
        return False
    return len(ohlcv) == len(store_ts)


def _fs_v1_context_cache_key(unit: PaidScreenUnit, context: WorkerContext) -> str:
    """Cache key for fs_v1 feature-store context (one load per batching key)."""
    return "fs_v1_ctx:" + batching_key_for_unit(unit, context).feature_cache_key()


def _get_or_load_fs_v1_context(
    unit: PaidScreenUnit,
    context: WorkerContext,
    data_cache: dict | BoundedLRUCache,
    profiler: RunProfiler,
):
    """Load fs_v1 screen context once per batching key; record cache counters."""
    cache_key = _fs_v1_context_cache_key(unit, context)
    cached = _cache_get(data_cache, cache_key)
    if cached is not None:
        profiler.performance.feature_store_cache_hits += 1
        return cached
    profiler.performance.feature_store_cache_misses += 1
    profiler.performance.feature_store_load_count += 1
    fs_ctx = _try_resolve_fs_v1_context(unit, context)
    if fs_ctx is not None:
        _cache_put(data_cache, cache_key, fs_ctx)
    return fs_ctx


def _try_resolve_fs_v1_context(
    unit: PaidScreenUnit,
    context: WorkerContext,
):
    """Return fs_v1 screen context when feature-store rows exist for *unit*."""
    repo_root = Path(context.repo_root)
    if not _fs_v1_screening_available(repo_root):
        return None
    try:
        from backtest_pipeline.src.fs_v1_screen_path import resolve_fs_v1_screen_context
    except ImportError:
        return None
    try:
        return resolve_fs_v1_screen_context(
            repo_root=repo_root,
            event_id=unit.event_id,
            symbol=unit.symbol,
        )
    except (ImportError, ModuleNotFoundError, OSError, ValueError):
        return None


def _paid_scope_fs_v1_gate_error(
    units: list[PaidScreenUnit],
    context: WorkerContext,
    reason: str,
    profiler: RunProfiler,
    ohlcv_cache_state: bool,
) -> list[UnitScreeningResult]:
    """Fail all units when paid scope cannot guarantee fs_v1-consistent screening."""
    reason_text = str(reason)
    results: list[UnitScreeningResult] = []
    for unit in units:
        profiler.record_failure(
            "paid_scope_fs_v1_gate",
            RuntimeError(reason_text),
            unit.unit_id,
            cache_state={"hit": bool(ohlcv_cache_state)},
        )
        results.append(UnitScreeningResult(
            unit_id=unit.unit_id,
            status="ERROR",
            error=reason_text,
            error_category="data_quality",
        ))
    return results


def _load_ohlcv_for_unit(unit: PaidScreenUnit, context: WorkerContext):
    """Load OHLCV for one unit via symbol-aware fs_v1 or NPZ paths."""
    fs_ctx = _try_resolve_fs_v1_context(unit, context)
    if fs_ctx is not None:
        from backtest_pipeline.src.fs_v1_screen_path import ohlcv_from_feature_store

        return ohlcv_from_feature_store(fs_ctx.store)

    from backtest_pipeline.src.vectorbt_adapter import _default_data_loader

    return _default_data_loader(unit.event_id, Path(context.repo_root), symbol=unit.symbol)


def _resolve_fs_v1_signal_computer(
    unit: PaidScreenUnit,
    context: WorkerContext,
    fs_ctx=None,
) -> Callable | None:
    """Return fs_v1 PIT signal computer when feature-store rows exist for *unit*."""
    if fs_ctx is None:
        fs_ctx = _try_resolve_fs_v1_context(unit, context)
    if fs_ctx is None:
        return None
    from backtest_pipeline.src.fs_v1_screen_path import build_fs_v1_signal_computer

    return build_fs_v1_signal_computer(fs_ctx)


def _build_candidate_model(unit: PaidScreenUnit, model_entry: dict, repo_root: Path, parsed: "ParsedHypothesis") -> "CandidateModel":
    """Build a CandidateModel for the given unit and model registry entry."""
    from research_pipeline.types import CandidateModel
    from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate

    candidate_id = f"{model_entry['model_id']}|{unit.symbol}|{unit.event_id}|{model_entry.get('hyp_id', 0)}"
    target_symbol = unit.symbol.split(".")[0] if unit.symbol else "MES"
    base = CandidateModel(
        candidate_id=candidate_id,
        model_id=model_entry["model_id"],
        strategy_params={},
        thesis=unit.thesis or f"{model_entry['model_id']} on {unit.symbol} event {unit.event_id}",
        metadata={
            "unit_id": unit.unit_id,
            "symbol": unit.symbol,
            "event_id": unit.event_id,
            "event_type": unit.event_type,
            "hyp_id": model_entry.get("hyp_id"),
            "feature_set_id": unit.feature_set_id,
            "research_clock": unit.research_clock,
            "context_set_id": unit.context_set_id,
            "allowed_context_set_id": unit.context_set_id,
            "declared_context_sets": list(unit.declared_context_sets),
            "ablation_group_id": unit.ablation_group_id,
            "negative_control_policy": unit.negative_control_policy,
        },
        target_symbol=target_symbol,
        target_event_id=unit.event_id,
        research_clock=unit.research_clock,
    )
    return attach_feature_recipe_to_candidate(
        base,
        parsed=parsed,
        target_event_id=unit.event_id,
        target_symbol=target_symbol,
        research_clock=unit.research_clock,
    )


def build_structured_parsed_hypothesis(
    unit: PaidScreenUnit,
    model_entry: dict,
) -> "ParsedHypothesis":
    """Build execution ``ParsedHypothesis`` from structured unit/registry fields.

    Thesis text is display-only; semantics come from ``model_id``, symbol, and
    registry metadata — not NL parsing.
    """
    from research_pipeline.types import ParsedHypothesis

    model_id = str(model_entry["model_id"])
    root_symbol = unit.symbol.split(".")[0] if unit.symbol else "MES"
    display_thesis = (
        unit.thesis
        or f"{model_id} on {unit.symbol} event {unit.event_id}"
    )
    return ParsedHypothesis(
        thesis=display_thesis,
        instrument_universe=[root_symbol],
        entry_rules=[f"Enter when {model_id} signal exceeds threshold"],
        exit_rules=["Exit on signal mean reversion or session end"],
        indicators=["microstructure_signal"],
        feature_list=[model_id],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id=model_id,
        source="structured_paid_screen",
    )


def resolve_git_commit(repo_root: str) -> str:
    """Resolve current git HEAD without spawning a subprocess per batch."""
    try:
        git_dir = Path(repo_root) / ".git"
        head_text = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head_text.startswith("ref: "):
            ref_rel = head_text[5:].strip()
            ref_file = git_dir / ref_rel
            if ref_file.is_file():
                commit = ref_file.read_text(encoding="utf-8").strip()
                return commit or "unknown"
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1].strip() == ref_rel:
                        return parts[0].strip() or "unknown"
            return "unknown"
        if len(head_text) >= 7:
            return head_text
    except Exception:
        pass
    return "unknown"


def resolve_resume_provenance(
    repo_root: str,
    unit: PaidScreenUnit,
    *,
    git_commit: str | None = None,
) -> dict[str, str]:
    """Current code/registry/signal/feature provenance for resume matching."""
    commit = git_commit or resolve_git_commit(repo_root)
    ctx = WorkerContext(
        repo_root=repo_root,
        git_commit=commit,
        screening_scope="",
        vectorbt_engine="",
        vectorbt_version="",
        rust_runtime_proof=False,
        events_csv_hash="",
        lake_manifest_hash="",
    )
    _, fs_hash, sig_hash, reg_hash = resolve_batching_hashes(unit, ctx)
    model_entry = {"model_id": unit.model_id, "hyp_id": unit.hyp_id}
    parsed = build_structured_parsed_hypothesis(unit, model_entry)
    candidate = _build_candidate_model(
        unit, model_entry, Path(repo_root), parsed
    )
    recipe_hash = str(getattr(candidate, "feature_recipe_hash", "") or "")
    return {
        "code_commit": commit,
        "model_registry_hash": reg_hash,
        "signal_implementation_hash": sig_hash,
        "feature_set_hash": fs_hash,
        "feature_recipe_hash": recipe_hash,
    }


def _write_screening_artifact(
    artifact_path: str,
    filter_result: "FilterResult",
    unit: PaidScreenUnit,
    model_entry: dict,
    context: WorkerContext,
    ohlcv_hash: str,
    profiler: RunProfiler,
    candidate: "CandidateModel | None" = None,
) -> str:
    """Write a screening artifact for the unit.

    Uses ``FilterResult.to_dict()`` as the canonical artifact builder.
    That method already:
      - includes all ``SCREENING_ARTIFACT_REQUIRED_FIELDS``
      - calls ``compute_screening_artifact_hash`` (correct exclusions)
      - calls ``validate_screening_artifact`` (fail-closed)
    We only need to stamp the git commit and data manifest hash, then
    write atomically.
    """
    from datetime import datetime, timezone

    from backtest_pipeline.src.fs_v1_screen_path import FS_V1_BAR_CONSTRUCTION_ID

    _, fs_hash, sig_hash, reg_hash = resolve_batching_hashes(unit, context)

    # Stamp provenance fields that the matrix function may not have set.
    filter_result.code_commit = context.git_commit or resolve_git_commit(context.repo_root)
    fs_v1_active = filter_result.bar_construction_id == FS_V1_BAR_CONSTRUCTION_ID
    if not fs_v1_active:
        filter_result.feature_set_hash = fs_hash
    if unit.feature_set_id and not fs_v1_active:
        filter_result.feature_set_id = unit.feature_set_id

    # Override provenance fields from the worker run context (BLUEPRINT §8).
    if not fs_v1_active:
        filter_result.data_manifest_hash = ohlcv_hash
    filter_result.lake_manifest_hash = context.lake_manifest_hash
    filter_result.events_csv_hash_or_not_applicable = context.events_csv_hash
    filter_result.research_clock = unit.research_clock
    filter_result.target_event_type_or_null = unit.event_type or None
    filter_result.allowed_context_set_id_or_null = unit.context_set_id
    filter_result.declared_context_sets = list(unit.declared_context_sets)

    # Build the canonical artifact dict — this calls validate_screening_artifact
    # internally and computes the correct screening_artifact_hash.
    artifact = filter_result.to_dict()

    split_label = (unit.research_split or DEFAULT_RESEARCH_SPLIT).strip()
    artifact["research_split"] = split_label

    # Paid-screen resume provenance (extensions beyond required artifact fields).
    artifact["model_registry_hash"] = reg_hash
    artifact["signal_implementation_hash"] = sig_hash
    recipe_hash = str(getattr(candidate, "feature_recipe_hash", "") or "")
    if recipe_hash:
        artifact["feature_recipe_hash"] = recipe_hash
    from backtest_pipeline.src.vectorbt_adapter import (
        _json_primitive_screening_payload,
        compute_screening_artifact_hash,
        validate_screening_artifact,
    )

    serializable = _json_primitive_screening_payload(artifact)
    serializable["screening_artifact_hash"] = compute_screening_artifact_hash(serializable)
    validate_screening_artifact(serializable)

    # Write atomically. The tmp name must be per-write so concurrent/retried
    # writers never race on the same intermediate path.
    tmp_path = f"{artifact_path}.tmp.{os.getpid()}.{time.monotonic_ns()}"
    os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, sort_keys=True)
        os.replace(tmp_path, artifact_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise

    from backtest_pipeline.src.research_pipeline_stages import annotate_promoted_screening_handoffs

    annotate_promoted_screening_handoffs(serializable, artifact_path=artifact_path)

    return str(serializable.get("screening_artifact_hash") or "")


def screen_paid_batch(
    units: list[PaidScreenUnit],
    context: WorkerContext,
    profiler: RunProfiler | None = None,
    data_cache: dict | BoundedLRUCache | None = None,
    *,
    run_screening: bool = True,
    max_trials: int | None = None,
    scratch_root: str | os.PathLike[str] | None = None,
) -> list[UnitScreeningResult]:
    """Screen a batch of compatible units using chunked VectorBT matrix execution.

    Units in the batch should share the same (symbol, event_id) for data reuse.
    This function:
    1. Loads NPZ data once for the shared event_id (via data_cache)
    2. Resolves each model from the registry (not from thesis text)
    3. Runs the parameter matrix screening via run_vectorbt_simulation_matrix
    4. Writes per-unit validated screening artifacts
    5. Returns one UnitScreeningResult per input unit

    The thesis field is preserved as descriptive metadata but does NOT
    determine which model or symbol is executed.

    ``data_cache`` may be either a plain ``dict`` (legacy callers) or a
    :class:`~backtest_pipeline.src.paid_screen_cache.BoundedLRUCache` (the
    Phase 4 bounded cache). When a BoundedLRUCache is supplied, its
    observable ``hit_count`` / ``miss_count`` counters are reconciled with
    the profiler after the batch completes.

    Args:
        units: Units to screen
        context: Worker context with repo paths and configuration
        profiler: Optional profiler for timing/failure tracking
        data_cache: Cache for NPZ/OHLCV data (dict or BoundedLRUCache)
        run_screening: If False, skip expensive matrix screening and return
            SKIPPED after model resolution. Useful for cache wiring tests.
        max_trials: Optional limit on parameter trials per candidate.
        scratch_root: Optional directory for worker scratch artifacts. When
            omitted, defaults to ``runtime/paid_screen_scratch`` under repo_root.
    """
    if profiler is None:
        profiler = RunProfiler()
    if data_cache is None:
        data_cache = {}

    use_lru = _is_bounded_lru_cache(data_cache)
    # Snapshot cumulative cache counters before the batch so we can fold
    # only the *delta* into the profiler. The BoundedLRUCache maintains
    # cumulative hit/miss counters across its lifetime; the profiler is
    # likewise cumulative across batches. Using the delta avoids
    # double-counting when screen_paid_batch is called multiple times
    # with the same cache + profiler (e.g. from PaidScreenWorker).
    pre_hits = data_cache.hit_count if use_lru else 0
    pre_misses = data_cache.miss_count if use_lru else 0

    results: list[UnitScreeningResult] = []

    if not units:
        return results

    _assert_batch_key_compatible(units, context)

    # Load OHLCV once for the shared (symbol, event_id) batching key.
    profiler.start_stage("npz_discovery")
    representative = units[0]
    cache_key = ohlcv_data_cache_key(representative, context)
    ohlcv = _cache_get(data_cache, cache_key)
    ohlcv_from_cache = ohlcv is not None
    if ohlcv is None:
        try:
            ohlcv = _load_ohlcv_for_unit(representative, context)
            if ohlcv is not None:
                _cache_put(data_cache, cache_key, ohlcv)
                if not use_lru:
                    profiler.cache_misses += 1
            else:
                if not use_lru:
                    profiler.cache_misses += 1
        except Exception as e:
            profiler.record_failure(
                "npz_load", e,
                f"batch_{representative.symbol}_{representative.event_id}",
                cache_state={"hit": False})
            ohlcv = None
    else:
        if not use_lru:
            profiler.cache_hits += 1
    profiler.end_stage("npz_discovery")

    # When using a BoundedLRUCache, its get()/put() already maintain the
    # authoritative hit_count/miss_count. Fold the *delta* (this batch's
    # contribution) into the profiler so the profiler reflects the cache's
    # view without double-counting across multiple batches.
    if use_lru:
        profiler.cache_hits += data_cache.hit_count - pre_hits
        profiler.cache_misses += data_cache.miss_count - pre_misses

    if ohlcv is None:
        # All units fail — no data
        for unit in units:
            profiler.record_failure(
                "npz_load", RuntimeError("no_ohlcv_data"),
                unit.unit_id, cache_state={"hit": False})
            results.append(UnitScreeningResult(
                unit_id=unit.unit_id,
                status="ERROR",
                error="no_ohlcv_data",
                error_category="data_quality",
            ))
        return results

    if run_screening and _is_paid_scope(context.screening_scope):
        ohlcv_rows = _ohlcv_row_count(ohlcv)
        if ohlcv_rows < 2:
            error = f"insufficient_ohlcv_bars_for_paid_screen:min=2 got={ohlcv_rows}"
            for unit in units:
                profiler.record_failure(
                    "npz_load",
                    RuntimeError(error),
                    unit.unit_id,
                    cache_state={"hit": ohlcv_from_cache},
                )
                results.append(UnitScreeningResult(
                    unit_id=unit.unit_id,
                    status="ERROR",
                    error=error,
                    error_category="data_quality",
                ))
            return results

    # Cache-wiring / disabled-screening path: resolve models per unit group,
    # return SKIPPED for successes and ERROR for per-unit resolution failures.
    if _should_skip_matrix_screening(run_screening, ohlcv_from_cache, data_cache):
        return _resolve_models_without_screening(units, context, profiler)

    # v1 screening auto-selects fs_v1 signal computer; v2 matrix path must too.
    fs_v1_ctx = _get_or_load_fs_v1_context(
        representative, context, data_cache, profiler
    )
    if run_screening and _is_paid_scope(context.screening_scope):
        if fs_v1_ctx is None:
            return _paid_scope_fs_v1_gate_error(
                units=units,
                context=context,
                reason="paid_scope_requires_fs_v1_context",
                profiler=profiler,
                ohlcv_cache_state=ohlcv_from_cache,
            )
        if not _ohlcv_aligns_with_fs_v1_store(ohlcv, fs_v1_ctx):
            return _paid_scope_fs_v1_gate_error(
                units=units,
                context=context,
                reason=(
                    "paid_scope_fs_v1_ohlcv_misaligned:"
                    f"ohlcv_rows={_ohlcv_row_count(ohlcv)} "
                    f"store_rows={len(fs_v1_ctx.store.get('ts', []))}"
                ),
                profiler=profiler,
                ohlcv_cache_state=ohlcv_from_cache,
            )
    signal_computer = None
    if fs_v1_ctx is not None and _ohlcv_aligns_with_fs_v1_store(ohlcv, fs_v1_ctx):
        signal_computer = _resolve_fs_v1_signal_computer(
            representative, context, fs_ctx=fs_v1_ctx
        )
        profiler.performance.record_models_for_last_load(
            len({u.model_id for u in units})
        )
    else:
        fs_v1_ctx = None
    # Compute OHLCV hash for artifact provenance
    ohlcv_hash = hashlib.sha256(ohlcv.tobytes()).hexdigest()[:32]

    # Group units by model for efficient screening (each model runs its own matrix)
    units_by_model: dict[str, list[PaidScreenUnit]] = {}
    for unit in units:
        units_by_model.setdefault(unit.model_id, []).append(unit)

    # Process each model group
    for model_id, model_units in units_by_model.items():
        profiler.start_stage(f"model_{model_id}")

        try:
            # Resolve model from registry once per model group
            model_entry = resolve_model_from_registry(model_id, context.repo_root)
            representative_unit = model_units[0]

            # Structured execution recipe — thesis is display-only metadata.
            parsed = build_structured_parsed_hypothesis(representative_unit, model_entry)

            # Build candidate with feature_recipe_hash for HBT handoff parity.
            candidate = _build_candidate_model(
                representative_unit, model_entry, Path(context.repo_root), parsed
            )

            # Run the matrix screening with worker run budget forwarded.
            from backtest_pipeline.src.vectorbt_adapter import DEFAULT_PARAM_GRID, _build_run_budget
            budget = _build_run_budget(
                candidates=[candidate],
                grid=DEFAULT_PARAM_GRID,
                screening_scope=context.screening_scope,
                max_total_trials=max_trials,
                run_budget=context.run_budget or None,
            )
            from backtest_pipeline.src.paid_screen_matrix import DEFAULT_MATRIX_CHUNK_SIZE
            chunk_size = DEFAULT_MATRIX_CHUNK_SIZE
            profiler.performance.matrix_chunk_size = chunk_size
            filter_result = run_vectorbt_simulation_matrix(
                ohlcv=ohlcv,
                candidates=[candidate],
                parsed=parsed,
                grid=DEFAULT_PARAM_GRID,
                repo_root=Path(context.repo_root),
                signal_computer=signal_computer,
                screening_scope=context.screening_scope,
                chunk_size=chunk_size,
                max_total_trials=max_trials,
                run_budget=budget,
                performance_counters=profiler.performance,
                fs_v1_ctx=fs_v1_ctx,
            )
            filter_result = apply_promotion_gates(
                filter_result,
                screening_scope=context.screening_scope,
                repo_root=Path(context.repo_root),
            )
            apply_filter_result_provenance_metadata(
                filter_result,
                [candidate],
                screening_scope=context.screening_scope,
                repo_root=Path(context.repo_root),
            )
            if fs_v1_ctx is not None:
                from backtest_pipeline.src.vectorbt_adapter import (
                    _apply_fs_v1_screen_metadata,
                    _resolve_research_clock,
                )

                _apply_fs_v1_screen_metadata(
                    filter_result,
                    fs_v1_ctx,
                    [candidate],
                    research_clock=_resolve_research_clock([candidate]),
                    screening_scope=context.screening_scope,
                    repo_root=Path(context.repo_root),
                )

            # Write per-unit artifacts and collect results
            for unit in model_units:
                artifact_dir = _worker_scratch_artifact_dir(
                    context.repo_root, unit.unit_id, scratch_root=scratch_root
                )
                os.makedirs(artifact_dir, exist_ok=True)
                artifact_path = os.path.join(artifact_dir, "screening_artifact.json")

                # Write the artifact
                try:
                    artifact_hash = _write_screening_artifact(
                        artifact_path, filter_result,
                        unit, model_entry, context, ohlcv_hash, profiler,
                        candidate=candidate,
                    )
                except ScreeningArtifactError as exc:
                    results.append(UnitScreeningResult(
                        unit_id=unit.unit_id,
                        status="ERROR",
                        error=str(exc),
                    ))
                    continue

                result = UnitScreeningResult(
                    unit_id=unit.unit_id,
                    status="OK",
                    screening_artifact_path=artifact_path,
                    screening_artifact_hash=artifact_hash,
                    elapsed_seconds=0.0,
                    promoted_ids=[p.candidate_id for p in filter_result.promoted],
                    rejected_ids=[r.candidate_id for r in filter_result.rejected],
                )
                results.append(result)

            # End the model-level stage once after all units in the group
            profiler.end_stage(f"model_{model_id}")

        except Exception as e:
            profiler.record_failure(
                f"model_{model_id}", e, f"union_{model_id}",
                cache_state={"hit": False})
            elapsed = profiler.end_stage(f"model_{model_id}")
            for unit in model_units:
                results.append(UnitScreeningResult(
                    unit_id=unit.unit_id,
                    status="ERROR",
                    error=str(e),
                    elapsed_seconds=elapsed,
                ))

    return results
