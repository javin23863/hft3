"""HftBacktest campaign runner — high-throughput independent scenario replay."""

from backtest_pipeline.src.hft_campaign.config import HftCampaignConfig, HftCampaignResult, HftScenarioResult
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario, compute_scenario_id


def run_hftbacktest_campaign(*args, **kwargs):
    from backtest_pipeline.src.hft_campaign.runner import run_hftbacktest_campaign as _run

    return _run(*args, **kwargs)


__all__ = [
    "HftCampaignConfig",
    "HftCampaignResult",
    "HftScenarioResult",
    "HftReplayScenario",
    "compute_scenario_id",
    "run_hftbacktest_campaign",
]
