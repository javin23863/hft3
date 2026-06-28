"""Tests for VectorBT paid screen gate and unit generation."""
from __future__ import annotations

import builtins
import json
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def test_generate_smoke_units_jsonl_count(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--smoke-count",
            "5",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert 1 <= len(lines) <= 5
    row = json.loads(lines[0])
    assert "unit_id" in row and "event_id" in row and "thesis" in row
    assert row["research_clock"] == "scheduled_event"
    assert row["context_set_id"] == "target_only"
    assert row["allowed_context_set_id"] == "target_only"
    assert row["declared_context_sets"] == ["target_only"]
    assert row["negative_control_policy"]["status"] == "not_required"


def test_generate_context_units_have_distinct_identity_and_control_policy(tmp_path: Path) -> None:
    baseline = tmp_path / "target_only.jsonl"
    context = tmp_path / "target_plus_cross_asset.jsonl"

    common_args = [
        sys.executable,
        str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
        "--smoke-count",
        "1",
        "--event-types",
        "CPI",
        "--symbols",
        "MES.v.0",
    ]
    baseline_proc = subprocess.run(
        common_args + ["--out", str(baseline)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert baseline_proc.returncode == 0, baseline_proc.stderr

    context_proc = subprocess.run(
        common_args
        + [
            "--out",
            str(context),
            "--research-clock",
            "context_feature_uplift",
            "--context-set-id",
            "target_plus_cross_asset",
            "--declared-context-sets",
            "target_only,target_plus_cross_asset",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert context_proc.returncode == 0, context_proc.stderr

    baseline_row = json.loads(baseline.read_text(encoding="utf-8").splitlines()[0])
    context_row = json.loads(context.read_text(encoding="utf-8").splitlines()[0])

    assert baseline_row["model_id"] == context_row["model_id"]
    assert baseline_row["event_id"] == context_row["event_id"]
    assert baseline_row["symbol"] == context_row["symbol"]
    assert baseline_row["unit_id"] != context_row["unit_id"]
    assert context_row["research_clock"] == "context_feature_uplift"
    assert context_row["context_set_id"] == "target_plus_cross_asset"
    assert context_row["allowed_context_set_id"] == "target_plus_cross_asset"
    assert context_row["declared_context_sets"] == ["target_only", "target_plus_cross_asset"]
    assert context_row["negative_control_policy"]["status"] == "required_before_context_claim"


def test_generator_negative_control_policy_uses_shared_default() -> None:
    from backtest_pipeline.src.paid_screen_types import default_negative_control_policy
    from scripts.generate_vbt_paid_units_jsonl import _default_negative_control_policy

    assert _default_negative_control_policy(
        "scheduled_event",
        "target_only",
    ) == default_negative_control_policy("scheduled_event", "target_only")
    assert _default_negative_control_policy(
        "context_feature_uplift",
        "target_plus_cross_asset",
    ) == default_negative_control_policy(
        "context_feature_uplift",
        "target_plus_cross_asset",
    )


def test_generate_rejects_unknown_context_set(tmp_path: Path) -> None:
    from scripts.generate_vbt_paid_units_jsonl import _normalize_context_set_id

    with pytest.raises(ValueError, match="context_set_id_invalid"):
        _normalize_context_set_id("target_plus_magic")


def test_generate_rejects_declared_context_sets_missing_current_context(tmp_path: Path) -> None:
    from scripts.generate_vbt_paid_units_jsonl import _parse_declared_context_sets

    with pytest.raises(ValueError, match="declared_context_sets_missing_context_set_id"):
        _parse_declared_context_sets("target_only", "target_plus_macro")


def test_ready_gate_fails_on_missing_pilot(tmp_path: Path) -> None:
    smoke_manifest = tmp_path / "manifest.json"
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "gate.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "validate_paid_screen_ready_gate.py"),
            "--pilot-artifact",
            str(tmp_path / "missing.json"),
            "--smoke-manifest",
            str(smoke_manifest),
            "--out",
            str(out),
            "--skip-pytest",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ready_for_full_run"] is False
    assert payload["errors"]


def test_paid_screen_dry_run_lists_units(tmp_path: Path) -> None:
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "event_id": "CPI_2024_09_11_TIGHT",
                "thesis": "t",
                "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "symbol": "MES.v.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_paid_screen.py"),
            "--units-jsonl",
            str(units),
            "--out",
            str(tmp_path / "run"),
            "--dry-run",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_next_steps_defaults_to_phase_a(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "vbt_paid_screen_next_steps.py"),
            "--json",
            "--pilot-artifact",
            str(missing),
            "--smoke-manifest",
            str(missing),
            "--gate-file",
            str(missing),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["phase"] == "A"
    assert payload["commands"]


def test_aggregate_promoted_ids(tmp_path: Path) -> None:
    unit_dir = tmp_path / "units" / "u1"
    unit_dir.mkdir(parents=True)
    artifact = {
        "promoted_ids": ["cand_a", "cand_b"],
        "candidate_ids": ["cand_a", "cand_b", "cand_c"],
    }
    (unit_dir / "screening_artifact.json").write_text(json.dumps(artifact), encoding="utf-8")
    manifest = {
        "out_dir": str(tmp_path),
        "expected_work_units": 1,
        "completed_work_units": 1,
        "failed_work_units": 0,
        "skipped_work_units": 0,
        "unit_results": [
            {
                "unit_id": "u1",
                "status": "OK",
                "screening_artifact_relpath": "units/u1/screening_artifact.json",
            }
        ],
    }
    manifest_path = tmp_path / "paid_screen_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "promoted.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "aggregate_vbt_promoted_ids.py"),
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["promoted_id_count"] == 2
    assert set(payload["promoted_ids"]) == {"cand_a", "cand_b"}


def test_fast_progress_audit_flags_zero_promo_bar_stub_sample(tmp_path: Path) -> None:
    from scripts.audit_vbt_run_progress import _scan_run_dir_fast

    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "paid_full_bad"
    units_dir = run_dir / "units"
    for idx in range(10):
        art_dir = units_dir / f"u{idx}"
        art_dir.mkdir(parents=True)
        (art_dir / "screening_artifact.json").write_text(
            json.dumps(
                {
                    "promoted_ids": [],
                    "rejected": [
                        {
                            "metric_values": {
                                "vbt_stats": {
                                    "Total Trades": 0,
                                    "Expectancy": None,
                                    "Max Drawdown [%]": None,
                                }
                            }
                        }
                    ],
                    "feature_plane_status": "bar_stub_research_only",
                    "bar_construction_id": "ohlcv_1m_from_npz_or_supplied_array",
                }
            ),
            encoding="utf-8",
        )
    (run_dir / "paid_screen_run_manifest.json").write_text(
        json.dumps(
            {
                "status": "running",
                "expected_work_units": 100,
                "completed_work_units": 10,
                "failed_work_units": 0,
                "skipped_work_units": 0,
                "workers": 4,
            }
        ),
        encoding="utf-8",
    )

    audit = _scan_run_dir_fast(run_dir)

    assert audit["sample_artifact_count"] == 10
    assert audit["artifact_audit_mode"] == "sampled"
    assert audit["artifact_audit_skipped"] is False
    assert audit["sample_promoted_ids"] == 0
    assert audit["sample_positive_trade_rows"] == 0
    assert "zero_promoted_ids_in_artifact_sample:n=10" in audit["validation_errors"]
    assert "zero_positive_trade_rows_in_artifact_sample:n=10" in audit["validation_errors"]
    assert "bar_stub_research_only_in_artifact_sample:n=10" in audit["validation_errors"]
    assert "npz_bar_fallback_in_artifact_sample:n=10" in audit["validation_errors"]


def test_progress_audit_handles_single_artifact_canary(tmp_path: Path) -> None:
    from scripts.audit_vbt_run_progress import _scan_run_dir

    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "paid_canary"
    art_dir = run_dir / "units" / "u1"
    art_dir.mkdir(parents=True)
    (art_dir / "screening_artifact.json").write_text(
        json.dumps(
            {
                "promoted_ids": [],
                "feature_set_id": "fs_v1",
                "feature_plane_status": "scheduled_event_only",
                "bar_construction_id": "fs_v1_row_loop_from_feature_store",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "paid_screen_run_manifest.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "expected_work_units": 1,
                "completed_work_units": 1,
                "failed_work_units": 0,
                "skipped_work_units": 0,
                "workers": 1,
            }
        ),
        encoding="utf-8",
    )

    audit = _scan_run_dir(run_dir)

    assert audit["artifact_files_on_disk"] == 1
    assert audit["completed_work_units"] == 1
    assert "zero_promoted_ids_in_artifacts:n=1" not in audit["validation_errors"]


def test_all_active_models_generates_multiple_hypotheses(tmp_path: Path) -> None:
    """Full-scope generator expands active registry (not single model or Stage A)."""
    from features_engine.src.hypotheses.registry import get_active_hypotheses

    n_active = len(get_active_hypotheses())
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--all-active-models",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= n_active
    model_ids = {json.loads(ln)["model_id"] for ln in lines}
    assert len(model_ids) >= 2
    row = json.loads(lines[0])
    assert row.get("hyp_id") is not None
    assert "unit_id" in row and "event_id" in row and "thesis" in row


def test_model_ids_flag_expands_explicit_models(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--model-ids",
            "HYP_5,HYP_1",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) >= 2
    model_ids = {r["model_id"] for r in rows}
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in model_ids
    assert "SECOND_WAVE_CONTINUATION" in model_ids


def test_next_steps_after_gate_points_to_vast_full(tmp_path: Path) -> None:
    pilot = tmp_path / "pilot.json"
    pilot.write_text(json.dumps({"screening_backend": "vectorbt"}), encoding="utf-8")
    smoke_manifest = tmp_path / "smoke_manifest.json"
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [],
            }
        ),
        encoding="utf-8",
    )
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"ready_for_full_run": True}), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "vbt_paid_screen_next_steps.py"),
            "--json",
            "--pilot-artifact",
            str(pilot),
            "--smoke-manifest",
            str(smoke_manifest),
            "--gate-file",
            str(gate),
            "--full-manifest",
            str(tmp_path / "missing_full.json"),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["phase"] == "D1-D4"
    joined = "\n".join(payload["commands"])
    assert "run_vbt_paid_screen_vast_full.sh" in joined
    assert "Stage-A survivor scope" in joined
    assert "VBT_UNIT_SOURCE=all_active" in joined


