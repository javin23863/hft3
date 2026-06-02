"""Write imbalance run artifacts under workbench_runs/<run_id>/imbalance/."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from features_engine.src.imbalance.ablation import AblationRunResult, all_ablation_modes
from features_engine.src.imbalance.registry import load_imbalance_registry
from features_engine.src.imbalance.classification import data_class_label, DataClass
from hft3_bootstrap import repo_root


def imbalance_artifact_dir(run_artifact_dir: Path) -> Path:
    d = run_artifact_dir / "imbalance"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _mean_field(rows: list, key: str) -> float | None:
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return float(sum(float(v) for v in vals) / len(vals))


def write_imbalance_artifacts(
    run_artifact_dir: Path,
    *,
    run_meta: Dict[str, Any],
    snapshots: Optional[list[dict]] = None,
    ablation_results: Optional[list[AblationRunResult]] = None,
    quality_report: Optional[dict] = None,
    auction_quality_report: Optional[dict] = None,
    latency_budget: Optional[dict] = None,
    operational_cost: Optional[dict] = None,
    mirror_to_artifacts_runs: bool = True,
) -> Path:
    out = imbalance_artifact_dir(run_artifact_dir)
    repo = repo_root()
    inv_path = repo / "runtime" / "data_audits" / "hft3_imbalance_inventory.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8")) if inv_path.is_file() else {}

    reg = load_imbalance_registry(repo)
    _write_json(
        out / "imbalance_feature_manifest.json",
        {
            "features": [
                {
                    "feature_name": f.feature_name,
                    "feature_family": f.feature_family,
                    "feature_set_id": f.feature_set_id,
                    "source_schema": f.source_schema,
                    "config_hash": reg["config_hash"],
                }
                for f in reg["features"]
            ],
            "run_id": run_meta.get("run_id"),
        },
    )
    _write_json(out / "imbalance_data_inventory_reference.json", {"inventory_path": str(inv_path), "dataset_count": inv.get("dataset_count", 0)})
    _write_json(out / "imbalance_data_coverage.json", {"datasets": inv.get("datasets", [])[:50]})
    _write_json(out / "imbalance_gap_report.json", {"gaps": [d for d in inv.get("datasets", []) if d.get("recommended_action") == "quarantine"][:100]})

    book_summary = {}
    of_summary = {}
    auc_summary = {}
    if snapshots:
        books = [s.get("book") for s in snapshots if s.get("book")]
        ofs = [s.get("order_flow") for s in snapshots if s.get("order_flow")]
        aucs = [s.get("auction") for s in snapshots if s.get("auction")]
        book_summary = {
            "sample_count": len(books),
            "last": books[-1] if books else {},
            "mean_book_imbalance_l1": _mean_field(books, "book_imbalance_l1"),
        }
        of_summary = {
            "sample_count": len(ofs),
            "last": ofs[-1] if ofs else {},
            "mean_ofi_l1": _mean_field(ofs, "ofi_l1"),
        }
        auc_summary = {
            "sample_count": len(aucs),
            "last": aucs[-1] if aucs else {},
        }

    _write_json(out / "book_imbalance_summary.json", book_summary)
    _write_json(out / "order_flow_imbalance_summary.json", of_summary)
    _write_json(out / "auction_imbalance_summary.json", auc_summary)
    _write_json(
        out / "true_vs_proxy_classification.json",
        {
            "MBO": data_class_label(DataClass.MBO),
            "MBP_10": data_class_label(DataClass.MBP_10),
            "note": "MBP-10 is aggregated depth, not Level 3",
        },
    )
    _write_json(out / "imbalance_quality_checks.json", quality_report or {"passed": False, "results": [], "note": "missing"})
    _write_json(
        out / "auction_quality_checks.json",
        auction_quality_report or {"passed": True, "results": [], "note": "no auction feed"},
    )
    _write_json(
        out / "imbalance_ablation_results.json",
        {
            "modes": [m.to_dict() for m in all_ablation_modes()],
            "results": [r.to_dict() for r in (ablation_results or [])],
        },
    )
    _write_json(
        out / "imbalance_latency_budget.json",
        latency_budget
        or {
            "families": {f.feature_family: f.latency_estimate_ns for f in reg["features"]},
            "profile": "workbench_default",
        },
    )
    _write_json(
        out / "imbalance_operational_cost.json",
        operational_cost or {"storage_bytes_estimate": 0, "replay_multiplier": 1.0},
    )
    _write_json(
        out / "imbalance_lineage.json",
        {
            "run_meta": run_meta,
            "config_hash": reg["config_hash"],
            "feature_version": reg["feature_version"],
        },
    )

    if mirror_to_artifacts_runs:
        run_id = run_meta.get("run_id", run_artifact_dir.name)
        mirror = repo / "artifacts" / "runs" / run_id / "imbalance"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        if mirror.exists():
            shutil.rmtree(mirror)
        shutil.copytree(out, mirror)

    return out
