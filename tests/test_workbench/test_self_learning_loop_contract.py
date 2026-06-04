"""Contract for Workbench parameter optimization and analyst feedback authority."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_parameter_optimization_paths_do_not_call_llm_or_after_action() -> None:
    """The self-learning optimization loop is WFC/evidence driven, not LLM driven."""

    paths = [
        "apps/workbench/src/optimization/param_matrix.py",
        "apps/workbench/src/optimization/matrix_runner.py",
        "apps/workbench/src/optimization/plateau_selector.py",
        "apps/workbench/src/robustness/wfc/gate.py",
        "apps/workbench/src/robustness/pack.py",
    ]
    forbidden = (
        "run_after_action_report",
        "run_llm_",
        "openai_compatible_client",
        "GPT-5.5",
        "llm_client",
        "after_action",
    )

    hits: list[str] = []
    for path in paths:
        text = _read(path)
        for needle in forbidden:
            if needle in text:
                hits.append(f"{path}: {needle}")

    assert hits == []


def test_after_action_is_post_run_and_fast_sweep_skipped() -> None:
    engine_src = _read("apps/workbench/src/run/engine.py")
    campaign_src = _read("apps/workbench/src/run/campaign_runner.py")

    assert "if not fast_sweep and _after_action_allowed():" in engine_src
    assert "run_after_action_report(ctx.artifact_dir" in engine_src
    assert "strategy_params" not in engine_src.split("run_after_action_report", 1)[1]
    assert "run_after_action_report" not in campaign_src


def test_plateau_selection_uses_in_sample_only() -> None:
    from workbench.src.optimization.plateau_selector import select_robust_plateau

    rows = [
        {
            "parameter_hash": "high_is_bad_oos",
            "params": {"signal_threshold": 0.9},
            "is_metrics": {"sharpe": 10.0},
            "oos_metrics": {"sharpe": -99.0, "net_return": -99.0},
        },
        {
            "parameter_hash": "low_is_good_oos",
            "params": {"signal_threshold": 0.1},
            "is_metrics": {"sharpe": 1.0},
            "oos_metrics": {"sharpe": 99.0, "net_return": 99.0},
        },
    ]

    selected = select_robust_plateau(rows, primary_metric="sharpe")

    assert selected
    assert selected["__plateau_hash__"] == "high_is_bad_oos"
