"""Tests for VectorBT paid screen gate and unit generation."""
from __future__ import annotations

import builtins
import json
import subprocess
import sys
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
    assert "VBT_WRITE_DECLARATION_TEMPLATE" in script
    assert "Wrote declaration template:" in script
    assert "to regenerate the declaration template" in script
    assert "Declaration verified:" in script
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
