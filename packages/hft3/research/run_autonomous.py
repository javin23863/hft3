"""HFT3 autonomous research runner (Phase 2).

========================================================================
STATUS: SCAFFOLD. NOT YET PRODUCTION-READY.
========================================================================

This module is the headless orchestrator shell. It loads a campaign
config, walks 12 stages, writes 14 artifacts per run, and persists a
checkpoint. **The actual backtest, robustness pack, walk-forward, and
scoring work is NOT yet wired** — those stages emit GateResults with
`observed_value=None` and `pass_fail=False` (BLOCKING) so the run
defaults to QUARANTINE / REJECT.

What is real:
  - YAML config loader + schema validator
  - Resumable state checkpoint
  - Atomic artifact writes
  - 12-stage pipeline that runs end-to-end without crashing
  - QUARANTINE / REJECT default in scaffolded mode
  - Promotion-gate wiring (writes to the atomic certification registry)
  - Report generator with the 22 spec sections

What is pending (blocked on WorkbenchEngine integration / Phase 10 wiring):
  - Real backtest metrics from WorkbenchEngine
  - Real robustness pack results
  - Walk-forward correlation (single WF only today; double-WF in Phase 10)
  - Real scoring (not the QUARANTINE default)

Do NOT ship a candidate as PROMOTE through this runner until
`stage_robustness_and_wf` produces real observed values. The
honesty tests in `tests/test_runner_honesty.py` enforce this.
========================================================================

End-to-end orchestrator that ties the existing HFT3 modules into
a single deterministic, resumable, auditable pipeline:

  1. Load + validate campaign YAML config
  2. Resolve data sources, symbol universe, event windows, latency profile
  3. Resolve feature set, model set, alpha/defensive/hybrid combinations
  4. Load or generate hypotheses (via Phase 3 intake bundle)
  5. Convert hypotheses into experiment specifications
  6. Run backtests (delegates to existing WorkbenchEngine)
  7. Run robustness checks
  8. Run walk-forward validation
  9. Run walk-forward correlation (Phase 10 stub: single WFC today)
  10. Score candidates (delegates to existing scoring)
  11. Decide REJECT / QUARANTINE / PROMOTE
  12. Generate reports
  13. Write structured artifacts (Phase 12 bundle)
  14. Update HFT3 registry atomically (Phase 11)

The runner is **headless**: no Streamlit, no notebooks, no interactive
prompts. It exits with code 0 on PROMOTE, 1 on REJECT, 2 on QUARANTINE,
3 on infrastructure failure.

The runner is **resumable**: each stage writes a checkpoint to
`runtime/research/{run_id}/state.json`. A rerun with the same `run_id`
skips stages whose outputs are already valid (per checkpoint schema).

The runner is **auditable**: every stage emits its own artifact file
under `artifacts/runs/{run_id}/` and the bundle is listed in the run
manifest.

Usage:
    python hft3-research.py --config configs/research/autonomous_hft3.yaml
"""
from __future__ import annotations

# --- bootstrap pythonpath for headless `python -m` invocations ---
# This makes `python -m hft3.research.run_autonomous` work from the repo
# root even before `pip install -e .`. Once hft3 is installed the
# bootstrap is a no-op (the path is already on sys.path).
from . import _path_bootstrap  # noqa: F401  (side-effect: path setup)

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from hft3.validation.certification_registry import (
    CertificationRecord,
    backtester_version,
    git_sha,
    load_registry,
    repo_root,
    save_registry as save_certification_registry,
)
from hft3.validation.gate_result import (
    GateCategory,
    GateResult,
    Severity,
    aggregate_promotion,
    blocking_failures,
    write_robustness_gates_json,
)

logger = logging.getLogger(__name__)


# ---------- config schema (lightweight pydantic-free dataclass) ----------


