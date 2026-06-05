"""Shell timeout wrapper contract tests."""

from __future__ import annotations

from pathlib import Path


def test_timeout_wrapper_cleans_only_launched_process_tree() -> None:
    src = (Path(__file__).resolve().parents[1] / "tools" / "shell" / "run_with_timeout.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Stop-ProcessTree" in src
    assert "function Get-ProcessTreeIds" in src
    assert "ParentProcessId=$RootProcessId" in src
    assert "Stop-ProcessTree -RootProcessId $p.Id" in src
    assert "if ($code -ne 0)" in src
    assert "$Command[0] -eq '--'" in src
    assert "with powershell -File, omit --" in src
    assert "Get-MatchingCommandProcesses" not in src
    assert "Stop-MatchingCommandLeftovers" not in src


def test_timeout_wrapper_has_bounded_taskkill_fallback_for_launched_tree() -> None:
    src = (Path(__file__).resolve().parents[1] / "tools" / "shell" / "run_with_timeout.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Wait-ProcessesGone" in src
    assert "Wait-ProcessesGone -ProcessIds $treeIds -TimeoutMs 5000" in src
    assert "taskkill.exe /PID $processId /T /F" in src
    assert "[Console]::Error.WriteLine(\"[$Label] TIMEOUT" in src
    assert "Write-Error \"[$Label] TIMEOUT" not in src


def test_graphify_rebuild_runs_raw_update_then_cluster_report() -> None:
    src = (Path(__file__).resolve().parents[1] / "scripts" / "graphify_rebuild.ps1").read_text(
        encoding="utf-8"
    )

    update_cmd = "python -m graphify update . --force --no-cluster"
    cluster_cmd = "python -m graphify cluster-only . --no-viz"

    assert update_cmd in src
    assert cluster_cmd in src
    assert src.index(update_cmd) < src.index(cluster_cmd)
    assert "graphify update . --force --no-cluster exited $updateExit" in src
    assert "graphify cluster-only . --no-viz exited $clusterExit" in src
    assert "-TimeoutSec $UpdateTimeoutSec" in src
    assert "-TimeoutSec $ClusterTimeoutSec" in src
    assert "function Backup-GraphState" in src
    assert "function Clear-GraphState" in src
    assert "function Restore-GraphState" in src
    assert "Clear-GraphState" in src
    assert "diagnostic fallback" not in src


def test_graphifyignore_excludes_generated_artifact_roots() -> None:
    ignored = {
        line.strip()
        for line in (Path(__file__).resolve().parents[1] / ".graphifyignore").read_text(
            encoding="utf-8-sig"
        ).splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert "artifacts/" in ignored
    assert "build-msvc/" in ignored
    assert "vendor/" in ignored
