from economic_event_universe.labels import row_to_event_context
from economic_event_universe.registry import event_definitions


def test_every_yaml_type_has_label():
    for et, cfg in event_definitions().items():
        label = cfg.get("event_context_label")
        assert label, f"missing label for {et}"
        mapped = row_to_event_context(et, str(cfg.get("window_name", "TIGHT")))
        assert mapped


def test_known_mappings():
    assert row_to_event_context("CPI", "TIGHT") == "CPI_TIGHT"
    assert row_to_event_context("UNEMPLOYMENT_CLAIMS", "TIGHT") == "CLAIMS_TIGHT"
    assert row_to_event_context("FOMC_STATEMENT", "TIGHT") == "FOMC_STATEMENT_TIGHT"