def test_vast_full_script_defaults_to_stage_a_survivor_scope() -> None:
    script = (REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh").read_text(encoding="utf-8")
    assert 'UNIT_SOURCE="${VBT_UNIT_SOURCE:-stage_a_survivors}"' in script
    assert "--from-stage-a-survivors" in script
    assert "VBT_STAGE_A_SURVIVORS" in script
    assert "VBT_UNIT_SOURCE=all_active" in script
    assert "--all-active-models" in script


def test_vast_full_script_pins_vectorbt_compatible_pandas() -> None:
    install_script = (REPO / "scripts" / "install_vbt_hbt_handoff_verify_deps.sh").read_text(encoding="utf-8")
    launch_script = (REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh").read_text(encoding="utf-8")

    assert '"pandas>=2.0.0,<3.0.0"' in install_script
    assert "pip3 install 'vectorbt[rust]==1.0.0' 'pandas>=2.0.0,<3.0.0' -q" in launch_script
    assert "VectorBT dependency check:" in launch_script
    assert "expected pandas<3.0" in launch_script


def test_vast_launchers_do_not_auto_prefer_forensic_ready_gate() -> None:
    launch_script = (REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh").read_text(encoding="utf-8")
    ssh_script = (REPO / "scripts" / "vast_ssh_run_vbt_paid_screen.sh").read_text(encoding="utf-8")

    assert "paid_screen_ready_gate_after_forensic_probe" not in launch_script
    assert "paid_screen_ready_gate_after_forensic_probe" not in ssh_script
    assert 'GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"' in launch_script
    assert 'VBT_READY_GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"' in ssh_script
    assert "Validating ready gate provenance" in launch_script
    assert '"events_csv_hash", events_hash' in launch_script
    assert '"lake_manifest_hash", lake_hash' in launch_script
    assert "ready gate {name} missing" in launch_script
    assert "Ready gate OK:" in launch_script
    assert 'VBT_MAX_UNITS_PER_BATCH="${VBT_MAX_UNITS_PER_BATCH-0}"' in ssh_script
    assert '[[ ! "$VBT_MAX_UNITS_PER_BATCH" =~ ^[0-9]+$ ]]' in ssh_script
    assert "ERROR: VBT_MAX_UNITS_PER_BATCH must be a non-negative integer" in ssh_script
    assert 'printf -v REMOTE_VBT_MAX_UNITS_PER_BATCH "%q" "$VBT_MAX_UNITS_PER_BATCH"' in ssh_script
    assert "VBT_READY_GATE_FILE=$VBT_READY_GATE_FILE VBT_MAX_UNITS_PER_BATCH=$REMOTE_VBT_MAX_UNITS_PER_BATCH bash scripts/run_vbt_paid_screen_vast_full.sh" in ssh_script


def test_phase_b_smoke_does_not_depend_on_full_run_ready_gate() -> None:
    smoke_script = (REPO / "scripts" / "run_vbt_paid_screen_smoke.sh").read_text(encoding="utf-8")

    assert "--owner-waiver \"phase_b_smoke_before_ready_gate\"" in smoke_script
    assert "--ready-gate-file runtime/reports/paid_screen_ready_gate.json" not in smoke_script


def test_stage_a_survivors_expansion_not_capped_at_fifty(tmp_path: Path) -> None:
    """Full scope uses all TIGHT events per cell — not [:50] and not CPI+NFP-only smoke."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--from-stage-a-survivors",
            str(survivors),
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # CPI TIGHT MES catalog has >50 events; old [:50] cap would yield exactly 50
    assert len(lines) > 50
    row = json.loads(lines[0])
    assert row["model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert row.get("hyp_id") == 5
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in row["thesis"]

    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis(row["thesis"], use_llm=False)
    assert parsed.primary_model_id == row["model_id"]


def test_stage_a_survivors_respects_split_and_event_type_filters(tmp_path: Path) -> None:
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [
                    {"hyp_id": 5, "event_type": "CPI"},
                    {"hyp_id": 5, "event_type": "NFP"},
                ],
                "pass_through": [],
                "tested_cells": [
                    {"hyp_id": 5, "event_type": "CPI"},
                    {"hyp_id": 5, "event_type": "NFP"},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--from-stage-a-survivors",
            str(survivors),
            "--research-split",
            "holdout",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "5",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows
    assert {row["event_type"] for row in rows} == {"CPI"}
    assert {row.get("research_split") for row in rows} == {"holdout"}
    years = {int(row["event_id"].split("_")[1]) for row in rows}
    assert years.issubset({2023, 2024})


def test_require_runnable_npz_uses_manifest_without_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A usable manifest is authoritative enough to avoid the slow per-unit resolver."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    npz_file = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(npz_file, data=np.arange(3, dtype=np.int64))
    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "symbol": "MES",
                    "event_id": "CPI_2020_01_15_TIGHT",
                    "npz_path": npz_file.name,
                    "event_count": 3,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.delenv("HFT3_MANIFEST_PATH", raising=False)

    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "backtest_pipeline.src.vectorbt_adapter":
            raise AssertionError("manifest-backed filter should not import VectorBT fallback")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    units = [
        {"unit_id": "keep", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"},
        {"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_02_15_TIGHT"},
    ]

    kept = generator._filter_runnable_npz_units(units, REPO)

    assert [unit["unit_id"] for unit in kept] == ["keep"]


def test_require_runnable_npz_uses_lake_manifest_parquet_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HFT3_MANIFEST_PATH is honored when it points at the same lake."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.parquet"
    manifest.write_bytes(b"placeholder")
    npz_file = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(npz_file, data=np.arange(3, dtype=np.int64))
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))
    monkeypatch.setattr(
        generator,
        "_read_manifest_parquet_records",
        lambda _path: [
            {
                "symbol": "MES",
                "event_id": "CPI_2020_01_15_TIGHT",
                "npz_path": npz_file.name,
                "event_count": 3,
            }
        ],
    )

    kept = generator._filter_runnable_npz_units(
        [
            {"unit_id": "keep", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"},
            {"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_02_15_TIGHT"},
        ],
        REPO,
    )

    assert [unit["unit_id"] for unit in kept] == ["keep"]


def test_require_runnable_npz_rejects_manifest_outside_lake_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-provided HFT3_MANIFEST_PATH must not be silently ignored."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    external_manifest = tmp_path / "cross_mount" / "manifest.parquet"
    external_manifest.parent.mkdir()
    external_manifest.write_bytes(b"placeholder")
    stray = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(stray, data=np.arange(3, dtype=np.int64))
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(external_manifest))

    with pytest.raises(RuntimeError, match="HFT3_MANIFEST_PATH rejected"):
        generator._filter_runnable_npz_units(
            [{"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_rejects_unsupported_explicit_manifest_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit HFT3_MANIFEST_PATH must be a supported sole authority."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.txt"
    manifest.write_text("not-json\n", encoding="utf-8")
    stray = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(stray, data=np.arange(3, dtype=np.int64))
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))

    with pytest.raises(RuntimeError, match="unsupported manifest suffix"):
        generator._filter_runnable_npz_units(
            [{"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_derives_npz_from_raw_parquet_output_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parquet output_path may point at raw DBN; runnable NPZ is derived under HFT3_NPZ_ROOT."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.parquet"
    manifest.write_bytes(b"placeholder")
    npz_file = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(npz_file, data=np.arange(3, dtype=np.int64))
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))
    monkeypatch.setattr(
        generator,
        "_read_manifest_parquet_records",
        lambda _path: [
            {
                "symbol": "MES",
                "event_id": "CPI_2020_01_15_TIGHT",
                "output_path": "mbo_release/CPI_2020_01_15_TIGHT/MES.v.0/raw.dbn.zst",
            }
        ],
    )

    kept = generator._filter_runnable_npz_units(
        [{"unit_id": "keep", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
        REPO,
    )

    assert [unit["unit_id"] for unit in kept] == ["keep"]


def test_require_runnable_npz_drops_zero_row_npz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Present but empty NPZ files are not runnable for paid-compute units."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.parquet"
    manifest.write_bytes(b"placeholder")
    empty_npz = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    full_npz = npz_root / "ES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(empty_npz, data=np.array([], dtype=np.int64))
    np.savez(full_npz, data=np.arange(3, dtype=np.int64))
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))
    monkeypatch.setattr(
        generator,
        "_read_manifest_parquet_records",
        lambda _path: [
            {"symbol": "MES", "event_id": "CPI_2020_01_15_TIGHT", "npz_path": empty_npz.name},
            {"symbol": "ES", "event_id": "CPI_2020_01_15_TIGHT", "npz_path": full_npz.name},
        ],
    )

    kept = generator._filter_runnable_npz_units(
        [
            {"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"},
            {"unit_id": "keep", "symbol": "ES.v.0", "event_id": "CPI_2020_01_15_TIGHT"},
        ],
        REPO,
    )

    assert [unit["unit_id"] for unit in kept] == ["keep"]


def _synthetic_npy_member(
    *,
    version: tuple[int, int] = (1, 0),
    descr: str = "<i8",
    shape: tuple[int, ...] = (3,),
    payload: bytes = b"",
    fortran_order: object = False,
    extra_header: dict[str, object] | None = None,
) -> bytes:
    encoding = "latin1" if version[0] < 3 else "utf-8"
    header_payload = {"descr": descr, "fortran_order": fortran_order, "shape": shape}
    if extra_header:
        header_payload.update(extra_header)
    header = repr(header_payload) + "\n"
    raw_header = header.encode(encoding)
    raw_len = (
        struct.pack("<H", len(raw_header))
        if version == (1, 0)
        else struct.pack("<I", len(raw_header))
    )
    return b"\x93NUMPY" + bytes(version) + raw_len + raw_header + payload


def _write_npz_member(path: Path, name: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name, payload)


def test_runnable_npz_row_check_accepts_data_member(tmp_path: Path) -> None:
    """Nonempty data NPZ files written by np.savez are runnable."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_file = tmp_path / "data_member.npz"
    np.savez(npz_file, data=np.arange(3, dtype=np.int64))

    assert generator._npz_has_rows(npz_file)


def test_runnable_npz_row_check_accepts_quotes_member(tmp_path: Path) -> None:
    """Nonempty quotes-only NPZ files are runnable."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_file = tmp_path / "quotes_only.npz"
    np.savez(npz_file, quotes=np.arange(3, dtype=np.int64))

    assert generator._npz_has_rows(npz_file)


def test_runnable_npz_row_check_rejects_truncated_positive_shape(tmp_path: Path) -> None:
    """Positive row count is not runnable unless the member has the declared payload bytes."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_file = tmp_path / "truncated.npz"
    _write_npz_member(
        npz_file,
        "data.npy",
        _synthetic_npy_member(shape=(3,), payload=b"\0" * 8),
    )

    assert not generator._npz_has_rows(npz_file)


def test_runnable_npz_row_check_rejects_negative_dim(tmp_path: Path) -> None:
    """Malformed negative dimensions fail closed."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_file = tmp_path / "negative_dim.npz"
    _write_npz_member(
        npz_file,
        "data.npy",
        _synthetic_npy_member(shape=(-1,), payload=b"\0" * 32),
    )

    assert not generator._npz_has_rows(npz_file)


def test_runnable_npz_row_check_rejects_unsupported_npy_version(tmp_path: Path) -> None:
    """Only exact NPY versions 1.0, 2.0, and 3.0 are accepted."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_file = tmp_path / "version_3_1.npz"
    _write_npz_member(
        npz_file,
        "data.npy",
        _synthetic_npy_member(version=(3, 1), payload=b"\0" * 24),
    )

    assert not generator._npz_has_rows(npz_file)


def test_runnable_npz_row_check_rejects_object_descr(tmp_path: Path) -> None:
    """Object dtype members fail closed because stdlib cannot prove payload item size safely."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_file = tmp_path / "object_dtype.npz"
    _write_npz_member(
        npz_file,
        "data.npy",
        _synthetic_npy_member(descr="|O", payload=b"\0" * 24),
    )

    assert not generator._npz_has_rows(npz_file)


def test_runnable_npz_row_check_rejects_malformed_header_contract(tmp_path: Path) -> None:
    """NPY headers must have the expected keys and a boolean fortran_order."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    bad_order = tmp_path / "bad_order.npz"
    _write_npz_member(
        bad_order,
        "data.npy",
        _synthetic_npy_member(fortran_order="False", payload=b"\0" * 24),
    )
    extra_key = tmp_path / "extra_key.npz"
    _write_npz_member(
        extra_key,
        "data.npy",
        _synthetic_npy_member(extra_header={"unexpected": True}, payload=b"\0" * 24),
    )

    assert not generator._npz_has_rows(bad_order)
    assert not generator._npz_has_rows(extra_key)


def test_runnable_npz_row_check_rejects_invalid_dtype_grammar(tmp_path: Path) -> None:
    """Ambiguous or malformed dtype descriptors fail closed."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    bad_descr = tmp_path / "bad_descr.npz"
    _write_npz_member(
        bad_descr,
        "data.npy",
        _synthetic_npy_member(descr="<i8[bogus]", payload=b"\0" * 24),
    )

    assert not generator._npz_has_rows(bad_descr)


def test_runnable_npz_row_check_warns_on_unexpected_zip_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected inspection errors warn and still fail closed."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    def raise_zip_error(*_args: object, **_kwargs: object) -> object:
        raise OSError("zip open failed")

    monkeypatch.setattr(generator.zipfile, "ZipFile", raise_zip_error)

    with pytest.warns(RuntimeWarning, match="zip open failed"):
        assert not generator._npz_has_rows(tmp_path / "broken.npz")


def test_require_runnable_npz_manifest_authority_blocks_glob_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present lake manifest with no runnable NPZ rows must not fall back to stray files."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.parquet"
    manifest.write_bytes(b"placeholder")
    stray = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(stray, data=np.arange(3, dtype=np.int64))
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))
    monkeypatch.setattr(
        generator,
        "_read_manifest_parquet_records",
        lambda _path: [
            {
                "symbol": "MES",
                "event_id": "CPI_2020_02_15_TIGHT",
                "output_path": "mbo_release/CPI_2020_02_15_TIGHT/MES.v.0/raw.dbn.zst",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="manifest authority yielded no valid keys"):
        generator._filter_runnable_npz_units(
            [{"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_parquet_authority_empty_records_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty parquet authority must fail closed instead of silently zeroing units."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.parquet"
    manifest.write_bytes(b"placeholder")
    stray = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(stray, data=np.arange(3, dtype=np.int64))
    (npz_root / "manifest.json").write_text(
        json.dumps([{"symbol": "MES", "event_id": "CPI_2020_01_15_TIGHT", "npz_path": stray.name}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))
    monkeypatch.setattr(generator, "_read_manifest_parquet_records", lambda _path: [])

    with pytest.raises(RuntimeError, match="parquet manifest yielded no records"):
        generator._filter_runnable_npz_units(
            [{"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_event_filter_empty_parquet_authority_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Event filtering must not convert empty parquet authority into a zero-unit run."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.parquet"
    manifest.write_bytes(b"placeholder")
    stray = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(stray, data=np.arange(3, dtype=np.int64))
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))
    monkeypatch.setattr(generator, "_read_manifest_parquet_records", lambda _path: [])

    with pytest.raises(RuntimeError, match="parquet manifest yielded no records"):
        generator._filter_events_to_runnable_npz(
            [{"symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_missing_parquet_manifest_fails_distinctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing parquet authority must report the missing file, not empty records."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    manifest = npz_root / "manifest.parquet"
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))

    with pytest.raises(RuntimeError, match="parquet manifest file is missing"):
        generator._filter_runnable_npz_units(
            [{"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_missing_json_manifest_fails_distinctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit missing JSON manifest authority must not fall back to stray NPZ files."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    stray = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(stray, data=np.arange(3, dtype=np.int64))
    manifest = npz_root / "manifest.json"
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(manifest))

    with pytest.raises(RuntimeError, match="JSON manifest file is missing"):
        generator._filter_runnable_npz_units(
            [{"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_cli_reports_manifest_error(tmp_path: Path) -> None:
    """CLI should print ERROR and exit 1 instead of a raw traceback."""
    out = tmp_path / "units.jsonl"
    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    env = os.environ.copy()
    env["HFT3_NPZ_ROOT"] = str(npz_root)
    env["HFT3_MANIFEST_PATH"] = str(npz_root / "manifest.parquet")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), str(REPO / "packages"), str(REPO / "apps"), env.get("PYTHONPATH", "")]
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--all-active-models",
            "--require-runnable-npz",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 1
    assert "ERROR: runnable NPZ event filter failed:" in proc.stderr
    assert "runnable NPZ parquet manifest file is missing" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_stage_a_require_runnable_npz_cli_reports_manifest_error(tmp_path: Path) -> None:
    """Stage-A survivor generation must use the same clean manifest-error path."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    env = os.environ.copy()
    env["HFT3_NPZ_ROOT"] = str(npz_root)
    env["HFT3_MANIFEST_PATH"] = str(npz_root / "manifest.parquet")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), str(REPO / "packages"), str(REPO / "apps"), env.get("PYTHONPATH", "")]
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--require-runnable-npz",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 1
    assert "ERROR: runnable NPZ unit filter failed:" in proc.stderr
    assert "runnable NPZ parquet manifest file is missing" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_require_runnable_npz_caps_after_filter(tmp_path: Path) -> None:
    """Stage-A max-units caps surviving runnable units, not pre-filter rows."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    events_csv = tmp_path / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,"
        "effective_date,notes,row_status\n"
        "CPI_2020_01_15_TIGHT,CPI,2020-01-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-01-01,missing npz,SOURCED\n"
        "CPI_2020_02_15_TIGHT,CPI,2020-02-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-02-01,runnable npz,SOURCED\n"
        "CPI_2020_03_15_TIGHT,CPI,2020-03-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-03-01,second runnable npz,SOURCED\n",
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    npz_file = npz_root / "MES_CPI_2020_02_15_TIGHT_mbo.npz"
    second_npz_file = npz_root / "MES_CPI_2020_03_15_TIGHT_mbo.npz"
    np.savez(npz_file, data=np.arange(3, dtype=np.int64))
    np.savez(second_npz_file, data=np.arange(3, dtype=np.int64))
    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "symbol": "MES",
                    "event_id": "CPI_2020_02_15_TIGHT",
                    "npz_path": npz_file.name,
                    "event_count": 3,
                },
                {
                    "symbol": "MES",
                    "event_id": "CPI_2020_03_15_TIGHT",
                    "npz_path": second_npz_file.name,
                    "event_count": 3,
                }
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HFT3_NPZ_ROOT"] = str(npz_root)
    env.pop("HFT3_MANIFEST_PATH", None)

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--events-csv",
            str(events_csv),
            "--from-stage-a-survivors",
            str(survivors),
            "--require-runnable-npz",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 1
    assert [row["event_id"] for row in rows] == ["CPI_2020_02_15_TIGHT"]


def test_stage_a_survivors_cli_reports_malformed_json(tmp_path: Path) -> None:
    """Malformed Stage-A survivor JSON should print ERROR instead of a traceback."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text("{not-json}\n", encoding="utf-8")
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR:" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_reports_non_object_json(tmp_path: Path) -> None:
    """Non-object Stage-A survivor JSON should print ERROR instead of a traceback."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text("[]\n", encoding="utf-8")
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: expected JSON object" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    ("missing_field", "payload"),
    [
        (
            "survivors",
            {
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            },
        ),
        (
            "pass_through",
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            },
        ),
        (
            "tested_cells",
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
            },
        ),
    ],
)
def test_stage_a_survivors_cli_rejects_missing_top_level_keys(
    tmp_path: Path,
    missing_field: str,
    payload: dict,
) -> None:
    """Stage-A survivor authority keys must be explicit, even when empty."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert f"ERROR: stage_a_survivors.json: missing required top-level fields: {missing_field}" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_reports_unknown_hyp_id(tmp_path: Path) -> None:
    """Unknown Stage-A hyp IDs should print ERROR instead of a traceback."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 999999, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 999999, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: unknown hyp_id 999999" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_allows_deprecated_tested_cell_hyp_id(tmp_path: Path) -> None:
    """tested_cells supplies event types; only survivor/pass-through IDs expand into units."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 999999, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["hyp_id"] == 5
    assert rows[0]["event_type"] == "CPI"


def test_stage_a_survivors_cli_reports_bad_nested_schema(tmp_path: Path) -> None:
    """Malformed nested Stage-A fields should print ERROR instead of a traceback."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": {"hyp_id": 5, "event_type": "CPI"},
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: survivors must be a list" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_rejects_bool_hyp_id(tmp_path: Path) -> None:
    """Bool hyp IDs should not coerce to 0/1."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": True, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: invalid survivor hyp_id" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_rejects_falsey_bad_nested_schema(tmp_path: Path) -> None:
    """Falsey malformed Stage-A containers must not be masked as missing."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": {},
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: survivors must be a list" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_rejects_bad_tested_cells_rows(tmp_path: Path) -> None:
    """Malformed tested_cells rows should not be ignored."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": ["CPI"],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: tested_cells rows must be objects" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_rejects_missing_tested_cell_fields(tmp_path: Path) -> None:
    """Object tested_cells rows still need the canonical hyp_id/event_type keys."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: tested_cells rows require hyp_id and event_type" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_rejects_missing_survivor_fields(tmp_path: Path) -> None:
    """Object survivor rows still need the canonical hyp_id/event_type keys."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: survivor rows require hyp_id and event_type" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_rejects_bad_pass_through_entries(tmp_path: Path) -> None:
    """Malformed pass-through entries should not be skipped."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [False],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: invalid pass_through entry" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_survivors_cli_reports_unknown_pass_through_hyp_id(tmp_path: Path) -> None:
    """Pass-through hyp IDs should validate against the same registry as survivors."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [999999],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "ERROR: stage_a_survivors.json: unknown hyp_id 999999" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_all_active_require_runnable_npz_cli_reports_no_valid_manifest_keys(tmp_path: Path) -> None:
    """All-active generation should report manifest authority failures cleanly."""
    out = tmp_path / "units.jsonl"
    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "symbol": "MES.v.0",
                    "event_id": "CPI_2020_01_15_TIGHT",
                    "npz_path": "MES.v.0_CPI_2020_01_15_TIGHT_mbo.npz",
                }
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HFT3_NPZ_ROOT"] = str(npz_root)
    env.pop("HFT3_MANIFEST_PATH", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), str(REPO / "packages"), str(REPO / "apps"), env.get("PYTHONPATH", "")]
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--all-active-models",
            "--require-runnable-npz",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 1
    assert "ERROR: runnable NPZ event filter failed:" in proc.stderr
    assert "runnable NPZ manifest authority yielded no valid keys" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_stage_a_require_runnable_npz_cli_reports_no_valid_manifest_keys(tmp_path: Path) -> None:
    """Stage-A survivor generation should report manifest authority failures cleanly."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "symbol": "MES.v.0",
                    "event_id": "CPI_2020_01_15_TIGHT",
                    "npz_path": "MES.v.0_CPI_2020_01_15_TIGHT_mbo.npz",
                }
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HFT3_NPZ_ROOT"] = str(npz_root)
    env.pop("HFT3_MANIFEST_PATH", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), str(REPO / "packages"), str(REPO / "apps"), env.get("PYTHONPATH", "")]
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--from-stage-a-survivors",
            str(survivors),
            "--require-runnable-npz",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 1
    assert "ERROR: runnable NPZ unit filter failed:" in proc.stderr
    assert "runnable NPZ manifest authority yielded no valid keys" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_require_runnable_npz_cli_reports_malformed_manifest_json(tmp_path: Path) -> None:
    """Malformed JSON manifest should report ERROR instead of a traceback."""
    out = tmp_path / "units.jsonl"
    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    (npz_root / "manifest.json").write_text("{not-json}\n", encoding="utf-8")
    env = os.environ.copy()
    env["HFT3_NPZ_ROOT"] = str(npz_root)
    env.pop("HFT3_MANIFEST_PATH", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), str(REPO / "packages"), str(REPO / "apps"), env.get("PYTHONPATH", "")]
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--all-active-models",
            "--require-runnable-npz",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "1",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 1
    assert "ERROR: runnable NPZ event filter failed:" in proc.stderr
    assert "runnable NPZ JSON manifest is unreadable" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not out.exists()


def test_require_runnable_npz_unrecognized_manifest_json_blocks_glob_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing manifest.json with unknown schema is still manifest authority."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    stray = npz_root / "MES_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(stray, data=np.arange(3, dtype=np.int64))
    (npz_root / "manifest.json").write_text(
        json.dumps({"unexpected": "schema"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.delenv("HFT3_MANIFEST_PATH", raising=False)

    with pytest.raises(RuntimeError, match="unrecognized schema"):
        generator._filter_runnable_npz_units(
            [{"unit_id": "drop", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
            REPO,
        )


def test_require_runnable_npz_parquet_missing_pandas_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing pandas must not make parquet manifest authority silently empty."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    manifest = tmp_path / "manifest.parquet"
    manifest.write_bytes(b"placeholder")
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pandas":
            raise ImportError("no pandas")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(RuntimeError, match="pandas is required"):
        generator._read_manifest_parquet_records(manifest)


def test_require_runnable_npz_corrupt_parquet_manifest_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corrupt parquet authority must report a manifest RuntimeError."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    manifest = tmp_path / "manifest.parquet"
    manifest.write_bytes(b"not-a-parquet-file")
    pd = pytest.importorskip("pandas")
    assert pd is not None

    with pytest.raises(RuntimeError, match="parquet manifest is unreadable"):
        generator._read_manifest_parquet_records(manifest)


def test_require_runnable_npz_accepts_fixture_manifest_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixture-style manifests with a files list are parsed instead of crashing."""
    import scripts.generate_vbt_paid_units_jsonl as generator

    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    npz_file = npz_root / "MES.v.0_CPI_2020_01_15_TIGHT_mbo.npz"
    np.savez(npz_file, data=np.arange(3, dtype=np.int64))
    (npz_root / "manifest.json").write_text(
        json.dumps({"seed": 1, "files": [npz_file.name]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_root))
    monkeypatch.delenv("HFT3_MANIFEST_PATH", raising=False)

    kept = generator._filter_runnable_npz_units(
        [{"unit_id": "keep", "symbol": "MES.v.0", "event_id": "CPI_2020_01_15_TIGHT"}],
        REPO,
    )

    assert [unit["unit_id"] for unit in kept] == ["keep"]


def test_generated_thesis_round_trips_hyp_ids_1_to_50(tmp_path: Path) -> None:
    """Every Stage-A-style thesis must parse back to the same canonical slug."""
    from features_engine.src.model_registry import get_slug_for_hyp_id
    from research_pipeline.hypothesis_parser import parse_hypothesis

    for hyp_id in range(1, 51):
        slug = get_slug_for_hyp_id(hyp_id)
        thesis = (
            f"Display event-window strategy ({slug}) on CPI release for MES.v.0 "
            f"event CPI_2024_09_11_TIGHT"
        )
        parsed = parse_hypothesis(thesis, use_llm=False)
        assert parsed.primary_model_id == slug, f"hyp_id={hyp_id} got {parsed.primary_model_id}"


def test_paid_screen_refuses_high_workers_without_gate(tmp_path: Path) -> None:
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "event_id": "CPI_2024_09_11_TIGHT",
                "thesis": "t",
                "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "symbol": "MES.v.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_paid_screen.py"),
            "--units-jsonl",
            str(units),
            "--out",
            str(tmp_path / "run"),
            "--workers",
            "32",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_single_model_id_hyp_5_resolves_canonical_slug(tmp_path: Path) -> None:
    events_csv = tmp_path / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,"
        "effective_date,notes,row_status\n"
        "CPI_2020_01_15_TIGHT,CPI,2020-01-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-01-01,test,SOURCED\n",
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--events-csv",
            str(events_csv),
            "--model-id",
            "HYP_5",
            "--symbols",
            "MES.v.0",
            "--event-types",
            "CPI",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert row.get("hyp_id") == 5
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in row["thesis"]


@pytest.mark.parametrize(
    "cap_args",
    [
        ["--smoke-count", "1"],
        ["--max-units", "1"],
        ["--smoke-count", "2", "--max-units", "1"],
        ["--smoke-count", "1", "--max-units", "2"],
    ],
    ids=["smoke-count", "max-units", "combined-max", "combined-smoke"],
)
def test_single_model_require_runnable_npz_caps_after_filter(
    tmp_path: Path,
    cap_args: list[str],
) -> None:
    events_csv = tmp_path / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,"
        "effective_date,notes,row_status\n"
        "CPI_2020_01_15_TIGHT,CPI,2020-01-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-01-01,missing npz,SOURCED\n"
        "CPI_2020_02_15_TIGHT,CPI,2020-02-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-02-01,runnable npz,SOURCED\n"
        "CPI_2020_03_15_TIGHT,CPI,2020-03-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-03-01,second runnable npz,SOURCED\n",
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    npz_root = tmp_path / "npz"
    npz_root.mkdir()
    npz_file = npz_root / "MES_CPI_2020_02_15_TIGHT_mbo.npz"
    second_npz_file = npz_root / "MES_CPI_2020_03_15_TIGHT_mbo.npz"
    np.savez(npz_file, data=np.arange(3, dtype=np.int64))
    np.savez(second_npz_file, data=np.arange(3, dtype=np.int64))
    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "symbol": "MES",
                    "event_id": "CPI_2020_02_15_TIGHT",
                    "npz_path": npz_file.name,
                    "event_count": 3,
                },
                {
                    "symbol": "MES",
                    "event_id": "CPI_2020_03_15_TIGHT",
                    "npz_path": second_npz_file.name,
                    "event_count": 3,
                }
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HFT3_NPZ_ROOT"] = str(npz_root)
    env.pop("HFT3_MANIFEST_PATH", None)

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--events-csv",
            str(events_csv),
            "--model-id",
            "HYP_5",
            "--symbols",
            "MES.v.0",
            "--event-types",
            "CPI",
            "--require-runnable-npz",
            *cap_args,
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 1
    assert [row["event_id"] for row in rows] == ["CPI_2020_02_15_TIGHT"]


