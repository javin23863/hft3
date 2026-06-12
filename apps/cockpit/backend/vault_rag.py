"""Keyword retrieval over the Obsidian vault for the Gemma chat.

No embeddings/infra — a tf-style overlap scorer over the curated vault markdown
(ontology, overview, architecture, 90 papers). Ontology.md is always pinned so
the model speaks the system's vocabulary. mtime/count-cached so the hot path
doesn't re-read the vault each query.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Optional

from . import paths

_TOKEN = re.compile(r"[a-z0-9]+")
_index: dict = {"sig": None, "docs": []}  # docs: [(path, title, text, lower)]

# Defense-in-depth: even though the curated vault holds no credential VALUES
# today, anything in it is surfaced verbatim to the chat model. Redact
# secret-shaped `key: value` / `key=value` lines from snippets BEFORE they reach
# the prompt so a future note pasting a key/token/connection-string can't leak.
_SECRET_KV = re.compile(
    r"(?im)^(.*\b(?:api[_-]?key|secret|token|password|passwd|credential|"
    r"access[_-]?key|private[_-]?key|conn(?:ection)?[_-]?str(?:ing)?)\b\s*[:=]\s*)\S.*$"
)


def _scrub(text: str) -> str:
    return _SECRET_KV.sub(r"\1[REDACTED]", text)


def _iter_md(root: Path):
    for p in root.rglob("*.md"):
        if ".pytest_cache" in p.parts or ".git" in p.parts:
            continue
        yield p


def _title(text: str, path: Path) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return path.stem


def _signature(root: Path) -> Optional[tuple]:
    files = list(_iter_md(root))
    if not files:
        return None
    try:
        return (len(files), round(max(f.stat().st_mtime for f in files), 2))
    except OSError:
        return (len(files), 0.0)


def _build_index() -> list:
    root = paths.vault_dir()
    sig = _signature(root)
    if sig is None:
        _index["sig"], _index["docs"] = None, []
        return []
    if _index["sig"] == sig:
        return _index["docs"]
    docs = []
    for p in _iter_md(root):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        docs.append((str(p), _title(text, p), text, text.lower()))
    _index["sig"], _index["docs"] = sig, docs
    return docs


def _snippet(text: str, terms: list[str], width: int = 420) -> str:
    low = text.lower()
    pos = -1
    for t in terms:
        i = low.find(t)
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        return text[:width].strip()
    start = max(0, pos - width // 4)
    return text[start:start + width].strip()


def retrieve(query: str, k: int = 4) -> list[dict]:
    """Top-k vault snippets for the query. Ontology.md always included."""
    docs = _build_index()
    terms = [t for t in _TOKEN.findall(query.lower()) if len(t) > 2]
    scored = []
    ontology = None
    for path, title, text, low in docs:
        if Path(path).name.lower() == "ontology.md":
            ontology = {"path": path, "title": title, "snippet": _scrub(text[:600].strip()), "score": 0.0, "pinned": True}
        if not terms:
            continue
        tf = sum(low.count(t) for t in terms)
        if tf == 0:
            continue
        score = tf / math.sqrt(max(len(low), 1))
        scored.append({"path": path, "title": title, "snippet": _scrub(_snippet(text, terms)),
                        "score": round(score, 6), "pinned": False})
    scored.sort(key=lambda d: -d["score"])
    out = scored[:k]
    if ontology is not None and not any(d["path"] == ontology["path"] for d in out):
        out = [ontology] + out
    return out
