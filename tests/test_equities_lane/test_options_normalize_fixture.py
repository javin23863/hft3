"""Options normalize NDJSON format."""
from __future__ import annotations

import json
from pathlib import Path


def test_options_ndjson_record_shape(tmp_path: Path):
    out = tmp_path / "gme.ndjson"
    rec = {
        "session_id": "gme_2021",
        "underlying": "GME",
        "quote_ts_ns": 1611750000000000000,
        "symbol": "GME  210129C00050000",
        "strike": 50.0,
        "right": "C",
        "expiry": "2021-01-29",
        "bid": 1.0,
        "ask": 1.1,
    }
    out.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["session_id"] == "gme_2021"
    assert row["underlying"] == "GME"
    assert row["quote_ts_ns"] > 0


def test_parse_opra_symbol():
    from equities_lane.src.ingest.options_chain_pull import parse_opra_symbol

    strike, right, expiry = parse_opra_symbol("GME   210129C00330000")
    assert strike == 330.0
    assert right == "C"
    assert expiry == "2021-01-29"
    from equities_lane.src.options.chain_resolver import resolve_pull_symbols
    from equities_lane.src.types import ChainRules, DecadalSession, OptionsChainSpec

    session = DecadalSession(
        id="gme_2021",
        symbol="GME",
        date="2021-01-27",
        dataset="XNYS.PILLAR",
        options=OptionsChainSpec(chain_rules=ChainRules()),
    )
    symbols, stype, schema = resolve_pull_symbols(session)
    assert symbols == ["GME.OPT"]
    assert stype == "parent"
    assert schema == "cbbo-1m"
