"""Document ingestion: PDF, Word, web → text, summary, embeddings, KG slice."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Union
from urllib.parse import urlparse

import networkx as nx

from research_pipeline.llm import generate_json

_SUMMARY_SYSTEM = (
    "You summarize trading research documents for quant review. "
    "Return 3-5 bullet points in plain text."
)


def extract_text(source: Union[str, Path]) -> str:
    src = str(source)
    if src.startswith(("http://", "https://")):
        return _extract_url(src)
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"Document not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in (".docx", ".doc"):
        return _extract_docx(path)
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"Unsupported document type: {suffix}")


def _extract_pdf(path: Path) -> str:
    import pdfplumber

    parts: List[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
    return "\n\n".join(parts)


def _extract_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_url(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(url, timeout=30, headers={"User-Agent": "hft3-research-pipeline/1.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def summarise_text(text: str) -> str:
    snippet = text[:12000]
    data, err = generate_json(_SUMMARY_SYSTEM, f"Summarize:\n\n{snippet}")
    if data and isinstance(data.get("summary"), str):
        return data["summary"]
    if err is None and data:
        return str(data)
    if err:
        return _heuristic_summary(snippet)
    return _heuristic_summary(snippet)


def _heuristic_summary(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = lines[:8]
    return "- " + "\n- ".join(head[:5]) if head else "(empty document)"


def embed_text(text: str) -> Any:
    """Return embedding vector; None if sentence-transformers unavailable."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    model_name = "all-MiniLM-L6-v2"
    model = SentenceTransformer(model_name)
    vec = model.encode(text[:8000], normalize_embeddings=True)
    return vec


def build_knowledge_graph(text: str, *, doc_id: str = "doc:unknown") -> nx.DiGraph:
    """Lightweight entity/relation graph aligned with data_layer KG node types."""
    g = nx.DiGraph()
    g.add_node(doc_id, type="document", label=doc_id)

    events = set(re.findall(r"\b(CPI|NFP|FOMC|GDP|PCE|ISM)\b", text, re.I))
    for ev in events:
        nid = f"event:{ev.upper()}"
        g.add_node(nid, type="macro-event", event_id=ev.upper())
        g.add_edge(doc_id, nid, relation="mentions")

    instruments = set(re.findall(r"\b(MES|ES|NQ|YM|CL|GC|ZN|ZB|VX)\b", text))
    for sym in instruments:
        nid = f"instrument:{sym}"
        g.add_node(nid, type="instrument", symbol=sym)
        g.add_edge(doc_id, nid, relation="mentions")

    entities = set(re.findall(r"\b([A-Z]{2,5})\b", text))
    for ent in sorted(entities - instruments - events):
        if ent in {"PDF", "API", "CLI", "LLM", "FIBO", "URL", "WORD"}:
            continue
        nid = f"entity:{ent}"
        g.add_node(nid, type="entity", name=ent)
        g.add_edge(doc_id, nid, relation="mentions")

    obligations = re.findall(
        r"(?i)(must not|shall not|required to|no lookahead|walk[- ]forward)[^.\\n]{0,80}",
        text,
    )
    for i, ob in enumerate(obligations[:10]):
        nid = f"obligation:{doc_id}:{i}"
        g.add_node(nid, type="obligation", text=ob.strip())
        g.add_edge(doc_id, nid, relation="contains")

    return g


def graph_to_kg_records(g: nx.DiGraph) -> Dict[str, List[Dict[str, Any]]]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for nid, attrs in g.nodes(data=True):
        node = {"id": str(nid), **{k: v for k, v in attrs.items() if k != "label"}}
        nodes.append(node)
    for src, dst, attrs in g.edges(data=True):
        edges.append({"from": str(src), "to": str(dst), **attrs})
    return {"nodes": nodes, "edges": edges}