@dataclass
class CampaignConfig:
    """The campaign YAML deserialized into a typed object.

    Schema (see `configs/research/autonomous_hft3.yaml` for an example):

    ```yaml
    campaign_id: "cpi-2024-tight"
    research_input: null  # or path to a research_inputs/{id}/ bundle
    data:
      dataset_id: "databento_es_mbo_v1"
      symbol_universe: ["ES", "MES"]
      event_windows:
        - event_id: "CPI_2024_09_11_TIGHT"
          start_ns: 1725600000000000000
          end_ns:   1725772800000000000
    latency_profile:
      decision_to_send_us: 80
      send_to_ack_us: 200
      ack_to_fill_us: 500
    features:
      feature_set_id: "core_64_v1"
    models:
      alpha: ["HYP_1", "HYP_5"]
      defensives: ["regime_filter", "throttle"]
      structurals: ["pdf_topology_1"]
    robustness:
      monte_carlo: {trials: 1000, sharpe_min: 0.5}
      walk_forward: {folds: 5, embargo_ns: 3600000000000}
    scoring:
      min_sharpe: 0.5
      max_drawdown: -0.10
    registry:
      promote_on: "PROMOTE"   # REJECT | QUARANTINE | PROMOTE
    output:
      artifacts_dir: "artifacts/runs"
    ```
    """

    campaign_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    latency_profile: Dict[str, Any] = field(default_factory=dict)
    features: Dict[str, Any] = field(default_factory=dict)
    models: Dict[str, Any] = field(default_factory=dict)
    robustness: Dict[str, Any] = field(default_factory=dict)
    scoring: Dict[str, Any] = field(default_factory=dict)
    registry: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    research_input: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: Path) -> "CampaignConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"config must be a YAML mapping, got {type(raw)}")
        if "campaign_id" not in raw:
            raise ValueError("config missing required field: campaign_id")
        return cls(
            campaign_id=str(raw["campaign_id"]),
            data=dict(raw.get("data", {})),
            latency_profile=dict(raw.get("latency_profile", {})),
            features=dict(raw.get("features", {})),
            models=dict(raw.get("models", {})),
            robustness=dict(raw.get("robustness", {})),
            scoring=dict(raw.get("scoring", {})),
            registry=dict(raw.get("registry", {})),
            output=dict(raw.get("output", {})),
            research_input=raw.get("research_input"),
        )

    def validate(self) -> List[str]:
        """Return list of validation errors. Empty list = OK."""
        errors: list[str] = []
        if not self.campaign_id or not self.campaign_id.replace("-", "").replace("_", "").isalnum():
            errors.append("campaign_id must be alphanumeric (with - or _ allowed)")
        if not self.data.get("dataset_id"):
            errors.append("data.dataset_id is required")
        if not self.data.get("symbol_universe"):
            errors.append("data.symbol_universe must be non-empty")
        if not self.data.get("event_windows"):
            errors.append("data.event_windows must contain at least one event")
        if not self.models.get("alpha"):
            errors.append("models.alpha must contain at least one model id")
        return errors


# ---------- run state (for resumability) ----------


@dataclass
class RunState:
    run_id: str
    campaign_id: str
    git_sha: str
    started_at: str
    last_updated_at: str
    completed_stages: List[str] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)
    config_hash: str = ""
    config_snapshot_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "RunState":
        return cls(**raw)

    def mark_complete(self, stage: str, artifact_path: Optional[str] = None) -> None:
        if stage not in self.completed_stages:
            self.completed_stages.append(stage)
        if artifact_path:
            self.artifacts[stage] = str(artifact_path)
        self.last_updated_at = datetime.now(timezone.utc).isoformat()


# ---------- runner ----------


class RecoveryDecision(str, Enum):
    SAFE_TO_RESUME = "SAFE_TO_RESUME"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    UNRECOVERABLE = "UNRECOVERABLE"


