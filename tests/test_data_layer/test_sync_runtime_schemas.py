"""Runtime schema mirror stays in sync with packet schemas."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "packages" / "data_layer" / "packet"
DEST = REPO / "runtime" / "schemas"

NAMES = (
    "schema_v1.json",
    "schema_aar_response_v1.json",
    "schema_pipeline_request_v1.json",
    "schema_pipeline_response_v1.json",
    "schema_pipeline_hypothesis_response_v1.json",
    "schema_pipeline_idea_set_v1.json",
    "schema_research_decision_packet_v1.json",
)


def test_runtime_schema_list_includes_research_decision_packet():
    assert "schema_research_decision_packet_v1.json" in NAMES


def test_runtime_schemas_match_packet_dir():
    for name in NAMES:
        src_text = (SRC / name).read_text(encoding="utf-8")
        dest_path = DEST / name
        assert dest_path.is_file(), f"missing runtime mirror: {name}"
        dest_text = dest_path.read_text(encoding="utf-8")
        assert json.loads(src_text) == json.loads(dest_text), name
