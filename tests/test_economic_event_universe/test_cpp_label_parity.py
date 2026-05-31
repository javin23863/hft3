"""Python/C++ E_t label parity and canonical events.csv gates."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from economic_event_universe.labels import row_to_event_context
from economic_event_universe.registry import event_definitions
from hft3_bootstrap import data_system_root, repo_root


def _cpp_label_table() -> dict:
    path = repo_root() / "packages" / "features_engine" / "config" / "event_context_labels.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_python_matches_cpp_label_table():
    table = _cpp_label_table()
    for et, cfg in event_definitions().items():
        wn = str(cfg.get("window_name", "TIGHT"))
        assert row_to_event_context(et, wn) == table[et]["label"]
        if cfg.get("main_context_label"):
            assert row_to_event_context(et, "MAIN") == table[et]["main_label"]


def test_fomc_types_distinct():
    assert row_to_event_context("FOMC_STATEMENT", "TIGHT") == "FOMC_STATEMENT_TIGHT"
    assert row_to_event_context("FOMC_PRESS", "TIGHT") == "FOMC_PRESS_TIGHT"
    assert row_to_event_context("FOMC_MINUTES", "TIGHT") == "FOMC_MINUTES_TIGHT"


def test_generated_hpp_fomc_labels_distinct():
    hpp = (repo_root() / "packages" / "features_engine" / "cpp" / "include" / "event_context_labels.generated.hpp").read_text(
        encoding="utf-8"
    )
    assert '"FOMC_STATEMENT", {"FOMC_STATEMENT_TIGHT"' in hpp
    assert '"FOMC_PRESS", {"FOMC_PRESS_TIGHT"' in hpp
    assert '"FOMC_MINUTES", {"FOMC_MINUTES_TIGHT"' in hpp


def test_events_csv_has_no_seed_placeholders():
    path = data_system_root() / "config" / "events.csv"
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            assert "SEED_PLACEHOLDER" not in str(row.get("notes", ""))