class AutonomousRunner:
    """End-to-end headless orchestrator. Single entry point per run.

    The runner is **deterministic**: same config + same git SHA + same
    data = same artifacts. The runner is **resumable**: re-running with
    the same `run_id` skips completed stages (per `RunState.completed_stages`).
    The runner is **auditable**: every stage writes a JSON artifact and
    the run manifest is written at the end.
    """

    def __init__(
        self,
        config: CampaignConfig,
        root: Optional[Path] = None,
        run_id: Optional[str] = None,
    ) -> None:
        self.root = root or repo_root()
        self.config = config
        self.run_id = run_id or f"RUN-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        self.state = RunState(
            run_id=self.run_id,
            campaign_id=self.config.campaign_id,
            git_sha=git_sha(self.root),
            started_at=datetime.now(timezone.utc).isoformat(),
            last_updated_at=datetime.now(timezone.utc).isoformat(),
        )

        # Resolve run dir
        artifacts_root = Path(
            self.config.output.get("artifacts_dir", "artifacts/runs")
        )
        if not artifacts_root.is_absolute():
            artifacts_root = self.root / artifacts_root
        self.artifacts_root = artifacts_root
        self.run_dir = self.artifacts_root / self.run_id
        self.state_dir = self.root / "runtime" / "research" / self.run_id
        self.state_path = self.state_dir / "state.json"
        self.recovery_decision = RecoveryDecision.SAFE_TO_RESUME
        self.recovery_reason = "fresh run"

        # Resume from checkpoint if present
        if self.state_path.is_file():
            try:
                saved = RunState.from_dict(
                    json.loads(self.state_path.read_text(encoding="utf-8"))
                )
                if saved.campaign_id == self.config.campaign_id and saved.run_id == self.run_id:
                    if _timestamp_regressed(saved.started_at, saved.last_updated_at):
                        self._classify_recovery(
                            RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                            "checkpoint timestamps regress",
                        )
                    else:
                        self.state = saved
                        self.recovery_reason = "checkpoint loaded"
                        logger.info(
                            "resuming from checkpoint: stages=%s", self.state.completed_stages
                        )
                else:
                    self._classify_recovery(
                        RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                        "checkpoint run_id/campaign_id mismatch",
                    )
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                self._classify_recovery(
                    RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                    f"could not load checkpoint: {exc}",
                )
                logger.warning("could not load state; manual review required: %s", exc)

        # Phase 6: data-resolution tag and derived gate, populated by
        # stage_resolve_data. Initialized to None so tests can assert
        # on them after a run without AttributeError.
        self._data_resolution_tag: Any = None
        self._data_eligibility_gate: Any = None
        # Phase 12: artifact bundle validation gate, populated by
        # stage_artifact_bundle.
        self._artifact_bundle_gate: Any = None

    # --- stage helpers ---

    def _stage_done(self, name: str) -> bool:
        if name not in self.state.completed_stages:
            return False
        artifact = self.state.artifacts.get(name)
        if not artifact:
            self._classify_recovery(
                RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                f"completed stage {name} has no artifact path",
            )
            raise RuntimeError(self.recovery_reason)
        path = Path(artifact)
        if not path.is_file():
            self._classify_recovery(
                RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                f"completed stage {name} artifact missing: {path}",
            )
            raise RuntimeError(self.recovery_reason)
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                self._classify_recovery(
                    RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                    f"completed stage {name} artifact corrupt: {exc}",
                )
                raise RuntimeError(self.recovery_reason)
        return True

    def _classify_recovery(self, decision: RecoveryDecision, reason: str) -> None:
        if self.recovery_decision != RecoveryDecision.UNRECOVERABLE:
            self.recovery_decision = decision
            self.recovery_reason = reason

    def _stage_start(self, name: str) -> None:
        logger.info("[%s] stage start: %s", self.run_id, name)
        self.state.last_updated_at = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def _stage_end(self, name: str, artifact_path: Optional[Path] = None) -> None:
        logger.info("[%s] stage end: %s", self.run_id, name)
        self.state.mark_complete(name, str(artifact_path) if artifact_path else None)
        self._save_state()

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.state_path,
            json.dumps(self.state.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n",
        )

    def _write_artifact(self, name: str, payload: Any) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / name
        if isinstance(payload, (dict, list)):
            _atomic_write_text(
                path,
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
            )
        else:
            _atomic_write_text(path, str(payload))
        return path

    # --- per-stage runners ---

    def stage_load_config(self) -> Path:
        """Stage 1: validate config + write config snapshot."""
        if self._stage_done("load_config"):
            return Path(self.state.artifacts["load_config"])
        self._stage_start("load_config")
        errors = self.config.validate()
        if errors:
            raise ValueError("config validation failed: " + "; ".join(errors))
        # snapshot
        snapshot_path = self.run_dir / "config_snapshot.yaml"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            snapshot_path,
            yaml.safe_dump(
                {
                    "campaign_id": self.config.campaign_id,
                    "data": self.config.data,
                    "latency_profile": self.config.latency_profile,
                    "features": self.config.features,
                    "models": self.config.models,
                    "robustness": self.config.robustness,
                    "scoring": self.config.scoring,
                    "registry": self.config.registry,
                    "output": self.config.output,
                    "research_input": self.config.research_input,
                },
                sort_keys=True,
            ),
        )
        self._write_artifact(
            "config_hash.txt", _hash_file(snapshot_path)
        )
        self._stage_end("load_config", snapshot_path)
        return snapshot_path

    def stage_resolve_data(self) -> Path:
        """Stage 2: resolve data sources, dataset IDs, symbol universe,
        event windows. Writes data_lineage.json + data_resolution.json
        (Phase 6) and emits a data-eligibility gate (Phase 8).

        The campaign config may specify separate `requested` and
        `resolved` data classes. When they differ, the runner tags
        the run with the downgrade reason and demotes promotion
        eligibility — silent downgrades are forbidden.
        """
        if self._stage_done("resolve_data"):
            return Path(self.state.artifacts["resolve_data"])
        self._stage_start("resolve_data")

        from hft3.data_class import DataClass, make_tag
        from hft3.validation.gate_result import GateCategory, GateResult, Severity

        data_cfg = self.config.data
        # Accept either the legacy single `resolution` field or the new
        # Phase 6 `requested` / `resolved` pair. If only `resolution`
        # is given, both classes are set to it (no downgrade).
        requested = data_cfg.get("requested") or data_cfg.get("resolution", "L3_MBO")
        resolved = data_cfg.get("resolved") or data_cfg.get("resolution", "L3_MBO")

        time_windows: list[tuple[int, int]] = []
        for w in data_cfg.get("event_windows", []):
            s = w.get("start_ns")
            e = w.get("end_ns")
            if s is not None and e is not None:
                time_windows.append((int(s), int(e)))

        tag = make_tag(
            requested=requested,
            resolved=resolved,
            source=data_cfg.get("source", "databento"),
            symbols=list(data_cfg.get("symbol_universe", [])),
            time_windows=time_windows,
            notes=str(data_cfg.get("notes", "")),
        )

        # Persist Phase 12 artifact: data_resolution.json
        path = self._write_artifact("data_resolution.json", tag.to_dict())
        # Backward-compat: keep data_lineage.json too (legacy)
        lineage = {
            "dataset_id": data_cfg.get("dataset_id"),
            "symbol_universe": data_cfg.get("symbol_universe", []),
            "event_windows": data_cfg.get("event_windows", []),
            "data_source": data_cfg.get("source", "databento"),
            "data_resolution": resolved,
            "requested_data_class": requested,
            "resolved_data_class": resolved,
            "downgrade_reason": tag.downgrade_reason,
            "validity_impact": tag.validity_impact.value,
            "promotion_eligibility_impact": tag.promotion_eligibility_impact.value,
        }
        self._write_artifact("data_lineage.json", lineage)

        # Emit a data-eligibility gate. Stash on the runner so the
        # robustness stage can merge it.
        from hft3.data_class import to_gate_result
        self._data_eligibility_gate = to_gate_result(tag)
        self._data_resolution_tag = tag

        self._stage_end("resolve_data", path)
        return path

    def stage_resolve_features(self) -> Path:
        """Stage 3: feature set + latency profile resolution."""
        if self._stage_done("resolve_features"):
            return Path(self.state.artifacts["resolve_features"])
        self._stage_start("resolve_features")
        payload = {
            "feature_set_id": self.config.features.get("feature_set_id", "core_64_v1"),
            "latency_profile": self.config.latency_profile,
            "execution_assumptions": {
                "fill_model": self.config.latency_profile.get("fill_model", "perfect"),
                "slippage_bps": self.config.latency_profile.get("slippage_bps", 0.5),
                "fees_per_side_usd": self.config.latency_profile.get("fees_per_side_usd", 1.5),
                "idealized": self.config.latency_profile.get("idealized", True),
            },
        }
        path = self._write_artifact("feature_lineage.json", payload)
        self._stage_end("resolve_features", path)
        return path

    def stage_resolve_model_combinations(self) -> Path:
        """Stage 4: model set + alpha/defensive/hybrid combinations
        (per Phase 7: MODEL_COMBINATIONS)."""
        if self._stage_done("resolve_model_combinations"):
            return Path(self.state.artifacts["resolve_model_combinations"])
        self._stage_start("resolve_model_combinations")
        # Build combinations data-driven
        from apps.workbench.src.core.defensive import MODEL_COMBINATIONS

        alpha_ids = self.config.models.get("alpha", [])
        defensive_ids = self.config.models.get("defensives", [])
        structural_ids = self.config.models.get("structurals", [])
        combinations: list[dict[str, Any]] = []
        for combo in MODEL_COMBINATIONS:
            combinations.append({
                "name": combo["name"],
                "alpha_ids": alpha_ids if combo["alpha"] else [],
                "defensive_ids": (
                    [d for d in defensive_ids if d in combo["defensives"]]
                    if combo["defensives"] else (defensive_ids if combo["name"] == "ablation_no_defensives" else [])
                ) if combo["alpha"] else combo["defensives"],
                "structural_ids": structural_ids if combo["structurals"] else [],
            })
        path = self._write_artifact("model_combination.json", combinations)
        self._stage_end("resolve_model_combinations", path)
        return path

    def stage_generate_hypotheses(self) -> Path:
        """Stage 5: load or generate hypotheses (Phase 3 intake bundle)."""
        if self._stage_done("generate_hypotheses"):
            return Path(self.state.artifacts["generate_hypotheses"])
        self._stage_start("generate_hypotheses")
        if self.config.research_input:
            bundle_path = Path(self.config.research_input)
            ref = {
                "source": "intake_bundle",
                "path": str(bundle_path.resolve()),
                "campaign_id": self.config.campaign_id,
            }
        else:
            ref = {
                "source": "auto",
                "alpha_ids": self.config.models.get("alpha", []),
                "campaign_id": self.config.campaign_id,
            }
        path = self._write_artifact("research_input_reference.json", ref)
        self._stage_end("generate_hypotheses", path)
        return path

    def stage_experiment_specs(self) -> Path:
        """Stage 6: convert hypotheses into experiment specifications."""
        if self._stage_done("experiment_specs"):
            return Path(self.state.artifacts["experiment_specs"])
        self._stage_start("experiment_specs")
        alpha_ids = self.config.models.get("alpha", [])
        specs: list[dict[str, Any]] = []
        for alpha_id in alpha_ids:
            for event in self.config.data.get("event_windows", []):
                specs.append({
                    "alpha_id": alpha_id,
                    "event_id": event.get("event_id"),
                    "feature_set_id": self.config.features.get("feature_set_id"),
                    "latency_profile": self.config.latency_profile,
                    "symbol_universe": self.config.data.get("symbol_universe", []),
                })
        path = self._write_artifact("experiment_spec.json", specs)
        self._stage_end("experiment_specs", path)
        return path

    def stage_backtest(self) -> Path:
        """Stage 7: run backtests. Stub: the runner does not invoke the
        full WorkbenchEngine here (that requires Databento NPZ data on
        CHI404). Instead it records a metrics payload that downstream
        consumers can replace. The artifact is `backtest_metrics.json`
        with the 33-point schema from Phase 5."""
        if self._stage_done("backtest"):
            return Path(self.state.artifacts["backtest"])
        self._stage_start("backtest")
        specs = json.loads(
            (self.run_dir / "experiment_spec.json").read_text(encoding="utf-8")
        )
        metrics = {
            "specs": specs,
            "idealized": True,  # Phase 5 will replace with real metrics
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "git_sha": self.state.git_sha,
            "metrics": {},  # populated when WorkbenchEngine integration lands
        }
        path = self._write_artifact("backtest_metrics.json", metrics)
        self._stage_end("backtest", path)
        return path

    def stage_robustness_and_wf(self) -> Path:
        """Stages 8-9: robustness + walk-forward. Emits GateResults
        (Phase 8) and writes robustness_gates.json (Phase 12).

        HONEST SCAFFOLD: when WorkbenchEngine integration is not yet
        available, the gates below have `observed_value=None` and
        `pass_fail=False` with `severity=BLOCKING`. This means a
        scaffolded run will NEVER pass T3 — the runner defaults to
        QUARANTINE via `stage_score_and_decide` until real metrics
        arrive. This is the opposite of the previous behavior, which
        wrote `pass_fail=True` with no observation (a silent lie that
        let bad candidates ship as "passed"). DO NOT change this back
        without a code review — see `tests/test_runner_no_dishonest_passes`
        for the guard.
        """
        if self._stage_done("robustness_and_wf"):
            return Path(self.state.artifacts["robustness_and_wf"])
        self._stage_start("robustness_and_wf")
        gates: list[GateResult] = []
        # Real pipeline replaces these with observed values when
        # WorkbenchEngine integration lands. Until then, every gate
        # has observed_value=None and pass_fail=False (BLOCKING).
        scoring = self.config.scoring or {}
        min_sharpe = float(scoring.get("min_sharpe", 0.5))
        max_drawdown = float(scoring.get("max_drawdown", -0.10))
        gates.append(GateResult(
            gate_name="monte_carlo_sharpe_p05",
            gate_category=GateCategory.ROBUSTNESS,
            metric_name="sharpe_p05",
            threshold=min_sharpe,
            observed_value=None,
            comparison_operator=">=",
            pass_fail=False,
            severity=Severity.BLOCKING,
            reason_code="ROBUSTNESS_PENDING",
            artifact_reference="robustness_gates.json",
        ))
        gates.append(GateResult(
            gate_name="oos_max_drawdown",
            gate_category=GateCategory.DRAWDOWN_TAIL_RISK,
            metric_name="max_drawdown",
            threshold=max_drawdown,
            observed_value=None,
            comparison_operator=">=",
            pass_fail=False,
            severity=Severity.BLOCKING,
            reason_code="DRAWDOWN_PENDING",
        ))
        gates.append(GateResult(
            gate_name="walk_forward_pass",
            gate_category=GateCategory.WALK_FORWARD,
            metric_name="wf_passed",
            threshold=1.0,
            observed_value=None,
            comparison_operator="==",
            pass_fail=False,
            severity=Severity.BLOCKING,
            reason_code="WF_PENDING",
        ))
        gates.append(GateResult(
            gate_name="artifact_completeness",
            gate_category=GateCategory.ARTIFACT_COMPLETENESS,
            metric_name="expected_files_present",
            threshold=1.0,
            observed_value=None,
            comparison_operator="==",
            pass_fail=False,
            severity=Severity.BLOCKING,
            reason_code="ARTIFACT_PENDING",
        ))
        # Emit a PENDING gate for the double-WF correlation before persisting
        # robustness_gates.json; this keeps the blocking summary honest.
        gates.append(GateResult(
            gate_name="double_wf_correlation",
            gate_category=GateCategory.WALK_FORWARD_CORRELATION,
            metric_name="spearman",
            threshold=0.20,
            observed_value=None,
            comparison_operator=">=",
            pass_fail=False,
            severity=Severity.BLOCKING,
            reason_code="DOUBLE_WF_PENDING",
            artifact_reference="walk_forward_correlation.json",
        ))
        # Persist to robustness_gates.json
        rg_path = self.run_dir / "robustness_gates.json"
        rg_path.parent.mkdir(parents=True, exist_ok=True)
        write_robustness_gates_json(
            rg_path,
            gates,
            tier="T3",
            run_id=self.run_id,
            git_sha=self.state.git_sha,
            thresholds_source="autonomous_runner",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        # Also emit walk_forward_results.json
        wf_path = self._write_artifact("walk_forward_results.json", {
            "tier": "T3",
            "single_wf_pending": True,
            "double_wf_pending": True,
            "folds": self.config.robustness.get("walk_forward", {}).get("folds", 5),
        })
        # walk_forward_correlation.json (Phase 10: honest PENDING stub)
        # The double-WF correlator exists in
        # apps/workbench/src/robustness/wfc/double_wf.py but is not yet
        # wired into the runner (requires real Workbench matrix data).
        # Until then, emit a PENDING stub with pass_fail=False so the
        # runner cannot PROMOTE.
        self._write_artifact("walk_forward_correlation.json", {
            "tier": "T3",
            "method": "PENDING",
            "double_wf_correlator": "PENDING",
            "pass_fail": False,
            "correlation_score": 0.0,
            "minimum_required_score": 0.20,
            "rejection_reasons": [
                "Double-WF correlator not yet wired (requires Workbench matrix data)"
            ],
        })
        self._stage_end("robustness_and_wf", rg_path)
        return rg_path

    def stage_score_and_decide(self) -> Path:
        """Stage 10-11: score candidates and decide REJECT/QUARANTINE/PROMOTE."""
        if self._stage_done("score_and_decide"):
            return Path(self.state.artifacts["score_and_decide"])
        self._stage_start("score_and_decide")
        # The decision is a code-generated, artifact-preserved value.
        # The default decision is QUARANTINE — the runner is in
        # scaffolding mode until WorkbenchEngine integration lands.
        decision = "QUARANTINE"
        reason = (
            "Autonomous runner scaffolding: WorkbenchEngine integration is "
            "not yet wired. Candidate is quarantined pending real backtest + "
            "robustness + walk-forward output. Re-run after WorkbenchEngine "
            "integration lands."
        )
        scoring_summary = {
            "decision": decision,
            "reason": reason,
            "campaign_id": self.config.campaign_id,
            "run_id": self.run_id,
            "git_sha": self.state.git_sha,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "min_sharpe_threshold": self.config.scoring.get("min_sharpe", 0.5),
            "max_drawdown_threshold": self.config.scoring.get("max_drawdown", -0.10),
        }
        path = self._write_artifact("scoring_summary.json", scoring_summary)
        pd_path = self._write_artifact("promotion_decision.json", {
            "decision": decision,
            "reason": reason,
            "blocking_gates": [g.to_dict() for g in blocking_failures(_gates_from_summary(scoring_summary))],
        })
        self._stage_end("score_and_decide", path)
        return path

    def stage_generate_report(self) -> Path:
        """Stage 12: generate `report.md` with the 22 spec sections."""
        if self._stage_done("generate_report"):
            return Path(self.state.artifacts["generate_report"])
        self._stage_start("generate_report")
        scoring = json.loads(
            (self.run_dir / "scoring_summary.json").read_text(encoding="utf-8")
        )
        decision = scoring.get("decision", "QUARANTINE")
        report = _build_report(
            campaign_id=self.config.campaign_id,
            run_id=self.run_id,
            git_sha=self.state.git_sha,
            started_at=self.state.started_at,
            decision=decision,
            reason=scoring.get("reason", ""),
            config=self.config,
            artifacts={
                k: v for k, v in self.state.artifacts.items()
            },
        )
        path = self._write_artifact("report.md", report)
        self._stage_end("generate_report", path)
        return path

    def stage_artifact_bundle(self) -> Path:
        """Stage 13: write the structured artifact bundle (Phase 12).

        Writes the 3 missing required artifacts (git_commit.txt,
        execution_assumptions.json, logs.txt). The bundle validation
        runs at the end of the run (after all stages have written their
        artifacts) via `_finalize_bundle()`.
        """
        if self._stage_done("artifact_bundle"):
            return Path(self.state.artifacts["artifact_bundle"])
        self._stage_start("artifact_bundle")

        # Write the 3 missing required artifacts
        self._write_artifact("git_commit.txt", self.state.git_sha + "\n")
        self._write_artifact("execution_assumptions.json", {
            "fill_model": self.config.latency_profile.get("fill_model", "perfect"),
            "slippage_bps": self.config.latency_profile.get("slippage_bps", 0.5),
            "fees_per_side_usd": self.config.latency_profile.get("fees_per_side_usd", 1.5),
            "idealized": self.config.latency_profile.get("idealized", True),
            "decision_to_send_us": self.config.latency_profile.get("decision_to_send_us", 80),
            "send_to_ack_us": self.config.latency_profile.get("send_to_ack_us", 200),
            "ack_to_fill_us": self.config.latency_profile.get("ack_to_fill_us", 500),
        })
        # logs.txt: capture the runner's log output (if any)
        logs_path = self.run_dir / "logs.txt"
        if not logs_path.is_file():
            _atomic_write_text(
                logs_path,
                f"Autonomous runner logs for {self.run_id}\n"
                f"Started: {self.state.started_at}\n"
                f"Git SHA: {self.state.git_sha}\n"
                f"Campaign: {self.config.campaign_id}\n"
                f"Stages completed: {len(self.state.completed_stages)}\n",
            )

        # Write the manifest (last, so it reflects the final state)
        manifest = {
            "run_id": self.run_id,
            "campaign_id": self.config.campaign_id,
            "git_sha": self.state.git_sha,
            "started_at": self.state.started_at,
            "last_updated_at": self.state.last_updated_at,
            "completed_stages": self.state.completed_stages,
            "artifacts": self.state.artifacts,
            "schema_version": 1,
        }
        path = self._write_artifact("manifest.json", manifest)
        self._stage_end("artifact_bundle", path)
        return path

    def _finalize_bundle(self) -> None:
        """Run bundle validation at the end of the run (after all stages
        have written their artifacts). Writes artifact_bundle_validation.json
        and re-writes the manifest with the validation result."""
        from hft3.artifact_bundle import validate_bundle, to_gate_result
        bundle_result = validate_bundle(self.run_dir)
        self._write_artifact("artifact_bundle_validation.json", bundle_result.to_dict())
        # Emit a gate result for bundle completeness
        bundle_gate = to_gate_result(bundle_result)
        self._artifact_bundle_gate = bundle_gate
        # Re-write the manifest with the validation result
        manifest = {
            "run_id": self.run_id,
            "campaign_id": self.config.campaign_id,
            "git_sha": self.state.git_sha,
            "started_at": self.state.started_at,
            "last_updated_at": self.state.last_updated_at,
            "completed_stages": self.state.completed_stages,
            "artifacts": self.state.artifacts,
            "schema_version": 1,
            "bundle_validation": bundle_result.to_dict(),
        }
        self._write_artifact("manifest.json", manifest)

    def _load_valid_registry_marker(self, path: Path, decision: str) -> dict[str, Any]:
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._classify_recovery(
                RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                "registry_update marker is corrupt",
            )
            raise RuntimeError(self.recovery_reason)
        if not isinstance(marker, dict):
            self._classify_recovery(
                RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                "registry_update marker is not an object",
            )
            raise RuntimeError(self.recovery_reason)
        if (
            marker.get("run_id") != self.run_id
            or marker.get("campaign_id") != self.config.campaign_id
            or marker.get("decision") != decision
        ):
            self._classify_recovery(
                RecoveryDecision.MANUAL_REVIEW_REQUIRED,
                "registry_update marker identity mismatch",
            )
            raise RuntimeError(self.recovery_reason)
        return marker

    def stage_registry_update(self) -> Path:
        """Stage 14: update the HFT3 registry atomically (Phase 11).

        Only PROMOTE decisions write to the certification registry.
        REJECT and QUARANTINE decisions write a `registry_update.json`
        marker artifact but do not touch the certification registry.
        """
        scoring = json.loads(
            (self.run_dir / "scoring_summary.json").read_text(encoding="utf-8")
        )
        decision = scoring.get("decision", "QUARANTINE")
        if self._stage_done("registry_update"):
            path = Path(self.state.artifacts["registry_update"])
            self._load_valid_registry_marker(path, decision)
            return path
        existing = self.run_dir / "registry_update.json"
        if existing.is_file():
            self._load_valid_registry_marker(existing, decision)
            self._stage_end("registry_update", existing)
            return existing
        self._stage_start("registry_update")
        registry_update = {
            "decision": decision,
            "promoted_to_certification_registry": False,
            "reason": scoring.get("reason", ""),
            "campaign_id": self.config.campaign_id,
            "run_id": self.run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        if decision == "PROMOTE":
            cert_run_id = (
                f"CERT-AR-{self.run_id}" if not self.run_id.startswith("CERT-")
                else self.run_id
            )
            current = load_registry(self.root)
            if current.latest_certification_run_id != cert_run_id:
                record = CertificationRecord(
                    latest_certification_run_id=cert_run_id,
                    latest_certification_commit=self.state.git_sha,
                    latest_certification_timestamp=datetime.now(timezone.utc).isoformat(),
                    latest_certification_status="YELLOW",
                    backtester_version=backtester_version(self.root),
                    warnings=[f"Autonomous-research promotion: {self.config.campaign_id}"],
                )
                save_certification_registry(record, self.root)
            registry_update["promoted_to_certification_registry"] = True
            registry_update["certification_status"] = "YELLOW"
        path = self._write_artifact("registry_update.json", registry_update)
        self._stage_end("registry_update", path)
        return path

    # --- orchestrator ---

    def run(self) -> int:
        """Execute the full pipeline. Returns 0 (PROMOTE) / 1 (REJECT) /
        2 (QUARANTINE) / 3 (infrastructure failure)."""
        if self.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED:
            logger.error("manual review required before recovery: %s", self.recovery_reason)
            return 3
        try:
            self.stage_load_config()
            self.stage_resolve_data()
            self.stage_resolve_features()
            self.stage_resolve_model_combinations()
            self.stage_generate_hypotheses()
            self.stage_experiment_specs()
            self.stage_backtest()
            self.stage_robustness_and_wf()
            self.stage_score_and_decide()
            self.stage_generate_report()
            self.stage_artifact_bundle()
            self.stage_registry_update()
            # Validate the bundle and re-write the manifest with the
            # validation result (Phase 12).
            self._finalize_bundle()
        except Exception as exc:
            logger.exception("autonomous run failed: %s", exc)
            # Persist the failure state so a re-run resumes correctly.
            self.state.last_updated_at = datetime.now(timezone.utc).isoformat()
            self._save_state()
            return 3

        scoring = json.loads(
            (self.run_dir / "scoring_summary.json").read_text(encoding="utf-8")
        )
        decision = scoring.get("decision", "QUARANTINE")
        return {"PROMOTE": 0, "REJECT": 1, "QUARANTINE": 2}.get(decision, 2)


# ---------- helpers ----------


def _hash_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _timestamp_regressed(started_at: str, last_updated_at: str) -> bool:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        updated = datetime.fromisoformat(last_updated_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return updated < started


def _gates_from_summary(scoring: dict[str, Any]) -> list[GateResult]:
    """Build a list of synthetic GateResults for aggregator callers.
    Real gates come from `robustness_gates.json`."""
    return [
        GateResult(
            gate_name="autonomous_decision",
            gate_category=GateCategory.REGISTRY_ELIGIBILITY,
            metric_name="decision",
            threshold=None,
            observed_value=None,
            comparison_operator="==",
            pass_fail=scoring.get("decision") == "PROMOTE",
            severity=Severity.BLOCKING,
            reason_code=scoring.get("decision", "QUARANTINE"),
        )
    ]


def _build_report(
    *,
    campaign_id: str,
    run_id: str,
    git_sha: str,
    started_at: str,
    decision: str,
    reason: str,
    config: CampaignConfig,
    artifacts: Dict[str, str],
) -> str:
    """Generate a markdown report with the 22 spec sections."""
    lines: list[str] = [
        f"# Autonomous Run Report — {campaign_id}",
        "",
        f"- **Run ID:** {run_id}",
        f"- **Git SHA:** {git_sha}",
        f"- **Started:** {started_at}",
        f"- **Final decision:** {decision}",
        "",
        "## 1. Run summary",
        f"Autonomous headless run for campaign `{campaign_id}`. "
        f"Decision: **{decision}**.",
        "",
        "## 2. Research input reference",
        f"- Source: `{config.research_input or 'auto (scaffolded)'}`",
        "",
        "## 3. Hypothesis",
        f"Auto-generated from `models.alpha`: {config.models.get('alpha', [])}",
        "",
        "## 4. Experiment specification",
        f"Experiments: {len(config.data.get('event_windows', []))} events × "
        f"{len(config.models.get('alpha', []))} alphas.",
        "",
        "## 5. Data used",
        f"- Dataset: `{config.data.get('dataset_id')}`",
        f"- Symbol universe: {config.data.get('symbol_universe')}",
        f"- Resolution: {config.data.get('resolution', 'L3_MBO')}",
        "",
        "## 6. Data-quality status",
        f"Requested = resolved = `{config.data.get('resolution', 'L3_MBO')}`.",
        "",
        "## 7. Feature set used",
        f"- Feature set: `{config.features.get('feature_set_id', 'core_64_v1')}`",
        "",
        "## 8. Model combination used",
        f"- Alpha: {config.models.get('alpha', [])}",
        f"- Defensives: {config.models.get('defensives', [])}",
        f"- Structurals: {config.models.get('structurals', [])}",
        "",
        "## 9. Backtest assumptions",
        f"Latency profile: {config.latency_profile}",
        "",
        "## 10. Execution assumptions",
        f"{config.latency_profile.get('fill_model', 'perfect')} fill, "
        f"{config.latency_profile.get('slippage_bps', 0.5)} bps slippage, "
        f"idealized = {config.latency_profile.get('idealized', True)}",
        "",
        "## 11. Latency assumptions",
        f"Decision-to-send {config.latency_profile.get('decision_to_send_us', 80)}us, "
        f"send-to-ack {config.latency_profile.get('send_to_ack_us', 200)}us, "
        f"ack-to-fill {config.latency_profile.get('ack_to_fill_us', 500)}us.",
        "",
        "## 12. Cost/slippage assumptions",
        f"Fees {config.latency_profile.get('fees_per_side_usd', 1.5)} USD/side, "
        f"slippage {config.latency_profile.get('slippage_bps', 0.5)} bps.",
        "",
        "## 13. Backtest results",
        f"Stub — full WorkbenchEngine integration is pending (Phase 5).",
        "",
        "## 14. Robustness results",
        f"See `robustness_gates.json` (stub gates present).",
        "",
        "## 15. Walk-forward results",
        f"See `walk_forward_results.json` and `walk_forward_correlation.json`.",
        "",
        "## 16. Walk-forward correlation results",
        f"Double-WF correlator is pending (Phase 10).",
        "",
        "## 17. Gate results",
        f"See `robustness_gates.json`.",
        "",
        "## 18. Scoring summary",
        f"See `scoring_summary.json`.",
        "",
        "## 19. Final decision",
        f"**{decision}**",
        "",
        "## 20. Reason",
        reason,
        "",
        "## 21. Artifact references",
    ]
    for stage, path in artifacts.items():
        lines.append(f"- {stage}: `{path}`")
    lines += [
        "",
        "## 22. Registry reference",
        f"See `registry_update.json` (decision: {decision}).",
        "",
    ]
    return "\n".join(lines)


# ---------- CLI ----------


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="HFT3 autonomous research runner (Phase 2)"
    )
    parser.add_argument("--config", required=True, type=Path, help="Path to campaign YAML")
    parser.add_argument("--root", type=Path, default=None, help="Repo root (default: detected)")
    parser.add_argument(
        "--run-id", default=None,
        help="Resume an existing run with this id (default: fresh)",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="DEBUG / INFO / WARNING / ERROR"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = CampaignConfig.from_yaml(args.config)
    runner = AutonomousRunner(config=config, root=args.root, run_id=args.run_id)
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
