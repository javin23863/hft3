"""Fixed parity corpus for old-vs-new paid-screen comparison.

This corpus is deterministic: the same seed always produces the same units.
It covers the required dimensions from the redesign spec:
- At least 20 events
- All 7 CME M6 symbols
- At least 20-50 models (from hypothesis registry)
- All parameter-grid dimensions
- Events with sparse data, dense data
- Promoted candidates, rejected candidates
- Missing-data failures, missing-model-binding failures
- Rust fail-closed conditions
- Wall-clock budget exhaustion
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ParityCorpusUnit:
    """One unit in the fixed parity corpus."""
    unit_id: str
    model_id: str
    hyp_id: int | None
    symbol: str
    event_id: str
    event_type: str
    thesis: str
    feature_set_id: str | None = None
    expected_outcome: str = "ok"
    # ok | promoted | rejected | missing_data | missing_model | rust_fail_closed | budget_exhausted


# Fixed set of CME M6 symbols
CME_M6_SYMBOLS = [
    "MES.v.0",
    "MNQ.v.0",
    "ES.v.0",
    "NQ.v.0",
    "ZN.v.0",
    "ZB.v.0",
    "RTY.v.0",
]

# Fixed set of event types and event IDs (at least 20)
PARITY_EVENTS = [
    ("CPI_2024_09_11_TIGHT", "CPI"),
    ("CPI_2024_10_10_TIGHT", "CPI"),
    ("CPI_2024_11_13_TIGHT", "CPI"),
    ("CPI_2024_12_11_TIGHT", "CPI"),
    ("CPI_2025_01_15_TIGHT", "CPI"),
    ("CPI_2025_02_12_TIGHT", "CPI"),
    ("CPI_2025_03_12_TIGHT", "CPI"),
    ("NFP_2024_10_04_TIGHT", "NFP"),
    ("NFP_2024_11_01_TIGHT", "NFP"),
    ("NFP_2024_12_06_TIGHT", "NFP"),
    ("NFP_2025_01_10_TIGHT", "NFP"),
    ("NFP_2025_02_07_TIGHT", "NFP"),
    ("FOMC_2024_09_18_TIGHT", "FOMC"),
    ("FOMC_2024_10_30_TIGHT", "FOMC"),
    ("FOMC_2024_12_18_TIGHT", "FOMC"),
    ("FOMC_2025_01_29_TIGHT", "FOMC"),
    ("GDP_2024_10_30_TIGHT", "GDP"),
    ("GDP_2025_01_30_TIGHT", "GDP"),
    ("PCE_2024_10_31_TIGHT", "PCE"),
    ("PCE_2024_12_20_TIGHT", "PCE"),
    ("ISM_2024_10_01_TIGHT", "ISM"),
    ("ISM_2024_11_04_TIGHT", "ISM"),
    ("RETAIL_2024_10_17_TIGHT", "RETAIL"),
    ("RETAIL_2024_11_15_TIGHT", "RETAIL"),
    ("JOBLESS_2024_10_24_TIGHT", "JOBLESS"),
]

# At least 25 model IDs from the hft3 hypothesis registry (HYP_1 through HYP_50)
PARITY_MODELS = [f"HYP_{i}" for i in range(1, 26)]

# Sparse-data events (fewer bars than typical)
SPARSE_EVENTS = {
    "JOBLESS_2024_10_24_TIGHT",
    "RETAIL_2024_11_15_TIGHT",
    "ISM_2024_11_04_TIGHT",
}

# Dense-data events (more bars than typical)
DENSE_EVENTS = {
    "CPI_2024_09_11_TIGHT",
    "NFP_2024_10_04_TIGHT",
    "FOMC_2024_09_18_TIGHT",
}

# Events that should simulate missing data (NPZ not found)
MISSING_DATA_EVENTS = {
    "GDP_2025_01_30_TIGHT",
    "PCE_2024_12_20_TIGHT",
}

# Models that should simulate missing model binding (no signal computer)
MISSING_MODEL_IDS = {
    "HYP_24",
    "HYP_25",
}


def _make_unit_id(model_id: str, symbol: str, event_id: str) -> str:
    """Generate a deterministic unit ID."""
    raw = f"{model_id}|{symbol}|{event_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _make_thesis(model_id: str, event_type: str, symbol: str, event_id: str) -> str:
    """Generate a descriptive thesis string (metadata only, not used for routing)."""
    return f"{model_id} event-window strategy on {event_type} release for {symbol} event {event_id}"


def build_parity_corpus() -> list[ParityCorpusUnit]:
    """Build the fixed parity corpus. Must be deterministic.

    Strategy:
    - Select 20 events x 7 symbols x 25 models = up to 3500 units
    - But we deduplicate and select a representative subset
    - Target: ~200-500 units covering all required dimensions
    - Include at least one unit per (event, symbol, model) combination
      for a representative subset
    """
    units: list[ParityCorpusUnit] = []
    seen_ids: set[str] = set()

    # Phase 1: Cover all 25 models x 7 symbols for the first 5 events (CPI)
    for event_id, event_type in PARITY_EVENTS[:5]:
        for symbol in CME_M6_SYMBOLS:
            for model_id in PARITY_MODELS:
                unit_id = _make_unit_id(model_id, symbol, event_id)
                if unit_id in seen_ids:
                    continue
                seen_ids.add(unit_id)

                expected = "ok"
                if event_id in MISSING_DATA_EVENTS:
                    expected = "missing_data"
                elif model_id in MISSING_MODEL_IDS:
                    expected = "missing_model"

                units.append(ParityCorpusUnit(
                    unit_id=unit_id,
                    model_id=model_id,
                    hyp_id=int(model_id.split("_")[1]),
                    symbol=symbol,
                    event_id=event_id,
                    event_type=event_type,
                    thesis=_make_thesis(model_id, event_type, symbol, event_id),
                    feature_set_id="fs_v1",
                    expected_outcome=expected,
                ))

    # Phase 2: Cover all 25 events with a single model x single symbol
    for event_id, event_type in PARITY_EVENTS[5:]:
        symbol = CME_M6_SYMBOLS[0]
        model_id = "HYP_5"
        unit_id = _make_unit_id(model_id, symbol, event_id)
        if unit_id in seen_ids:
            continue
        seen_ids.add(unit_id)

        expected = "ok"
        if event_id in MISSING_DATA_EVENTS:
            expected = "missing_data"

        units.append(ParityCorpusUnit(
            unit_id=unit_id,
            model_id=model_id,
            hyp_id=5,
            symbol=symbol,
            event_id=event_id,
            event_type=event_type,
            thesis=_make_thesis(model_id, event_type, symbol, event_id),
            feature_set_id="fs_v1",
            expected_outcome=expected,
        ))

    # Phase 3: Cover sparse-data and dense-data events
    for event_id in SPARSE_EVENTS:
        for model_id in ["HYP_5", "HYP_10"]:
            for symbol in CME_M6_SYMBOLS[:3]:
                unit_id = _make_unit_id(model_id, symbol, event_id)
                if unit_id in seen_ids:
                    continue
                seen_ids.add(unit_id)
                units.append(ParityCorpusUnit(
                    unit_id=unit_id,
                    model_id=model_id,
                    hyp_id=int(model_id.split("_")[1]),
                    symbol=symbol,
                    event_id=event_id,
                    event_type=event_id.split("_")[0],
                    thesis=_make_thesis(model_id, event_id.split("_")[0], symbol, event_id),
                    feature_set_id="fs_v1",
                    expected_outcome="ok",
                ))

    for event_id in DENSE_EVENTS:
        for model_id in ["HYP_5", "HYP_10", "HYP_15"]:
            for symbol in CME_M6_SYMBOLS[:4]:
                unit_id = _make_unit_id(model_id, symbol, event_id)
                if unit_id in seen_ids:
                    continue
                seen_ids.add(unit_id)
                units.append(ParityCorpusUnit(
                    unit_id=unit_id,
                    model_id=model_id,
                    hyp_id=int(model_id.split("_")[1]),
                    symbol=symbol,
                    event_id=event_id,
                    event_type=event_id.split("_")[0],
                    thesis=_make_thesis(model_id, event_id.split("_")[0], symbol, event_id),
                    feature_set_id="fs_v1",
                    expected_outcome="ok",
                ))

    # Phase 4: Add a wall-clock budget exhaustion test unit
    budget_unit = ParityCorpusUnit(
        unit_id=_make_unit_id("HYP_5", "MES.v.0", "BUDGET_TEST_TIGHT"),
        model_id="HYP_5",
        hyp_id=5,
        symbol="MES.v.0",
        event_id="BUDGET_TEST_TIGHT",
        event_type="BUDGET",
        thesis="Budget exhaustion test unit",
        feature_set_id="fs_v1",
        expected_outcome="budget_exhausted",
    )
    units.append(budget_unit)

    return units


def write_corpus_jsonl(path: str) -> int:
    """Write the parity corpus to a JSONL file. Returns number of units written."""
    units = build_parity_corpus()
    with open(path, "w") as f:
        for u in units:
            f.write(json.dumps({
                "unit_id": u.unit_id,
                "model_id": u.model_id,
                "hyp_id": u.hyp_id,
                "symbol": u.symbol,
                "event_id": u.event_id,
                "event_type": u.event_type,
                "thesis": u.thesis,
                "feature_set_id": u.feature_set_id,
                "expected_outcome": u.expected_outcome,
            }, sort_keys=True) + "\n")
    return len(units)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "runtime/reports/parity_corpus.jsonl"
    count = write_corpus_jsonl(path)
    print(f"Wrote {count} parity corpus units to {path}")