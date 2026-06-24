#!/usr/bin/env python3
"""End-to-end autoresearch pipeline CLI."""

from __future__ import annotations

import argparse
import copy
import contextvars
import hashlib
import json
import logging
import math
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages"), str(REPO / "apps")]

from research_pipeline.deployment import deploy_best
from research_pipeline.document_ingestion import (
    build_knowledge_graph,
    extract_text,
    graph_to_kg_records,
    summarise_text,
)
from research_pipeline.evaluation import evaluate_model
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.knowledge_graph import persist_graph_slice
from research_pipeline.model_generation import generate_candidates
from research_pipeline.parameter_search import SUPPORTED_SEARCH_METHODS
from research_pipeline.idea_generation import (
    candidates_from_ideas,
    generate_idea_set,
    idea_summary as summarize_ideas,
    mark_queued_ideas_without_candidates_failed,
    parsed_from_idea,
    update_idea_statuses_from_results,
)
from backtest_pipeline.src.fs_v1_screen_path import FS_V1_BAR_CONSTRUCTION_ID
from backtest_pipeline.src.vectorbt_adapter import filter_candidates, persist_screening_artifact
from data_system.src.feature_store import feature_store_root
from backtest_pipeline.src.hftbacktest_realism import (
    compute_robustness_evidence_receipt_hash,
    validate_applied_robustness_evidence_receipt,
    validate_candidate_replay_eligibility,
    write_hftbacktest_realism_artifacts,
)
from backtest_pipeline.src.promotion_gate import PromotionGate
from research_pipeline.packets import (
    build_pipeline_request,
    build_pipeline_response,
    write_pipeline_packets,
)
from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds, PipelineReport, ParsedHypothesis
from data_layer.llm.openai_compatible_client import DEFAULT_MODEL_DEVELOPMENT_MODEL


def _run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pipeline_{ts}_{uuid.uuid4().hex[:8]}"


_DEFAULT_PIPELINE_CONFIG_PATH = REPO / "config" / "research_pipeline" / "default_runtime.json"
_DEFAULT_PIPELINE_RUNTIME_CONFIG: dict[str, Any] = {
    "schema_version": "hft3_research_pipeline_runtime_v1",
    "max_candidates": 5,
    "vectorbt": {
        "scope": "pilot",
        "budget": {
            "max_trials": None,
            "max_models": None,
            "max_symbols": None,
            "max_feature_sets": None,
            "max_total_trials": None,
            "max_wall_clock_seconds": None,
            "max_peak_memory_mb_or_null": None,
            "abort_on_budget_exhaustion": None,
        },
    },
    "llm_ideas": {
        "max_ideas": None,
        "review_memory_limit": 5,
        "temperature": 0.7,
        "top_p": 0.95,
    },
    "doc_cache": {
        "enabled": True,
        "root": "runtime/research_pipeline/doc_cache",
        "cache_urls": False,
    },
    "candidate_prefilter": {
        "enabled": True,
        "model_id_pattern": r"^[A-Z][A-Z0-9_]*$",
        "signal_threshold_min": 0.0,
        "signal_threshold_max": 1.0,
        "require_positive_holding_period_bars": True,
    },
    "candidate_search": {
        "method": "grid",
        "seed": 42,
    },
}
_VECTORBT_SCOPE_CHOICES = {
    "pilot",
    "screen",
    "refine",
    "paid",
    "paid-compute",
    "paid_compute",
    "broad",
    "broad-screen",
    "broad_screen",
    "all-model",
    "all_model",
    "all-models",
    "all_models",
}
_VECTORBT_BUDGET_ARGS = {
    "max_trials": "vectorbt_max_trials",
    "max_models": "vectorbt_max_models",
    "max_symbols": "vectorbt_max_symbols",
    "max_feature_sets": "vectorbt_max_feature_sets",
    "max_total_trials": "vectorbt_max_total_trials",
    "max_wall_clock_seconds": "vectorbt_max_wall_clock_seconds",
    "max_peak_memory_mb_or_null": "vectorbt_max_peak_memory_mb",
}
_PIPELINE_RESULT_MARKER = "HFT3_PIPELINE_RESULT="
_LOG_HANDLER_ATTR = "_hft3_pipeline_run_handler"
logger = logging.getLogger("hft3.research_pipeline.run_pipeline")
_LOG_HANDLER_LOCK = threading.Lock()
_ACTIVE_LOG_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hft3_pipeline_run_id",
    default=None,
)


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return path


