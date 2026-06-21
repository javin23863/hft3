"""Autonomous production-path guards.

These tests keep the autonomous runner from turning a sample model/event into
production behavior. Fixtures and docs may use examples; the runner itself must
stay registry/config/catalog driven.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hft3.research.run_autonomous import AutonomousRunner, CampaignConfig


RUNNER = Path("packages/hft3/research/run_autonomous.py")
DEFAULT_CONFIG = Path("configs/research/autonomous_hft3.yaml")


def _config_without_catalog_discovery() -> CampaignConfig:
    cfg = CampaignConfig.from_yaml(DEFAULT_CONFIG)
    cfg.data["event_windows"] = [
        {
            "event_id": "pytest",
            "start_ns": 1,
            "end_ns": 2,
            "symbols": ["MES.v.0"],
        }
    ]
    cfg.models["alpha"] = ["HYP_1"]
    cfg.models["select"] = {"roles": ["alpha"]}
    return cfg


def test_autonomous_runner_has_no_fixed_candidate_or_agent_branding() -> None:
    src = RUNNER.read_text(encoding="utf-8")
    forbidden = (
        "CPI_",
        "HYP_5",
        "SPREAD_BLOWOUT_RECOMPRESSION",
        "Codex",
        "codex",
    )
    for token in forbidden:
        assert token not in src, f"production runner must not hardcode {token!r}"


def test_default_autonomous_config_uses_catalogs_not_fixed_samples() -> None:
    src = DEFAULT_CONFIG.read_text(encoding="utf-8")
    forbidden = (
        "CPI_",
        "HYP_5",
        "SPREAD_BLOWOUT_RECOMPRESSION",
    )
    for token in forbidden:
        assert token not in src, f"default autonomous config must not pin sample {token!r}"
    assert "event_catalog_path" in src
    assert "catalog_path" in src


def test_autonomous_runner_does_not_generate_external_access_packet(tmp_path: Path) -> None:
    cfg = _config_without_catalog_discovery()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="NOEXTPACKET")
    assert runner.run() == 2

    assert not (tmp_path / "artifacts" / "research_cards").exists()
    src = RUNNER.read_text(encoding="utf-8").lower()
    assert "diamond" not in src
    assert "external_access_packet" not in src


def test_workbench_evidence_bridge_observes_without_promoting(monkeypatch, tmp_path: Path) -> None:
    import apps.workbench.src.run.campaign_runner as campaign_runner

    def fake_run_campaign(repo_root: Path, model_id: str, symbol: str, **kwargs):
        artifact_dir = tmp_path / "workbench" / kwargs["campaign_id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "campaign_id": kwargs["campaign_id"],
            "status": "PASS",
            "model_id": model_id,
            "symbol": symbol,
            "events_ran": 4,
            "robustness_passed": True,
            "robustness_checks": [
                {
                    "name": "monte_carlo_sharpe",
                    "observed_value": 0.7,
                    "threshold": 0.5,
                    "comparison_operator": ">=",
                    "passed": True,
                },
                {
                    "name": "drawdown_limit",
                    "observed_value": -0.05,
                    "threshold": -0.10,
                    "comparison_operator": ">=",
                    "passed": True,
                },
            ],
            "walk_forward": {"status": "PASS"},
            "wfc_status": "PASS",
            "wfc": {"spearman": 0.3},
            "promote_candidate": False,
            "periods": [{"name": "Discovery", "expectancy": 1.0, "events_run": 4}],
        }
        (artifact_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (artifact_dir / "diagnostics.json").write_text(json.dumps(summary), encoding="utf-8")
        (artifact_dir / "campaign.json").write_text(json.dumps({"campaign_id": kwargs["campaign_id"]}), encoding="utf-8")
        return SimpleNamespace(
            campaign_id=kwargs["campaign_id"],
            model_id=model_id,
            symbol=symbol,
            status="PASS",
            artifact_dir=str(artifact_dir),
        )

    monkeypatch.setattr(campaign_runner, "run_campaign", fake_run_campaign)

    cfg = CampaignConfig(
        campaign_id="autonomous-test",
        data={
            "dataset_id": "unit",
            "source": "fixture",
            "requested": "L3_MBO",
            "resolved": "L3_MBO",
            "symbol_universe": ["MES.v.0"],
            "event_windows": [{"event_id": "EVENT_FROM_TEST", "start_ns": 1, "end_ns": 2}],
        },
        latency_profile={},
        features={"feature_set_id": "core_64_v1"},
        models={"alpha": ["MODEL_FROM_TEST"]},
        scoring={"min_sharpe": 0.5, "max_drawdown": -0.10},
        workbench={"enabled": True},
        output={"artifacts_dir": str(tmp_path / "artifacts")},
    )
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="OBSERVED")
    assert runner.run() == 2

    metrics = json.loads((tmp_path / "artifacts" / "OBSERVED" / "backtest_metrics.json").read_text(encoding="utf-8"))
    assert metrics["observed"] is True
    rankings = json.loads((tmp_path / "artifacts" / "OBSERVED" / "candidate_rankings.json").read_text(encoding="utf-8"))
    assert rankings[0]["model_id"] == "MODEL_FROM_TEST"
    scoring = json.loads((tmp_path / "artifacts" / "OBSERVED" / "scoring_summary.json").read_text(encoding="utf-8"))
    assert scoring["decision"] == "QUARANTINE"
    assert "Workbench promotion remains blocked" in scoring["reason"]
