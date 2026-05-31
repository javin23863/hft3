"""Shared C++ stack self-test check names (must match hft_research_sim JSON)."""

REQUIRED_STACK_CHECKS = frozenset(
    {
        "gateway_init",
        "spsc_queue_roundtrip",
        "feature_extract",
        "decision_evaluate",
        "risk_precheck",
    }
)
