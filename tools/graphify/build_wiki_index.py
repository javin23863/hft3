#!/usr/bin/env python3
"""Generate graphify-out/wiki/index.md with freshness banner for AI onboarding."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "graphify-out" / "wiki" / "index.md"
LABELS = REPO / "graphify-out" / ".graphify_labels.json"
GRAPH = REPO / "graphify-out" / "graph.json"

KEY_EXPLAIN = [
    "ReplaySession",
    "build_certification_stamp",
    "run_after_action_report",
    "CampaignRunner",
    "run_event_replay",
    "MBOFeatureExtractor",
    "HypothesisRegistry",
]

COMMUNITY_HINTS = {
    "0": "Core infrastructure",
    "1": "Backtest pipeline",
    "2": "Feature engine",
    "3": "Workbench",
    "4": "Execution / replay",
    "5": "Data ingest",
}


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _node_count() -> int:
    if not GRAPH.is_file():
        return 0
    try:
        data = json.loads(GRAPH.read_text(encoding="utf-8"))
        return len(data.get("nodes", []))
    except (json.JSONDecodeError, OSError):
        return 0


def _top_communities(limit: int = 12) -> list[tuple[str, str]]:
    if not LABELS.is_file():
        return []
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    items = sorted(labels.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0)
    out: list[tuple[str, str]] = []
    for cid, name in items[:limit]:
        hint = COMMUNITY_HINTS.get(str(cid), "")
        label = f"{name}" + (f" — {hint}" if hint else "")
        out.append((str(cid), label))
    return out


def build() -> Path:
    ts = datetime.now(timezone.utc).isoformat()
    sha = _git_sha()
    nodes = _node_count()
    communities = _top_communities()

    lines = [
        "# hft3 Code Graph — AI Entry Point",
        "",
        f"> **Freshness:** Built `{ts}` | Graph commit `{sha}` | AST nodes `{nodes}`",
        "",
        "Read this file **before** prose docs. Then use `graphify query`, `graphify path`, or `graphify explain`.",
        "",
        "## Start here",
        "",
        "```bash",
        "graphify query \"where is ReplaySession defined?\"",
        "graphify explain ReplaySession",
        "graphify path run_event_replay ReplaySession",
        "```",
        "",
        "## Key concepts (graphify explain)",
        "",
    ]
    for name in KEY_EXPLAIN:
        lines.append(f"- `{name}` — run: `graphify explain \"{name}\"`")
    lines.extend(["", "## Top communities", ""])
    for cid, label in communities:
        lines.append(f"- Community {cid}: {label}")
    lines.extend(
        [
            "",
            "## Human docs (after graph)",
            "",
            "- [docs/ai/ONBOARDING.md](../../docs/ai/ONBOARDING.md)",
            "- [AGENTS.md](../../AGENTS.md)",
            "- [docs/human/DOC_INDEX.md](../../docs/human/DOC_INDEX.md)",
            "",
            "## Rebuild graph",
            "",
            "```bash",
            "graphify update .",
            "# or: scripts/graphify_rebuild.ps1",
            "python tools/graphify/build_wiki_index.py",
            "```",
            "",
        ]
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path}")
