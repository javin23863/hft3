"""Fresh all-lane cleanup safety tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.run.fresh_start import FreshStartError, fresh_start


def _tracked(paths: set[str]):
    def inner(_repo: Path) -> set[str]:
        return set(paths)

    return inner


def test_fresh_start_refuses_without_confirm(tmp_path: Path) -> None:
    with pytest.raises(FreshStartError, match="confirm_hard_delete"):
        fresh_start(tmp_path, confirm_hard_delete=False, tracked_paths_fn=_tracked(set()))


def test_fresh_start_deletes_generated_artifacts_and_writes_active_run(tmp_path: Path) -> None:
    old_file = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "latest_status.json"
    old_file.parent.mkdir(parents=True)
    old_file.write_text('{"run_id":"old"}', encoding="utf-8")
    source_data = tmp_path / "data" / "source.ndjson"
    source_data.parent.mkdir(parents=True)
    source_data.write_text("keep\n", encoding="utf-8")
    baseline = tmp_path / "reports" / "latency_baselines" / "current_baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("{}", encoding="utf-8")

    result = fresh_start(
        tmp_path,
        confirm_hard_delete=True,
        run_id="fresh_all_lanes_test",
        tracked_paths_fn=_tracked(set()),
    )

    assert result["status"] == "PASS"
    assert not old_file.exists()
    assert source_data.read_text(encoding="utf-8") == "keep\n"
    assert baseline.read_text(encoding="utf-8") == "{}"
    active = json.loads((tmp_path / "runtime" / "workbench" / "active_run.json").read_text(encoding="utf-8"))
    assert active["run_id"] == "fresh_all_lanes_test"
    assert active["artifact_reuse_policy"] == "active_run_id_only"
    assert active["previous_run_artifacts_reused"] is False
    assert (tmp_path / "runtime" / "workbench" / "active_run.lock").is_file()
    assert Path(active["pre_delete_manifest"]).is_file()


def test_fresh_start_preserves_tracked_files_inside_generated_roots(tmp_path: Path) -> None:
    tracked_file = tmp_path / "research_cards" / "crypto" / ".gitkeep"
    generated_file = tmp_path / "research_cards" / "crypto" / "old" / "smoke_report.json"
    generated_file.parent.mkdir(parents=True)
    tracked_file.write_text("", encoding="utf-8")
    generated_file.write_text("{}", encoding="utf-8")

    fresh_start(
        tmp_path,
        confirm_hard_delete=True,
        run_id="fresh_all_lanes_test",
        tracked_paths_fn=_tracked({"research_cards/crypto/.gitkeep"}),
    )

    assert tracked_file.exists()
    assert not generated_file.exists()
    assert (tmp_path / "runtime" / "workbench" / "fresh_start_manifests").exists()


def test_fresh_start_rejects_preserved_scope_if_added_to_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import workbench.src.run.fresh_start as module

    monkeypatch.setattr(module, "GENERATED_TARGETS", ("catalogs", "runtime/wallet_setup", "data"))

    with pytest.raises(FreshStartError, match="preserved path"):
        fresh_start(
            tmp_path,
            confirm_hard_delete=True,
            run_id="fresh_all_lanes_test",
            tracked_paths_fn=_tracked(set()),
        )


def test_fresh_start_rejects_generated_target_outside_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import workbench.src.run.fresh_start as module

    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir()
    (outside / "artifact.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "GENERATED_TARGETS", (str(outside),))
    try:
        with pytest.raises(FreshStartError, match="outside repo"):
            fresh_start(
                tmp_path,
                confirm_hard_delete=True,
                run_id="fresh_all_lanes_test",
                tracked_paths_fn=_tracked(set()),
            )
        assert (outside / "artifact.json").is_file()
    finally:
        (outside / "artifact.json").unlink(missing_ok=True)
        outside.rmdir()