def test_multi_symbol_expansion_emits_all_matching_symbols(tmp_path: Path) -> None:
    events_csv = tmp_path / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,"
        "effective_date,notes,row_status\n"
        "CPI_2020_01_15_TIGHT,CPI,2020-01-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,\"MES.v.0,MNQ.v.0\",50,CPI,https://example.com/,2020-01-01,test,SOURCED\n",
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--events-csv",
            str(events_csv),
            "--model-id",
            "HYP_5",
            "--symbols",
            "MES.v.0,MNQ.v.0",
            "--event-types",
            "CPI",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"MES.v.0", "MNQ.v.0"}
    assert len(rows) == 2


def test_all_active_default_excludes_holdout_events(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--all-active-models",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows
    assert all(r.get("research_split") == "discovery_confirmation" for r in rows)
    years = {int(r["event_id"].split("_")[1]) for r in rows if r["event_id"].startswith("CPI_")}
    assert max(years) <= 2022
    assert not any(y >= 2023 for y in years)


def test_all_active_holdout_split_includes_holdout_when_explicit(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--all-active-models",
            "--research-split",
            "holdout",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "5",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows
    assert all(r.get("research_split") == "holdout" for r in rows)
    years = {int(r["event_id"].split("_")[1]) for r in rows if r["event_id"].startswith("CPI_")}
    assert years.issubset({2023, 2024})
    assert min(years) >= 2023


def test_vast_full_script_requires_declaration_before_workers() -> None:
    script = (REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh").read_text(encoding="utf-8")
    assert "vbt_full_run_declaration.json" in script
    assert 'RESEARCH_SPLIT="${VBT_RESEARCH_SPLIT:-discovery_confirmation}"' in script
    assert 'if [[ -n "$RESEARCH_SPLIT" ]]' in script
    assert "stage_a_survivors_cme_m6_runnable_npz" in script
    assert "expected_work_units" in script
    assert "DECL_EXPECTED" in script
    assert "ERROR: Full-run declaration missing" in script
    assert "ERROR: Declaration expected_work_units=" in script
    assert "ERROR: Declaration mismatch:" in script
    assert 'STALL_MINUTES="${VBT_STALL_MINUTES:-30}"' in script
    assert 'BATCH_TIMEOUT_SECONDS="${VBT_BATCH_TIMEOUT_SECONDS:-1800}"' in script
    assert '[[ ! "$BATCH_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]' in script
    assert "(( BATCH_TIMEOUT_SECONDS < 1 ))" in script
    assert "ERROR: VBT_BATCH_TIMEOUT_SECONDS must be a positive integer" in script
    assert "got '$BATCH_TIMEOUT_SECONDS'" in script
    assert 'REQUESTED_VBT_MAX_UNITS_PER_BATCH_SET="${VBT_MAX_UNITS_PER_BATCH+x}"' in script
    assert 'REQUESTED_VBT_MAX_UNITS_PER_BATCH="${VBT_MAX_UNITS_PER_BATCH:-}"' in script
    assert 'VBT_MAX_UNITS_PER_BATCH="$REQUESTED_VBT_MAX_UNITS_PER_BATCH"' in script
    assert 'MAX_UNITS_PER_BATCH="${VBT_MAX_UNITS_PER_BATCH-0}"' in script
    assert '[[ ! "$MAX_UNITS_PER_BATCH" =~ ^[0-9]+$ ]]' in script
    assert "ERROR: VBT_MAX_UNITS_PER_BATCH must be a non-negative integer" in script
    assert "got '$MAX_UNITS_PER_BATCH'" in script
    assert '"stall_minutes": int(stall_minutes)' in script
    assert '"batch_timeout_seconds": int(batch_timeout_seconds)' in script
    assert '"max_units_per_batch": int(max_units_per_batch)' in script
    assert 'DECL_ABORT_ON_FAILED_UNITS="${VBT_DECL_ABORT_ON_FAILED_UNITS:-true}"' in script
    assert 'payload.get("abort_on_failed_units") is not True' in script
    assert "abort_on_failed_units must be true;" in script
    assert 'expect_int("stall_minutes", int(stall_minutes))' in script
    assert 'expect_int("batch_timeout_seconds", int(batch_timeout_seconds))' in script
    assert 'expect_int("max_units_per_batch", int(max_units_per_batch))' in script
    assert 'expect_int("stall_minutes", 30)' not in script
    assert 'expect_int("batch_timeout_seconds", 1800)' not in script
    assert "VBT_WRITE_DECLARATION_TEMPLATE" in script
    assert "Wrote declaration template:" in script
    assert "to regenerate the declaration template" in script
    assert "Declaration verified:" in script
    assert "--batch-timeout-seconds $BATCH_TIMEOUT_SECONDS" in script
    assert "--max-units-per-batch $MAX_UNITS_PER_BATCH" in script
    assert "--abort-on-failed-units" in script
    assert "--research-split" in script


def test_vast_ssh_script_uses_current_branch_not_hardcoded_main() -> None:
    script = (REPO / "scripts" / "vast_ssh_run_vbt_paid_screen.sh").read_text(encoding="utf-8")
    assert "VBT_GIT_BRANCH:-main" not in script
    assert "git branch --show-current" in script
    assert "detached HEAD" in script


def test_vast_ssh_script_supports_separate_host_and_port() -> None:
    script = (REPO / "scripts" / "vast_ssh_run_vbt_paid_screen.sh").read_text(encoding="utf-8")
    assert "VAST_SSH_HOST_ARG" in script
    assert "VAST_SSH_PORT" in script
    assert 'SSH_OPTS+=(-p "$VAST_SSH_PORT")' in script
    assert 'SCP_OPTS+=(-P "$VAST_SSH_PORT")' in script
    assert "do not embed -p in VAST_SSH_TARGET" in script
    assert '"$VAST_SSH_HOST_ARG"' in script
    assert "VAST_SSH_TARGET" in script


def test_next_steps_includes_declaration_before_full_run(tmp_path: Path) -> None:
    from scripts.vbt_paid_screen_next_steps import _phase

    pilot = tmp_path / "pilot.json"
    pilot.write_text(json.dumps({"screening_backend": "vectorbt"}), encoding="utf-8")
    smoke_manifest = tmp_path / "smoke_manifest.json"
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [],
            }
        ),
        encoding="utf-8",
    )
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"ready_for_full_run": True}), encoding="utf-8")
    missing_decl = tmp_path / "missing_decl.json"
    missing_full = tmp_path / "missing_full.json"

    phase, commands = _phase(
        {
            "pilot": pilot,
            "smoke": smoke_manifest,
            "gate": gate,
            "full": missing_full,
            "full_units": tmp_path / "units.jsonl",
            "decl": missing_decl,
        }
    )
    assert phase == "D1-D4"
    joined = "\n".join(commands)
    assert "Phase D0" in joined
    assert "vbt_full_run_declaration.json" in joined
    assert "run_vbt_paid_screen_vast_full.sh" in joined
    assert joined.index("vbt_full_run_declaration.json") < joined.index("run_vbt_paid_screen_vast_full.sh")
