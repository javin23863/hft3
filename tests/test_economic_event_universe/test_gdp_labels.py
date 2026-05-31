"""GDP release prints map to distinct E_t labels."""

from economic_event_universe.labels import row_to_event_context


def test_gdp_release_labels_distinct():
    assert row_to_event_context("GDP_ADVANCE", "TIGHT") == "GDP_ADVANCE_TIGHT"
    assert row_to_event_context("GDP_SECOND", "TIGHT") == "GDP_SECOND_TIGHT"
    assert row_to_event_context("GDP_FINAL", "TIGHT") == "GDP_FINAL_TIGHT"
