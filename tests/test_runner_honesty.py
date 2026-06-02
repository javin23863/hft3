"""Honesty guards for the autonomous runner (Phase 2 follow-up).

These tests enforce: the runner MUST NOT silently mark a candidate as
"passed" without an observed value. This is the single most important
integrity property of the runner. If a contributor ever tries to
revert the "pass_fail=False when observed_value=None" rule (perhaps
to make a failing pipeline look like it passed), these tests catch it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hft3.research.run_autonomous import (
    AutonomousRunner,
    CampaignConfig,
)
from hft3.validation.gate_result import (
    GateCategory,
    GateResult,
    Severity,
)


# ---------- the rule under test ----------


def test_runner_no_dishonest_passes(tmp_path: Path) -> None:
    """In scaffolded mode, every gate in robustness_gates.json must
    have pass_fail=False (no observed value) — never pass_fail=True."""
    cfg = CampaignConfig.from_yaml(Path("configs/research/autonomous_hft3.yaml"))
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="HONEST1")
    runner.run()
    import json
    rg = json.loads(
        (tmp_path / "artifacts" / "HONEST1" / "robustness_gates.json").read_text(encoding="utf-8")
    )
    for g in rg["gates"]:
        # The honest rule: pass_fail can only be True if observed_value is not None.
        if g["observed_value"] is None:
            assert g["pass_fail"] is False, (
                f"DISHONEST GATE: {g['gate_name']} has pass_fail=True but "
                f"observed_value=None. The runner must not claim a gate "
                f"passed without observing anything."
            )
            assert g["severity"] == "blocking", (
                f"Gate {g['gate_name']} with no observation must be BLOCKING, "
                f"otherwise the runner would silently allow the candidate to pass."
            )


def test_runner_default_decision_is_quarantine(tmp_path: Path) -> None:
    """Without real backtest integration, the runner MUST default to
    QUARANTINE (or REJECT), never PROMOTE."""
    cfg = CampaignConfig.from_yaml(Path("configs/research/autonomous_hft3.yaml"))
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="HONEST2")
    rc = runner.run()
    # rc=0 is PROMOTE; rc=1 is REJECT; rc=2 is QUARANTINE; rc=3 is infra failure.
    assert rc in (1, 2), f"runner returned {rc} (PROMOTE) without real backtest"


def test_score_and_decide_does_not_silent_promote(tmp_path: Path) -> None:
    """The scoring stage must produce a decision of QUARANTINE or REJECT
    when the gates have no observed values."""
    import json
    cfg = CampaignConfig.from_yaml(Path("configs/research/autonomous_hft3.yaml"))
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="HONEST3")
    runner.run()
    sc = json.loads(
        (tmp_path / "artifacts" / "HONEST3" / "scoring_summary.json").read_text(encoding="utf-8")
    )
    assert sc["decision"] in ("QUARANTINE", "REJECT"), (
        f"scoring produced {sc['decision']!r} for an unobserved candidate. "
        f"Without real backtest + robustness + walk-forward, the only "
        f"honest decisions are QUARANTINE or REJECT."
    )


# ---------- scaffold disclosure ----------


def test_runner_docstring_discloses_scaffold() -> None:
    """The runner module must explicitly disclose that it is a scaffold
    awaiting WorkbenchEngine integration. A silent scaffold is a
    dishonest scaffold."""
    from hft3.research import run_autonomous
    src = Path(run_autonomous.__file__).read_text(encoding="utf-8")
    # The docstring at the top of the module OR the stage_robustness_and_wf
    # docstring must contain the word "SCAFFOLD" in capital letters.
    assert "SCAFFOLD" in src, (
        "Runner module must self-disclose as SCAFFOLD in its docstring. "
        "Otherwise contributors may mistake the artifact emission for "
        "a real pipeline run."
    )


def test_config_discloses_scaffold() -> None:
    """The example config file must document that the runner is in
    scaffolded mode and that real backtest integration is required
    before any of the artifacts can be trusted for promotion decisions."""
    cfg_text = Path("configs/research/autonomous_hft3.yaml").read_text(encoding="utf-8")
    assert "scaffold" in cfg_text.lower() or "pending" in cfg_text.lower(), (
        "autonomous_hft3.yaml must disclose scaffolded state. A user "
        "running this config without reading the runner code will otherwise "
        "mistake QUARANTINE for a config issue and PROMOTE for a real result."
    )
