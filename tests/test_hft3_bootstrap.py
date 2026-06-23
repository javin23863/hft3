from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_import_bootstrap_from_repo_root_enables_package_and_app_imports() -> None:
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in list(env):
        if key.upper() == "PYTHONPATH":
            env.pop(key)
    env["PYTHONNOUSERSITE"] = "1"

    code = textwrap.dedent(
        """
        import json
        import sys
        from pathlib import Path

        from hft3_bootstrap import repo_root

        import backtest_pipeline
        import workbench.src

        root = repo_root()
        required = [str(root), str(root / "packages"), str(root / "apps")]
        missing = [entry for entry in required if entry not in sys.path]
        backtest_root = root / "packages" / "backtest_pipeline"
        workbench_src = root / "apps" / "workbench" / "src"
        backtest_paths = [str(Path(path).resolve()) for path in backtest_pipeline.__path__]
        workbench_src_file = Path(workbench.src.__file__).resolve()

        payload = {
            "backtest_paths": backtest_paths,
            "missing": missing,
            "workbench_src_file": str(workbench_src_file),
        }
        print(json.dumps(payload, sort_keys=True))

        if missing:
            raise SystemExit(2)
        if str(backtest_root) not in backtest_paths:
            raise SystemExit(3)
        workbench_src_file.relative_to(workbench_src)
        """
    )

    result = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_hft_campaign_scripts_bootstrap_without_pythonpath() -> None:
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in list(env):
        if key.upper() == "PYTHONPATH":
            env.pop(key)
    env["PYTHONNOUSERSITE"] = "1"

    scripts = (
        "hft_generate_campaign_manifest.py",
        "hft_validate_replay_inputs.py",
        "hft_run_campaign.py",
    )
    for script in scripts:
        result = subprocess.run(
            [sys.executable, str(repo / "scripts" / script), "--help"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"{script} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