def _deep_merge(base: dict[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def load_pipeline_runtime_config(path: Path | None = None) -> dict[str, Any]:
    config = copy.deepcopy(_DEFAULT_PIPELINE_RUNTIME_CONFIG)
    config_path = path or _DEFAULT_PIPELINE_CONFIG_PATH
    if config_path.is_file():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"pipeline runtime config must be a JSON object: {config_path}")
        _deep_merge(config, raw)
    elif path is not None:
        raise FileNotFoundError(f"pipeline runtime config not found: {config_path}")
    return config


def _section(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: Any, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _nonnegative_int(value: Any, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a nonnegative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return parsed


def _optional_positive_int(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, name=name)


def _float_default(value: Any, *, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _bool_default(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be boolean")


def _required_true(value: Any, *, name: str) -> bool:
    parsed = _bool_default(value, name=name)
    if not parsed:
        raise ValueError(f"{name} must be true")
    return parsed


def _apply_pipeline_runtime_defaults(args: argparse.Namespace, config: Mapping[str, Any]) -> None:
    vectorbt_config = _section(config, "vectorbt")
    idea_config = _section(config, "llm_ideas")
    search_config = _section(config, "candidate_search")

    if args.max_candidates is None:
        args.max_candidates = config.get("max_candidates", 5)
    args.max_candidates = _positive_int(args.max_candidates, name="max_candidates")

    if args.vectorbt_scope is None:
        args.vectorbt_scope = str(vectorbt_config.get("scope") or "pilot")
    if args.vectorbt_scope not in _VECTORBT_SCOPE_CHOICES:
        raise ValueError(f"vectorbt.scope must be one of: {', '.join(sorted(_VECTORBT_SCOPE_CHOICES))}")

    budget_config = _section(vectorbt_config, "budget")
    if budget_config.get("abort_on_budget_exhaustion") is not None:
        _required_true(
            budget_config["abort_on_budget_exhaustion"],
            name="vectorbt.budget.abort_on_budget_exhaustion",
        )
    for config_key, arg_name in _VECTORBT_BUDGET_ARGS.items():
        if getattr(args, arg_name) is None and budget_config.get(config_key) is not None:
            setattr(args, arg_name, int(budget_config[config_key]))

    if args.max_ideas is None:
        args.max_ideas = _optional_positive_int(idea_config.get("max_ideas"), name="llm_ideas.max_ideas")
    if args.review_memory_limit is None:
        args.review_memory_limit = idea_config.get("review_memory_limit", 5)
    args.review_memory_limit = _nonnegative_int(args.review_memory_limit, name="review_memory_limit")

    if getattr(args, "candidate_search_method", None) is None:
        args.candidate_search_method = str(search_config.get("method") or "grid")
    args.candidate_search_method = str(args.candidate_search_method).strip().lower().replace("-", "_")
    if args.candidate_search_method not in SUPPORTED_SEARCH_METHODS:
        raise ValueError(
            "candidate_search.method must be one of: "
            + ", ".join(sorted(SUPPORTED_SEARCH_METHODS))
        )
    if getattr(args, "candidate_search_seed", None) is None:
        args.candidate_search_seed = search_config.get("seed", 42)
    args.candidate_search_seed = _nonnegative_int(args.candidate_search_seed, name="candidate_search.seed")


class _PipelineJsonFormatter(logging.Formatter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "run_id": self.run_id,
            "event": record.getMessage(),
        }
        extra_payload = getattr(record, "payload", None)
        if isinstance(extra_payload, Mapping):
            payload["payload"] = dict(extra_payload)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=_json_default)


class _RunLogFilter(logging.Filter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        return _ACTIVE_LOG_RUN_ID.get() == self.run_id


def _configure_run_logging(
    artifact_dir: Path,
    run_id: str,
) -> tuple[Path, logging.FileHandler, contextvars.Token[str | None]]:
    log_path = artifact_dir / "pipeline_run.log"
    token = _ACTIVE_LOG_RUN_ID.set(run_id)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    setattr(handler, _LOG_HANDLER_ATTR, True)
    handler.addFilter(_RunLogFilter(run_id))
    handler.setFormatter(_PipelineJsonFormatter(run_id))
    with _LOG_HANDLER_LOCK:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    logger.info("pipeline_run_logging_configured", extra={"payload": {"log_path": str(log_path)}})
    return log_path, handler, token


def _close_run_logging(
    handler: logging.Handler,
    token: contextvars.Token[str | None],
) -> None:
    with _LOG_HANDLER_LOCK:
        if handler in logger.handlers:
            logger.removeHandler(handler)
    handler.close()
    _ACTIVE_LOG_RUN_ID.reset(token)


def _pipeline_config_receipt(
    *,
    config: Mapping[str, Any],
    config_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    idea_config = _section(config, "llm_ideas")
    vectorbt_budget = {
        key: getattr(args, arg_name)
        for key, arg_name in _VECTORBT_BUDGET_ARGS.items()
    }
    abort_default = _section(_section(config, "vectorbt"), "budget").get("abort_on_budget_exhaustion")
    if abort_default is not None:
        vectorbt_budget["abort_on_budget_exhaustion"] = _required_true(
            abort_default,
            name="vectorbt.budget.abort_on_budget_exhaustion",
        )
    effective = {
        "max_candidates": args.max_candidates,
        "vectorbt": {
            "scope": args.vectorbt_scope,
            "budget": vectorbt_budget,
        },
        "llm_ideas": {
            "max_ideas": args.max_ideas,
            "review_memory_limit": args.review_memory_limit,
            "temperature": getattr(args, "resolved_idea_temperature", idea_config.get("temperature", 0.7)),
            "top_p": getattr(args, "resolved_idea_top_p", idea_config.get("top_p", 0.95)),
        },
        "doc_cache": _section(config, "doc_cache"),
        "candidate_prefilter": _section(config, "candidate_prefilter"),
        "candidate_search": {
            "method": getattr(args, "candidate_search_method", "grid"),
            "seed": getattr(args, "candidate_search_seed", 42),
        },
    }
    hash_payload = {
        "loaded_config": copy.deepcopy(dict(config)),
        "effective": effective,
    }
    config_hash = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, default=_json_default).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "hft3_pipeline_runtime_config_receipt_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_config_path": str(config_path),
        "pipeline_runtime_config_hash": config_hash,
        "loaded_config": copy.deepcopy(dict(config)),
        "effective": effective,
    }


def _write_pipeline_run_receipt(payload: Mapping[str, Any]) -> Path | None:
    artifact_dir = payload.get("artifact_dir")
    if not artifact_dir:
        return None
    receipt_path = _write_json(Path(str(artifact_dir)) / "pipeline_run_receipt.json", payload)
    logger.info(
        "pipeline_run_receipt_written",
        extra={"payload": {"receipt_path": str(receipt_path), "status": payload.get("status")}},
    )
    return receipt_path


def _set_active_run_failure_context(context: dict[str, Any], **values: Any) -> None:
    context.clear()
    context.update(values)


def _update_active_run_failure_context(context: dict[str, Any], **values: Any) -> None:
    context.update(values)


def _emit_pipeline_failure_receipt(exc: Exception, context: Mapping[str, Any]) -> None:
    context = dict(context)
    artifact_dir = context.get("artifact_dir")
    if not artifact_dir:
        logger.exception("pipeline_run_failed_without_artifact_dir")
        return
    payload: dict[str, Any] = {
        "run_id": context.get("run_id"),
        "artifact_dir": str(artifact_dir),
        "status": "pipeline_failed",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
    }
    for key in (
        "request_packet",
        "document_summary",
        "document_cache",
        "candidate_prefilter",
        "paths",
    ):
        if key in context:
            payload[key] = context[key]
    logger.exception("pipeline_run_failed", extra={"payload": payload["error"]})
    _emit_pipeline_payload(payload, orchestrator_result=bool(context.get("orchestrator_result")))


def _emit_pipeline_payload(payload: dict, *, orchestrator_result: bool) -> None:
    _write_pipeline_run_receipt(payload)
    if orchestrator_result:
        slim = {
            "run_id": payload.get("run_id"),
            "artifact_dir": payload.get("artifact_dir"),
            "status": payload.get("status"),
            "paths": payload.get("paths"),
        }
        print(_PIPELINE_RESULT_MARKER + json.dumps(slim, default=_json_default))
    else:
        print(json.dumps(payload, indent=2, default=_json_default))


def _pipeline_llm_status(parsed: ParsedHypothesis, *, no_llm: bool) -> str:
    if no_llm:
        return "skipped_no_llm"
    if parsed.llm_status:
        return parsed.llm_status
    return "ok" if parsed.source == "openai_compatible" else "unavailable"


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _deployment_allowed(idea_set_enabled: bool, results: list[EvaluationResult]) -> bool:
    return (not idea_set_enabled) or any(r.passes_all_gates() for r in results)


def _idea_set_missing_prefilter(
    *,
    idea_set_enabled: bool,
    dry_run: bool,
    vectorbt: bool,
    vectorbt_only: bool,
) -> bool:
    return idea_set_enabled and not dry_run and not (vectorbt or vectorbt_only)


def _missing_hftbacktest_realism_inputs(args: argparse.Namespace) -> list[str]:
    required = {
        "hftbacktest_data_npz": "--hftbacktest-data-npz",
        "hftbacktest_latency_model": "--hftbacktest-latency-model",
        "hftbacktest_fill_queue_model": "--hftbacktest-fill-queue-model",
        "hftbacktest_upstream_ref": "--hftbacktest-upstream-ref",
    }
    missing = [flag for attr, flag in required.items() if getattr(args, attr) is None]
    if not args.native_hot_path_evidence:
        missing.append("--native-hot-path-evidence")
    return missing


def _optional_resolved_path(path: Path | None) -> Path | None:
    return path.resolve() if path is not None else None


def _resolve_config_path(repo_root: Path, raw_path: Any) -> Path:
    path = Path(str(raw_path))
    if path.is_absolute():
        return path
    return repo_root / path


def _is_url_source(source: str | Path) -> bool:
    return str(source).startswith(("http://", "https://"))


def _resolve_doc_file(source: str | Path, *, repo_root: Path) -> Path:
    source_path = Path(str(source))
    return (repo_root / source_path).resolve() if not source_path.is_absolute() else source_path.resolve()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _doc_cache_path(source: str | Path, *, repo_root: Path, cache_root: Path) -> Path:
    if _is_url_source(source):
        source_meta: dict[str, Any] = {"kind": "url", "source": str(source)}
    else:
        resolved = _resolve_doc_file(source, repo_root=repo_root)
        source_meta = {"kind": "file", "source": str(resolved)}
    digest = hashlib.sha256(
        json.dumps(source_meta, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return cache_root / f"{digest}.json"


def _doc_file_metadata(path: Path, *, sha256: str | None = None) -> dict[str, Any]:
    stat = path.stat()
    metadata: dict[str, Any] = {
        "kind": "file",
        "source": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if sha256 is not None:
        metadata["sha256"] = sha256
    return metadata


def _cached_doc_match_metadata(
    cached: Mapping[str, Any],
    source: str | Path,
    *,
    repo_root: Path,
) -> tuple[bool, dict[str, Any] | None]:
    if _is_url_source(source):
        return True, None
    resolved = _resolve_doc_file(source, repo_root=repo_root)
    source_file = cached.get("source_file")
    if not isinstance(source_file, Mapping):
        return False, None
    if str(source_file.get("source") or "") != str(resolved):
        return False, None
    stat = resolved.stat()
    if source_file.get("size") == stat.st_size and source_file.get("mtime_ns") == stat.st_mtime_ns:
        return True, None
    cached_sha = source_file.get("sha256")
    if not cached_sha:
        return False, None
    current_sha = _file_sha256(resolved)
    if str(cached_sha) != current_sha:
        return False, None
    return True, _doc_file_metadata(resolved, sha256=current_sha)


def _doc_id(
    source: str | Path,
    *,
    repo_root: Path,
    resolved_source: Path | None = None,
) -> str:
    if _is_url_source(source):
        identity = str(source)
        stem = Path(str(source).rstrip("/")).stem
    else:
        resolved = resolved_source or _resolve_doc_file(source, repo_root=repo_root)
        identity = str(resolved)
        stem = resolved.stem
    stem = stem or hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "unknown"
    path_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"doc:{safe_stem}_{path_hash}"


def _graph_from_kg_records(records: Mapping[str, Any]):
    import networkx as nx

    graph = nx.DiGraph()
    for node in records.get("nodes", []) or []:
        if not isinstance(node, Mapping) or not node.get("id"):
            continue
        attrs = {k: v for k, v in node.items() if k != "id"}
        graph.add_node(str(node["id"]), **attrs)
    for edge in records.get("edges", []) or []:
        if not isinstance(edge, Mapping) or not edge.get("from") or not edge.get("to"):
            continue
        attrs = {k: v for k, v in edge.items() if k not in {"from", "to"}}
        graph.add_edge(str(edge["from"]), str(edge["to"]), **attrs)
    return graph


def ingest_document_with_cache(
    source: str | Path,
    *,
    repo_root: Path,
    cache_config: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    enabled = _bool_default(cache_config.get("enabled", True), name="doc_cache.enabled")
    cache_urls = _bool_default(cache_config.get("cache_urls", False), name="doc_cache.cache_urls")
    if _is_url_source(source) and not cache_urls:
        enabled = False
    cache_root = _resolve_config_path(
        repo_root,
        cache_config.get("root") or _DEFAULT_PIPELINE_RUNTIME_CONFIG["doc_cache"]["root"],
    )
    resolved_source = None if _is_url_source(source) else _resolve_doc_file(source, repo_root=repo_root)
    cache_path = _doc_cache_path(source, repo_root=repo_root, cache_root=cache_root)
    doc_id = _doc_id(source, repo_root=repo_root, resolved_source=resolved_source)
    if enabled and cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        cache_matches, refreshed_source_file = _cached_doc_match_metadata(cached, source, repo_root=repo_root)
        if cache_matches and str(cached.get("doc_id") or "") == doc_id:
            if refreshed_source_file is not None:
                cached["source_file"] = refreshed_source_file
                _write_json(cache_path, cached)
            summary = str(cached.get("summary") or "")
            records = cached.get("kg_records") or {}
            persist_graph_slice(repo_root, _graph_from_kg_records(records))
            return summary, {
                "enabled": enabled,
                "status": "hit",
                "cache_path": str(cache_path),
                "doc_id": str(cached.get("doc_id") or doc_id),
            }

    text = extract_text(source)
    summary = summarise_text(text)
    kg = build_knowledge_graph(text, doc_id=doc_id)
    records = graph_to_kg_records(kg)
    persist_graph_slice(repo_root, kg)
    if enabled:
        _write_json(
            cache_path,
            {
                "schema_version": "hft3_doc_ingestion_cache_v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "doc_id": doc_id,
                "text_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
                "text_char_count": len(text),
                "source_file": (
                    _doc_file_metadata(
                        resolved_source,
                        sha256=_file_sha256(resolved_source),
                    )
                    if resolved_source is not None
                    else None
                ),
                "summary": summary,
                "kg_records": records,
            },
        )
    return summary, {
        "enabled": enabled,
        "status": "miss",
        "cache_path": str(cache_path) if enabled else None,
        "doc_id": doc_id,
    }


def prefilter_candidates(
    candidates: list[CandidateModel],
    *,
    config: Mapping[str, Any],
) -> tuple[list[CandidateModel], dict[str, Any]]:
    enabled = _bool_default(config.get("enabled", True), name="candidate_prefilter.enabled")
    if not enabled:
        return candidates, {
            "schema_version": "hft3_candidate_prefilter_v1",
            "enabled": False,
            "total_candidates": len(candidates),
            "accepted_count": len(candidates),
            "rejected_count": 0,
            "accepted_ids": [c.candidate_id for c in candidates],
            "rejected": [],
        }

    pattern = re.compile(str(config.get("model_id_pattern") or r"^[A-Z][A-Z0-9_]*$"))
    threshold_min = _float_default(config.get("signal_threshold_min", 0.0), name="signal_threshold_min")
    threshold_max = _float_default(config.get("signal_threshold_max", 1.0), name="signal_threshold_max")
    require_positive_holding = _bool_default(
        config.get("require_positive_holding_period_bars", True),
        name="candidate_prefilter.require_positive_holding_period_bars",
    )
    accepted: list[CandidateModel] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        reasons: list[str] = []
        model_id = str(candidate.model_id or "")
        if not pattern.fullmatch(model_id):
            reasons.append("malformed_model_id")
        params = dict(candidate.strategy_params or {})
        if "signal_threshold" in params:
            try:
                threshold = float(params["signal_threshold"])
            except (TypeError, ValueError):
                threshold = math.nan
            if (
                not math.isfinite(threshold)
                or threshold < threshold_min
                or threshold > threshold_max
            ):
                reasons.append("signal_threshold_out_of_bounds")
        if require_positive_holding and "holding_period_bars" in params:
            try:
                holding = float(params["holding_period_bars"])
            except (TypeError, ValueError):
                holding = math.nan
            if not math.isfinite(holding) or holding <= 0:
                reasons.append("holding_period_bars_nonpositive")
        if reasons:
            rejected.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "model_id": candidate.model_id,
                    "reasons": reasons,
                    "params": params,
                }
            )
        else:
            accepted.append(candidate)
    return accepted, {
        "schema_version": "hft3_candidate_prefilter_v1",
        "enabled": True,
        "total_candidates": len(candidates),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted_ids": [c.candidate_id for c in accepted],
        "rejected": rejected,
    }


def _idea_sampling_value(
    cli_value: float | None,
    *,
    env_name: str,
    config: Mapping[str, Any],
    key: str,
    fallback: float,
) -> float:
    if cli_value is not None:
        return cli_value
    env_value = _optional_float(os.environ.get(env_name))
    if env_value is not None:
        return env_value
    if config.get(key) is not None:
        return _float_default(config[key], name=f"llm_ideas.{key}")
    return fallback


def _resolve_idea_sampling_values(args: argparse.Namespace, config: Mapping[str, Any]) -> None:
    idea_config = _section(config, "llm_ideas")
    args.resolved_idea_temperature = _idea_sampling_value(
        args.idea_temperature,
        env_name="HFT3_IDEA_TEMPERATURE",
        config=idea_config,
        key="temperature",
        fallback=0.7,
    )
    args.resolved_idea_top_p = _idea_sampling_value(
        args.idea_top_p,
        env_name="HFT3_IDEA_TOP_P",
        config=idea_config,
        key="top_p",
        fallback=0.95,
    )


def _vectorbt_run_budget(args: argparse.Namespace, config: Mapping[str, Any]) -> dict[str, Any]:
    vectorbt_config = _section(config, "vectorbt")
    budget_config = _section(vectorbt_config, "budget")
    run_budget = {
        key: value
        for key, value in {
            "max_trials": args.vectorbt_max_trials,
            "max_models": args.vectorbt_max_models,
            "max_symbols": args.vectorbt_max_symbols,
            "max_feature_sets": args.vectorbt_max_feature_sets,
            "max_total_trials": args.vectorbt_max_total_trials,
            "max_wall_clock_seconds": args.vectorbt_max_wall_clock_seconds,
            "max_peak_memory_mb_or_null": args.vectorbt_max_peak_memory_mb,
        }.items()
        if value is not None
    }
    if budget_config.get("abort_on_budget_exhaustion") is not None:
        run_budget["abort_on_budget_exhaustion"] = _required_true(
            budget_config["abort_on_budget_exhaustion"],
            name="vectorbt.budget.abort_on_budget_exhaustion",
        )
    return run_budget


def _strict_replay_eligible_ids(
    screening_artifact: Mapping[str, Any],
) -> tuple[list[str], dict[str, list[str]]]:
    promoted_ids = {str(value) for value in screening_artifact.get("promoted_ids") or []}
    eligible: list[str] = []
    ineligible: dict[str, list[str]] = {}
    for row in screening_artifact.get("promoted") or []:
        if not isinstance(row, Mapping):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id or candidate_id not in promoted_ids:
            continue
        reasons = validate_candidate_replay_eligibility(row)
        if validate_applied_robustness_evidence_receipt(row, screening_artifact=screening_artifact):
            reasons.append("robustness_evidence_receipt_missing")
        if reasons:
            ineligible[candidate_id] = list(dict.fromkeys(str(reason) for reason in reasons))
        else:
            eligible.append(candidate_id)
    missing_rows = sorted(promoted_ids - set(eligible) - set(ineligible))
    for candidate_id in missing_rows:
        ineligible[candidate_id] = ["candidate_metadata_missing_from_screening_artifact"]
    return eligible, ineligible


def _canonical_hash(value: Any) -> str:
    return compute_robustness_evidence_receipt_hash(value)


def _main_impl(
    argv: list[str] | None = None,
    failure_context: dict[str, Any] | None = None,
    log_contexts: list[tuple[logging.Handler, contextvars.Token[str | None]]] | None = None,
) -> int:
    if failure_context is None:
        failure_context = {}
    parser = argparse.ArgumentParser(description="Autoresearch pipeline")
    parser.add_argument("--thesis", required=True, help="Natural-language trading thesis")
    parser.add_argument("--doc", help="Optional research document (PDF/DOCX/URL)")
    parser.add_argument("--event-id", required=True, help="Explicit catalog event id from events.csv")
    parser.add_argument(
        "--symbol",
        default="MES",
        help="Target symbol for feature-store fs_v1 VectorBT path (default MES)",
    )
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--chi404-summary", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Parse and generate only")
    parser.add_argument("--no-llm", action="store_true", help="Heuristic hypothesis parse only")
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--vectorbt", action="store_true", help="Enable VectorBT pre-filter before HftBacktest")
    parser.add_argument(
        "--vectorbt-only",
        action="store_true",
        help="Run VectorBT filter only and stop before the downstream realism handoff",
    )
    parser.add_argument(
        "--vectorbt-scope",
        choices=[
            "pilot",
            "screen",
            "refine",
            "paid",
            "paid-compute",
            "paid_compute",
            "broad",
            "broad-screen",
            "broad_screen",
            "all-model",
            "all_model",
            "all-models",
            "all_models",
        ],
        default=None,
        help="VectorBT screening scope; all non-pilot broad/refine/paid scopes require the Rust engine",
    )
    parser.add_argument("--vectorbt-max-trials", type=int, default=None)
    parser.add_argument("--vectorbt-max-models", type=int, default=None)
    parser.add_argument("--vectorbt-max-symbols", type=int, default=None)
    parser.add_argument("--vectorbt-max-feature-sets", type=int, default=None)
    parser.add_argument("--vectorbt-max-total-trials", type=int, default=None)
    parser.add_argument("--vectorbt-max-wall-clock-seconds", type=int, default=None)
    parser.add_argument("--vectorbt-max-peak-memory-mb", type=int, default=None)
    parser.add_argument(
        "--hftbacktest-realism",
        action="store_true",
        help="Opt in to official HftBacktest realism handoff after VectorBT screening",
    )
    parser.add_argument("--hftbacktest-data-npz", type=Path, default=None)
    parser.add_argument("--hftbacktest-latency-model", type=Path, default=None)
    parser.add_argument("--hftbacktest-fill-queue-model", type=Path, default=None)
    parser.add_argument("--hftbacktest-observation-artifact", type=Path, default=None)
    parser.add_argument("--hftbacktest-candidate-id", default=None)
    parser.add_argument("--hftbacktest-upstream-ref", default=None)
    parser.add_argument("--native-hot-path-evidence", action="append", default=[])
    parser.add_argument("--idea-set", action="store_true", help="Use packet-strict LLM idea set before candidate tests")
    parser.add_argument("--max-ideas", type=int, default=None, help="Maximum idea records to accept before static filtering")
    parser.add_argument("--review-memory-limit", type=int, default=None, help="Prior AAR/KG memory facts to include")
    parser.add_argument("--idea-temperature", type=float, default=None, help="Sampling temperature for idea generation only")
    parser.add_argument("--idea-top-p", type=float, default=None, help="Top-p sampling for idea generation only")
    parser.add_argument(
        "--candidate-search-method",
        choices=sorted(SUPPORTED_SEARCH_METHODS),
        default=None,
        help="Candidate parameter search method before VectorBT screening",
    )
    parser.add_argument(
        "--candidate-search-seed",
        type=int,
        default=None,
        help="Seed for deterministic candidate parameter search",
    )
    parser.add_argument(
        "--orchestrator-result",
        action="store_true",
        help="Emit single-line HFT3_PIPELINE_RESULT for paid-screen worker subprocesses",
    )
    parser.add_argument("--autoresearch", action="store_true", help="Run multi-generation autoresearch loop")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO / "config" / "autoresearch" / "default.yaml",
        help="Autoresearch loop YAML config",
    )
    parser.add_argument(
        "--pipeline-config",
        type=Path,
        default=_DEFAULT_PIPELINE_CONFIG_PATH,
        help="Research pipeline runtime JSON config",
    )
    parser.add_argument("--resume", action="store_true", help="Resume autoresearch campaign from manifest")
    parser.add_argument("--campaign-id", default=None, help="Autoresearch campaign id (required with --resume)")
    parser.add_argument("--max-generations", type=int, default=None, help="Override config max_generations")
    parser.add_argument("--stop-file", type=Path, default=None, help="Stop autoresearch loop when this file exists")
    args = parser.parse_args(argv)

    try:
        runtime_config = load_pipeline_runtime_config(args.pipeline_config)
        _apply_pipeline_runtime_defaults(args, runtime_config)
        _resolve_idea_sampling_values(args, runtime_config)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.autoresearch:
        from research_pipeline.generation_loop import (
            load_autoresearch_config,
            make_default_robustness_fn,
            run_autoresearch_loop,
        )

        repo_root = args.repo_root.resolve()
        run_id = _run_id()
        artifact_dir = repo_root / "research_cards" / "pipeline_runs" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        _log_path, log_handler, log_token = _configure_run_logging(artifact_dir, run_id)
        if log_contexts is not None:
            log_contexts.append((log_handler, log_token))
        _set_active_run_failure_context(
            failure_context,
            run_id=run_id,
            artifact_dir=str(artifact_dir),
            orchestrator_result=bool(args.orchestrator_result),
        )
        _write_json(
            artifact_dir / "pipeline_runtime_config.json",
            _pipeline_config_receipt(
                config=runtime_config,
                config_path=args.pipeline_config,
                args=args,
            ),
        )
        overrides = {
            "max_generations": args.max_generations,
            "stop_file": str(args.stop_file) if args.stop_file else None,
        }
        cfg = load_autoresearch_config(args.config, overrides=overrides)
        chi404 = args.chi404_summary
        if chi404 is None:
            default_lat = repo_root / "runtime" / "latency_reports" / "latency_summary.json"
            chi404 = default_lat if default_lat.is_file() else None
        robustness_fn = make_default_robustness_fn(chi404_summary=chi404) if cfg.run_robustness else None
        code, report = run_autoresearch_loop(
            repo_root=repo_root,
            thesis=args.thesis,
            event_id=args.event_id,
            cfg=cfg,
            campaign_id=args.campaign_id,
            resume=bool(args.resume),
            no_llm=args.no_llm,
            robustness_fn=robustness_fn,
        )
        _emit_pipeline_payload(
            {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "autoresearch_complete" if code == 0 else "autoresearch_failed",
                "autoresearch_report": report,
            },
            orchestrator_result=args.orchestrator_result,
        )
        return code

    if args.resume and not args.autoresearch:
        print("Error: --resume requires --autoresearch.", file=sys.stderr)
        return 2

    if args.hftbacktest_realism and args.vectorbt_only:
        print(
            "Error: --hftbacktest-realism cannot be combined with --vectorbt-only.",
            file=sys.stderr,
        )
        return 2
    if args.hftbacktest_realism and not args.vectorbt:
        print(
            "Error: --hftbacktest-realism requires --vectorbt so the handoff has a terminal screening_artifact.json.",
            file=sys.stderr,
        )
        return 2
    if args.doc and not args.dry_run and not (args.vectorbt or args.vectorbt_only):
        print(
            "Error: --doc without --vectorbt/--vectorbt-only is dry-run only; add --dry-run or use the VectorBT/HftBacktest handoff.",
            file=sys.stderr,
        )
        return 2

    repo_root = args.repo_root.resolve()
    run_id = _run_id()
    doc_summary = None
    document_cache = None
    doc_ref = str(args.doc) if args.doc else None

    request = build_pipeline_request(
        request_id=run_id,
        thesis=args.thesis,
        event_id=args.event_id,
        repo_root=repo_root,
        max_candidates=args.max_candidates,
        document_ref=doc_ref,
    )
    artifact_dir = repo_root / "research_cards" / "pipeline_runs" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _log_path, log_handler, log_token = _configure_run_logging(artifact_dir, run_id)
    if log_contexts is not None:
        log_contexts.append((log_handler, log_token))
    _set_active_run_failure_context(
        failure_context,
        run_id=run_id,
        artifact_dir=str(artifact_dir),
        orchestrator_result=bool(args.orchestrator_result),
        request_packet=request,
    )
    logger.info(
        "pipeline_run_start",
        extra={
            "payload": {
                "artifact_dir": str(artifact_dir),
                "event_id": args.event_id,
                "vectorbt": bool(args.vectorbt),
                "vectorbt_only": bool(args.vectorbt_only),
                "dry_run": bool(args.dry_run),
            }
        },
    )
    _write_json(
        artifact_dir / "pipeline_runtime_config.json",
        _pipeline_config_receipt(
            config=runtime_config,
            config_path=args.pipeline_config,
            args=args,
        ),
    )
    (artifact_dir / "request_packet.json").write_text(
        json.dumps(request, indent=2), encoding="utf-8"
    )

    if args.doc:
        try:
            doc_summary, document_cache = ingest_document_with_cache(
                args.doc,
                repo_root=repo_root,
                cache_config=_section(runtime_config, "doc_cache"),
            )
            logger.info("document_ingestion_complete", extra={"payload": document_cache})
        except Exception as exc:
            print(f"Warning: document ingestion failed, continuing without doc: {exc}", file=sys.stderr)
            doc_summary = None
            document_cache = {"status": "error", "error": str(exc)}
            logger.warning("document_ingestion_failed", extra={"payload": document_cache})
    _update_active_run_failure_context(
        failure_context,
        document_summary=doc_summary,
        document_cache=document_cache,
    )

    idea_packet = None
    idea_candidates_count = 0
    if args.idea_set:
        idea_packet = generate_idea_set(
            request,
            thesis=args.thesis,
            repo_root=repo_root,
            max_ideas=args.max_ideas or min(3, args.max_candidates),
            max_candidates=args.max_candidates,
            review_memory_limit=args.review_memory_limit,
            use_llm=not args.no_llm,
            temperature=args.resolved_idea_temperature,
            top_p=args.resolved_idea_top_p,
        )
        candidates = candidates_from_ideas(
            idea_packet,
            max_candidates=args.max_candidates,
            expand_for_vectorbt=bool(args.vectorbt or args.vectorbt_only),
            search_method=args.candidate_search_method,
            search_seed=args.candidate_search_seed,
        )
        idea_candidates_count = len(candidates)
        queued = [idea for idea in idea_packet.get("ideas", []) if idea.get("status") == "queued_for_test"]
        parsed = parsed_from_idea(queued[0]) if queued else parse_hypothesis(args.thesis, use_llm=False)
        (artifact_dir / "review_memory.json").write_text(
            json.dumps(
                {
                    "refs": idea_packet.get("refs", {}),
                    "review_memory": idea_packet.get("review_memory", []),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (artifact_dir / "idea_set_packet.json").write_text(
            json.dumps(idea_packet, indent=2), encoding="utf-8"
        )
        if _idea_set_missing_prefilter(
            idea_set_enabled=args.idea_set,
            dry_run=args.dry_run,
            vectorbt=args.vectorbt,
            vectorbt_only=args.vectorbt_only,
        ):
            candidates, candidate_prefilter = prefilter_candidates(
                candidates,
                config=_section(runtime_config, "candidate_prefilter"),
            )
            _write_json(artifact_dir / "candidate_prefilter.json", candidate_prefilter)
            logger.info("candidate_prefilter_complete", extra={"payload": candidate_prefilter})
            _update_active_run_failure_context(failure_context, candidate_prefilter=candidate_prefilter)
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_idea_set_requires_vectorbt_prefilter",
                "detail": "--idea-set full runs require --vectorbt so generated ideas pass the prefilter before evaluation.",
                "request_packet": request,
                "idea_set_packet": idea_packet,
                "document_summary": doc_summary,
                "document_cache": document_cache,
                "candidate_prefilter": candidate_prefilter,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            print(
                "Error: --idea-set full runs require --vectorbt so generated ideas pass the prefilter before evaluation.",
                file=sys.stderr,
            )
            return 2
    else:
        parsed = parse_hypothesis(
            args.thesis,
            use_llm=not args.no_llm,
            pipeline_request=request,
            repo_root=repo_root,
        )
        candidates = list(generate_candidates(
            parsed,
            max_candidates=args.max_candidates,
            expand_for_vectorbt=bool(args.vectorbt or args.vectorbt_only),
            target_event_id=args.event_id,
            target_symbol=args.symbol,
            search_method=args.candidate_search_method,
            search_seed=args.candidate_search_seed,
        ))

    candidates, candidate_prefilter = prefilter_candidates(
        candidates,
        config=_section(runtime_config, "candidate_prefilter"),
    )
    _write_json(artifact_dir / "candidate_prefilter.json", candidate_prefilter)
    logger.info("candidate_prefilter_complete", extra={"payload": candidate_prefilter})
    _update_active_run_failure_context(failure_context, candidate_prefilter=candidate_prefilter)

    if args.vectorbt or args.vectorbt_only:
        print(f"Running VectorBT filter on {len(candidates)} candidates x grid...")
        source_meta = {c.candidate_id: dict(c.metadata) for c in candidates}
        vbt_gates = PromotionGate(
            min_oos_expectancy=0.0,
            max_drawdown_pct=-50.0,
            min_trades=3 if args.vectorbt_only else 10,
        )
        run_budget = _vectorbt_run_budget(args, runtime_config)
        filter_result = filter_candidates(
            candidates=candidates,
            parsed=parsed,
            event_id=args.event_id,
            repo_root=repo_root,
            gates=vbt_gates,
            screening_scope=args.vectorbt_scope,
            run_budget=run_budget or None,
            feature_store_root=feature_store_root(repo_root),
            symbol=args.symbol,
        )
        vectorbt_artifact = filter_result.to_dict()
        print(
            f"  bar_construction_id: {vectorbt_artifact.get('bar_construction_id')}"
            + (
                " (fs_v1 row loop)"
                if vectorbt_artifact.get("bar_construction_id") == FS_V1_BAR_CONSTRUCTION_ID
                else " (ohlcv bar stub fallback)"
            )
        )
        print(f"  feature_plane_status: {vectorbt_artifact.get('feature_plane_status')}")
        print(f"  model_feature_usage_status: {vectorbt_artifact.get('model_feature_usage_status')}")
        screening_path = persist_screening_artifact(
            vectorbt_artifact,
            artifact_dir / "screening_artifact.json",
        )
        vectorbt_artifact = json.loads(screening_path.read_text(encoding="utf-8"))
        (artifact_dir / "vectorbt_filter.json").write_text(
            json.dumps(vectorbt_artifact, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  Promoted: {len(filter_result.promoted)}, Rejected: {len(filter_result.rejected)}")
        for r in filter_result.rejected:
            print(f"  REJECTED {r.candidate_id}: {r.reject_reason}")
        if filter_result.promoted:
            def _promotion_source_meta(promoted):
                base_id = (
                    promoted.vectorbt_results.get("base_candidate_id")
                    if isinstance(promoted.vectorbt_results, dict)
                    else None
                )
                embedded_meta = (
                    promoted.vectorbt_results.get("base_candidate_metadata")
                    if isinstance(promoted.vectorbt_results, dict)
                    else None
                )
                if isinstance(embedded_meta, dict):
                    return dict(embedded_meta)
                if base_id and base_id in source_meta:
                    return dict(source_meta[base_id])
                if promoted.candidate_id in source_meta:
                    return dict(source_meta[promoted.candidate_id])
                return {}

            candidates = [
                CandidateModel(
                    candidate_id=p.candidate_id,
                    model_id=p.hypothesis_id,
                    strategy_params=p.param_values,
                    thesis=parsed.thesis,
                    metadata={
                        **_promotion_source_meta(p),
                        "strategy_family": p.strategy_family,
                        "promoted": True,
                        "vectorbt_run_id": p.vectorbt_run_id,
                        "vectorbt_results": p.vectorbt_results,
                        "asset_class": p.asset_class,
                        "symbol": p.symbol,
                    },
                )
                for p in filter_result.promoted
            ]
        else:
            print("No candidates survived VectorBT filter.")
            candidates = []
            if idea_packet:
                mark_queued_ideas_without_candidates_failed(idea_packet, [])
                (artifact_dir / "idea_set_packet.json").write_text(
                    json.dumps(idea_packet, indent=2), encoding="utf-8"
                )
        if idea_packet and candidates:
            mark_queued_ideas_without_candidates_failed(
                idea_packet,
                {
                    str(candidate.metadata.get("idea_id"))
                    for candidate in candidates
                    if candidate.metadata.get("idea_id")
                },
            )
            (artifact_dir / "idea_set_packet.json").write_text(
                json.dumps(idea_packet, indent=2), encoding="utf-8"
            )
        if args.vectorbt_only:
            idea_summary = (
                summarize_ideas(idea_packet, candidates_from_ideas_count=idea_candidates_count)
                if idea_packet
                else None
            )
            report = PipelineReport(
                run_id=run_id,
                thesis=args.thesis,
                event_id=args.event_id,
                parsed=parsed,
                candidates_tested=int(filter_result.total_candidates),
                results=[],
                selected=None,
                artifact_dir=str(artifact_dir),
                document_summary=doc_summary,
            )
            llm_status = (
                str(idea_packet.get("llm_status"))
                if idea_packet
                else _pipeline_llm_status(parsed, no_llm=args.no_llm)
            )
            response = build_pipeline_response(
                report,
                request,
                llm_status=llm_status,
                llm_model=(
                    idea_packet.get("llm_model")
                    if idea_packet and llm_status == "ok"
                    else None if llm_status != "ok" else DEFAULT_MODEL_DEVELOPMENT_MODEL
                ),
                idea_summary=idea_summary,
            )
            write_pipeline_packets(artifact_dir, request, response)
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "vectorbt_only_complete" if candidates else "vectorbt_only_no_survivors",
                "request_packet": request,
                "response_packet": response,
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "parsed": {
                    "primary_model_id": parsed.primary_model_id,
                    "source": parsed.source,
                    "param_ranges": parsed.param_ranges,
                },
                "candidates": [
                    {
                        "candidate_id": c.candidate_id,
                        "model_id": c.model_id,
                        "params": c.strategy_params,
                    }
                    for c in candidates
                ],
                "document_summary": doc_summary,
                "document_cache": document_cache,
                "candidate_prefilter": candidate_prefilter,
            }
            if idea_packet:
                payload["idea_summary"] = idea_summary
                payload["idea_set_packet"] = idea_packet
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 0 if candidates else 1
        paths = {
            "screening_artifact_path": str(screening_path),
            "vectorbt_filter_path": str(artifact_dir / "vectorbt_filter.json"),
        }
        _update_active_run_failure_context(failure_context, paths=paths)
        if not args.hftbacktest_realism:
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_downstream_realism_opt_in_required",
                "detail": (
                    "screening_artifact.json was written; pass --hftbacktest-realism "
                    "with required HftBacktest input artifacts to run the official realism handoff"
                ),
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "paths": paths,
                "document_summary": doc_summary,
                "document_cache": document_cache,
                "candidate_prefilter": candidate_prefilter,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2
        promoted_ids = list(vectorbt_artifact.get("promoted_ids") or [])
        if not promoted_ids:
            replay_summary = {
                "run_id": run_id,
                "replay_realism_status": "fail",
                "fail_closed_reasons": ["screening_artifact_has_no_promoted_candidate"],
            }
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_hftbacktest_realism_no_promoted_candidates",
                "detail": "HftBacktest realism handoff requires at least one VectorBT-promoted candidate",
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "hftbacktest_realism": None,
                "replay_summary": replay_summary,
                "paths": paths,
                "document_summary": doc_summary,
                "document_cache": document_cache,
                "candidate_prefilter": candidate_prefilter,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2
        strict_eligible_ids, strict_ineligible_reasons = _strict_replay_eligible_ids(vectorbt_artifact)
        if not strict_eligible_ids:
            replay_summary = {
                "run_id": run_id,
                "replay_realism_status": "fail",
                "fail_closed_reasons": ["screening_artifact_has_no_strict_replay_eligible_candidate"],
                "ineligible_reasons": strict_ineligible_reasons,
            }
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_hftbacktest_realism_replay_ineligible",
                "detail": (
                    "HftBacktest realism handoff requires at least one promoted row with "
                    "strict replay eligibility from the robustness evidence applicator"
                ),
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "hftbacktest_realism": None,
                "replay_summary": replay_summary,
                "paths": paths,
                "document_summary": doc_summary,
                "document_cache": document_cache,
                "candidate_prefilter": candidate_prefilter,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2
        if args.hftbacktest_candidate_id and args.hftbacktest_candidate_id not in strict_eligible_ids:
            replay_summary = {
                "run_id": run_id,
                "replay_realism_status": "fail",
                "fail_closed_reasons": ["requested_candidate_not_strict_replay_eligible"],
                "eligible_candidate_ids": strict_eligible_ids,
                "ineligible_reasons": strict_ineligible_reasons,
            }
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_hftbacktest_realism_candidate_not_eligible",
                "detail": "Requested HftBacktest candidate is not strict replay-eligible.",
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "hftbacktest_realism": None,
                "replay_summary": replay_summary,
                "paths": paths,
                "document_summary": doc_summary,
                "document_cache": document_cache,
                "candidate_prefilter": candidate_prefilter,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2
        hftbacktest_candidate_id = args.hftbacktest_candidate_id or strict_eligible_ids[0]
        missing_hbt_inputs = _missing_hftbacktest_realism_inputs(args)
        if missing_hbt_inputs:
            replay_summary = {
                "run_id": run_id,
                "replay_realism_status": "fail",
                "fail_closed_reasons": [
                    f"missing_hftbacktest_realism_input:{flag}" for flag in missing_hbt_inputs
                ],
            }
            payload = {
                "run_id": run_id,
                "artifact_dir": str(artifact_dir),
                "status": "blocked_hftbacktest_realism_inputs_missing",
                "detail": "HftBacktest realism handoff was opted in but required source-lock, native, or input artifacts were not provided",
                "missing_hftbacktest_inputs": missing_hbt_inputs,
                "vectorbt_filter": vectorbt_artifact,
                "screening_artifact": vectorbt_artifact,
                "hftbacktest_realism": None,
                "replay_summary": replay_summary,
                "paths": paths,
                "document_summary": doc_summary,
                "document_cache": document_cache,
                "candidate_prefilter": candidate_prefilter,
            }
            _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
            return 2

        hftbacktest_out_dir = artifact_dir / "hftbacktest_realism"
        hftbacktest_realism = write_hftbacktest_realism_artifacts(
            repo_root=repo_root,
            out_dir=hftbacktest_out_dir,
            screening_artifact_path=screening_path,
            data_npz_path=_optional_resolved_path(args.hftbacktest_data_npz),
            latency_model_path=_optional_resolved_path(args.hftbacktest_latency_model),
            fill_queue_model_path=_optional_resolved_path(args.hftbacktest_fill_queue_model),
            observation_artifact_path=_optional_resolved_path(args.hftbacktest_observation_artifact),
            candidate_id=hftbacktest_candidate_id,
            upstream_ref=args.hftbacktest_upstream_ref,
            native_hot_path_evidence=list(args.native_hot_path_evidence or []),
            run_id=run_id,
        )
        replay_summary = hftbacktest_realism["replay_summary"]
        paths.update(
            {
                "hftbacktest_realism_dir": str(hftbacktest_out_dir),
                "source_lock_path": hftbacktest_realism.get("source_lock_path"),
                "latency_model_path": hftbacktest_realism.get("latency_model_path"),
                "fill_queue_model_path": hftbacktest_realism.get("fill_queue_model_path"),
                "official_replay_path": hftbacktest_realism.get("official_replay_path"),
                "replay_summary_path": hftbacktest_realism.get("replay_summary_path"),
            }
        )
        payload = {
            "run_id": run_id,
            "artifact_dir": str(artifact_dir),
            "status": (
                "hftbacktest_realism_pass"
                if replay_summary.get("replay_realism_status") == "pass"
                else "hftbacktest_realism_fail_closed"
            ),
            "vectorbt_filter": vectorbt_artifact,
            "screening_artifact": vectorbt_artifact,
            "hftbacktest_realism": hftbacktest_realism,
            "replay_summary": replay_summary,
            "paths": paths,
            "document_summary": doc_summary,
            "document_cache": document_cache,
            "candidate_prefilter": candidate_prefilter,
        }
        _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
        return 0 if replay_summary.get("replay_realism_status") == "pass" else 2

    if args.dry_run:
        idea_summary = (
            summarize_ideas(idea_packet, candidates_from_ideas_count=idea_candidates_count)
            if idea_packet
            else None
        )
        report = PipelineReport(
            run_id=run_id,
            thesis=args.thesis,
            event_id=args.event_id,
            parsed=parsed,
            candidates_tested=0,
            results=[],
            selected=None,
            artifact_dir=str(artifact_dir),
            document_summary=doc_summary,
        )
        llm_status = (
            str(idea_packet.get("llm_status"))
            if idea_packet
            else _pipeline_llm_status(parsed, no_llm=args.no_llm)
        )
        response = build_pipeline_response(
            report,
            request,
            llm_status=llm_status,
            llm_model=(
                idea_packet.get("llm_model")
                if idea_packet and llm_status == "ok"
                else None if llm_status != "ok" else DEFAULT_MODEL_DEVELOPMENT_MODEL
            ),
            idea_summary=idea_summary,
        )
        write_pipeline_packets(artifact_dir, request, response)
        payload = {
            "run_id": run_id,
            "artifact_dir": str(artifact_dir),
            "status": "dry_run_complete",
            "request_packet": request,
            "response_packet": response,
            "parsed": {
                "primary_model_id": parsed.primary_model_id,
                "source": parsed.source,
                "param_ranges": parsed.param_ranges,
            },
            "candidates": [
                {"candidate_id": c.candidate_id, "model_id": c.model_id, "params": c.strategy_params}
                for c in candidates
            ],
            "document_summary": doc_summary,
            "document_cache": document_cache,
            "candidate_prefilter": candidate_prefilter,
        }
        if idea_packet:
            payload["idea_summary"] = idea_summary
            payload["idea_set_packet"] = idea_packet
        _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
        return 0

    chi404 = args.chi404_summary
    if chi404 is None:
        default_lat = repo_root / "runtime" / "latency_reports" / "latency_summary.json"
        chi404 = default_lat if default_lat.is_file() else None
    if chi404 is None:
        print("Warning: no latency data available; backtest will run without CHI404 latency", file=sys.stderr)

    gates = GateThresholds(min_trades=0)
    results = []
    for cand in candidates:
        print(f"Evaluating {cand.model_id} threshold={cand.strategy_params.get('signal_threshold')}...")
        results.append(
            evaluate_model(cand, args.event_id, repo_root, chi404_summary=chi404, gates=gates)
        )

    if idea_packet:
        update_idea_statuses_from_results(idea_packet, results)
        (artifact_dir / "idea_set_packet.json").write_text(
            json.dumps(idea_packet, indent=2), encoding="utf-8"
        )

    report = PipelineReport(
        run_id=run_id,
        thesis=args.thesis,
        event_id=args.event_id,
        parsed=parsed,
        candidates_tested=len(results),
        results=results,
        selected=None,
        artifact_dir=str(artifact_dir),
        document_summary=doc_summary,
    )

    artifact = None
    if _deployment_allowed(args.idea_set, results):
        artifact = deploy_best(repo_root, report)
    if artifact is None:
        print("Note: deploy_best returned None (no passing candidates)", file=sys.stderr)
    llm_status = (
        str(idea_packet.get("llm_status"))
        if idea_packet
        else _pipeline_llm_status(parsed, no_llm=args.no_llm)
    )
    response = build_pipeline_response(
        report,
        request,
        llm_status=llm_status,
        llm_model=(
            idea_packet.get("llm_model")
            if idea_packet and llm_status == "ok"
            else None if llm_status != "ok" else DEFAULT_MODEL_DEVELOPMENT_MODEL
        ),
        idea_summary=(
            summarize_ideas(idea_packet, candidates_from_ideas_count=idea_candidates_count)
            if idea_packet
            else None
        ),
    )
    write_pipeline_packets(artifact_dir, request, response)
    status = "candidate_deployed" if artifact else "no_candidate_deployed"
    payload = {
        "run_id": run_id,
        "artifact_dir": str(artifact_dir),
        "status": status,
        "report": report.to_dict(),
        "response_packet": response,
    }
    if document_cache:
        payload["document_cache"] = document_cache
    payload["candidate_prefilter"] = candidate_prefilter
    _emit_pipeline_payload(payload, orchestrator_result=args.orchestrator_result)
    if artifact:
        print(f"Artifacts: {artifact}")
    else:
        print("No candidate deployed.", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    failure_context: dict[str, Any] = {}
    log_contexts: list[tuple[logging.Handler, contextvars.Token[str | None]]] = []
    try:
        return _main_impl(argv, failure_context=failure_context, log_contexts=log_contexts)
    except Exception as exc:
        try:
            _emit_pipeline_failure_receipt(exc, failure_context)
        except Exception as receipt_exc:
            print(
                f"Error: pipeline failed and failure receipt could not be written: {receipt_exc}",
                file=sys.stderr,
            )
        print(f"Error: pipeline failed: {exc}", file=sys.stderr)
        return 1
    finally:
        for handler, token in reversed(log_contexts):
            _close_run_logging(handler, token)


if __name__ == "__main__":
    raise SystemExit(main())
