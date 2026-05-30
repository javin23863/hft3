"""model_event_binding.yaml matches generator output."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BINDING = REPO / "workbench" / "config" / "model_event_binding.yaml"


def test_binding_matches_generator():
    before = BINDING.read_text(encoding="utf-8")
    subprocess.run(
        [sys.executable, str(REPO / "workbench" / "scripts" / "generate_model_event_binding.py")],
        cwd=str(REPO),
        check=True,
    )
    after = BINDING.read_text(encoding="utf-8")
    assert before == after, "Run generate_model_event_binding.py and commit model_event_binding.yaml"
