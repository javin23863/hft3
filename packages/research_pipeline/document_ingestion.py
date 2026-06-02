"""Document ingestion: PDF, Word, web → text, summary, embeddings, KG slice.

Phase 3 extension: `ingest_research_bundle(source, research_id, intake_dir)`
extracts text and drives a single LLM call that produces a structured
`IntakePayload`. The payload is validated by pydantic v2 models in
`intake_schema.py` and written to 14 files under
`intake_dir / research_id /` by `intake_bundle.write_intake_bundle`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Union
from urllib.parse import urlparse

import networkx as nx

from research_pipeline.llm import DEFAULT_PIPELINE_MODEL, generate_json
from research_pipeline.intake_schema import (
    Assumption,
    DataRequirement,
    ExecutionLogic,
    ExperimentTranslationNotes,
    FailureMode,
    FeatureRequirement,
    ParameterRange,
    SignalLogic,
    TestableHypothesis,
    ThesisSummary,
)

_SUMMARY_SYSTEM = (
    "You summarize trading research documents for quant review. "
    "Return 3-5 bullet points in plain text."
)

_INTAKE_SYSTEM = (
    "You convert trading research into a structured JSON object for HFT3 backtesting. "
    "The schema is: "
    "{thesis_summary:{main_thesis,instrument_scope,time_horizon,confidence}, "
    "assumptions:[{assumption_id,statement,category,criticality}], "
    "required_data:[{data_id,name,source,vendor,granularity,history_years}], "
    "required_features:[{feature_id,name,definition,feature_engine_slug,group}], "
    "proposed_signal_logic:{signal,entry[],exit[],regime_filter,time_stop_bars}, "
    "proposed_execution_logic:{order_types[],latency_budget_us,cost_model,slippage_bps,max_position,venue}, "
    "parameter_ranges:[{param,min,max,default,units,distribution}], "
    "failure_modes:[{mode_id,condition,severity,mitigation,detection_signal}], "
    "testable_hypotheses:[{hypothesis_id,statement,pass_criteria,fail_criteria,metric,threshold,evaluation_window}], "
    "experiment_translation_notes:{missing_info[],hft3_implementation_reqs[],confidence}}. "
    "If the paper is vague, populate experiment_translation_notes.missing_info with a list of items. "
    "Do NOT invent data, latency, or performance numbers that the paper does not provide. "
    "Return ONLY the JSON object."
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


# ---- Phase 3: 14-file intake bundle ----------------------------------------


def _coerce_list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _parse_intake_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a raw LLM JSON dict into the pydantic model shapes.

    Falls back to safe defaults for missing fields. Never raises — the
    bundle is always written so humans can audit the LLM's raw attempt.
    """
    thesis_raw = raw.get("thesis_summary") or {}
    thesis = ThesisSummary(
        main_thesis=str(thesis_raw.get("main_thesis", "")).strip(),
        instrument_scope=[str(x) for x in thesis_raw.get("instrument_scope", []) or []],
        time_horizon=str(thesis_raw.get("time_horizon", "")).strip(),
        confidence=thesis_raw.get("confidence"),
    )

    assumptions = [
        Assumption(
            assumption_id=str(a.get("assumption_id", f"a{i}")),
            statement=str(a.get("statement", "")),
            category=str(a.get("category", "unspecified")),
            evidence=a.get("evidence"),
            criticality=a.get("criticality"),
        )
        for i, a in enumerate(_coerce_list_of_dicts(raw.get("assumptions")))
    ]
    required_data = [
        DataRequirement(
            data_id=str(d.get("data_id", f"d{i}")),
            name=str(d.get("name", "")),
            source=str(d.get("source", "unknown")),
            granularity=d.get("granularity"),
            history_years=d.get("history_years"),
            vendor=d.get("vendor"),
        )
        for i, d in enumerate(_coerce_list_of_dicts(raw.get("required_data")))
    ]
    required_features = [
        FeatureRequirement(
            feature_id=str(f.get("feature_id", f"f{i}")),
            name=str(f.get("name", "")),
            definition=str(f.get("definition", "")),
            feature_engine_slug=f.get("feature_engine_slug"),
            group=f.get("group"),
        )
        for i, f in enumerate(_coerce_list_of_dicts(raw.get("required_features")))
    ]

    sig_raw = raw.get("proposed_signal_logic") or {}
    signal = SignalLogic(
        signal=str(sig_raw.get("signal", "")).strip(),
        entry=[str(x) for x in sig_raw.get("entry", []) or []],
        exit=[str(x) for x in sig_raw.get("exit", []) or []],
        time_stop_bars=sig_raw.get("time_stop_bars"),
        regime_filter=sig_raw.get("regime_filter"),
    )

    exec_raw = raw.get("proposed_execution_logic") or {}
    execution = ExecutionLogic(
        order_types=[str(x) for x in exec_raw.get("order_types", []) or []],
        latency_budget_us=exec_raw.get("latency_budget_us"),
        cost_model=exec_raw.get("cost_model"),
        slippage_bps=exec_raw.get("slippage_bps"),
        max_position=exec_raw.get("max_position"),
        venue=exec_raw.get("venue"),
    )

    parameters: list[ParameterRange] = []
    for i, p in enumerate(_coerce_list_of_dicts(raw.get("parameter_ranges"))):
        try:
            parameters.append(
                ParameterRange(
                    param=str(p.get("param", f"p{i}")),
                    min=float(p.get("min", 0.0)),
                    max=float(p.get("max", 0.0)),
                    default=float(p.get("default", 0.0)),
                    units=p.get("units"),
                    distribution=p.get("distribution"),
                )
            )
        except (TypeError, ValueError):
            continue

    failure_modes = [
        FailureMode(
            mode_id=str(fm.get("mode_id", f"fm{i}")),
            condition=str(fm.get("condition", "")),
            severity=str(fm.get("severity", "med")),
            mitigation=fm.get("mitigation"),
            detection_signal=fm.get("detection_signal"),
        )
        for i, fm in enumerate(_coerce_list_of_dicts(raw.get("failure_modes")))
    ]

    hypotheses: list[TestableHypothesis] = []
    for i, h in enumerate(_coerce_list_of_dicts(raw.get("testable_hypotheses"))):
        threshold = h.get("threshold")
        try:
            threshold_val: float | None
            threshold_val = float(threshold) if threshold is not None else None
        except (TypeError, ValueError):
            threshold_val = None
        hypotheses.append(
            TestableHypothesis(
                hypothesis_id=str(h.get("hypothesis_id", f"h{i}")),
                statement=str(h.get("statement", "")),
                pass_criteria=str(h.get("pass_criteria", "")),
                fail_criteria=str(h.get("fail_criteria", "")),
                metric=h.get("metric"),
                threshold=threshold_val,
                evaluation_window=h.get("evaluation_window"),
            )
        )

    notes_raw = raw.get("experiment_translation_notes") or {}
    notes = ExperimentTranslationNotes(
        missing_info=[str(x) for x in notes_raw.get("missing_info", []) or []],
        hft3_implementation_reqs=[
            str(x) for x in notes_raw.get("hft3_implementation_reqs", []) or []
        ],
        confidence=notes_raw.get("confidence"),
    )

    return {
        "thesis": thesis,
        "assumptions": assumptions,
        "required_data": required_data,
        "required_features": required_features,
        "signal": signal,
        "execution": execution,
        "parameters": parameters,
        "failure_modes": failure_modes,
        "hypotheses": hypotheses,
        "notes": notes,
    }


def _heuristic_intake_payload(text: str) -> Dict[str, Any]:
    """Fallback when the LLM fails or is disabled: minimal valid bundle,
    marked quarantined."""
    snippet = text.strip()[:500]
    return {
        "thesis_summary": {
            "main_thesis": f"Heuristic summary: {snippet[:200]}",
            "instrument_scope": [],
            "time_horizon": "",
        },
        "assumptions": [],
        "required_data": [],
        "required_features": [],
        "proposed_signal_logic": {"signal": "", "entry": [], "exit": []},
        "proposed_execution_logic": {"order_types": []},
        "parameter_ranges": [],
        "failure_modes": [],
        "testable_hypotheses": [],
        "experiment_translation_notes": {
            "missing_info": [
                "llm_unavailable_or_parse_failed",
                "manual_review_required",
            ],
            "hft3_implementation_reqs": [
                "rerun intake with --no-quarantine flag once LLM is available",
            ],
        },
    }


def generate_intake_payload(
    text: str,
    *,
    model: str = DEFAULT_PIPELINE_MODEL,
    max_chars: int = 24000,
) -> tuple[Dict[str, Any], Optional[str]]:
    """Single LLM call that returns the 10 top-level fields of the intake
    payload. Falls back to a heuristic bundle on parse error and tags it
    `quarantine=True`."""
    snippet = text[:max_chars]
    user = f"Document:\n\n{snippet}"
    data, err = generate_json(_INTAKE_SYSTEM, user, model=model)
    if data is None:
        return _heuristic_intake_payload(text), err or "json_parse_failed"
    return data, None


def ingest_research_bundle(
    source: Union[str, Path],
    research_id: str,
    intake_dir: Path,
    *,
    use_llm: bool = True,
    model: str = DEFAULT_PIPELINE_MODEL,
) -> Path:
    """End-to-end intake: extract text → LLM structured fields → write 14 files.

    Returns the bundle directory. The bundle is always written, even when
    the LLM fails — `experiment_translation_notes.json` will have
    `quarantine=True` and populated `quarantine_reasons`.
    """
    from research_pipeline.intake_bundle import write_intake_bundle

    text = extract_text(source)
    if use_llm:
        raw, _ = generate_intake_payload(text, model=model)
    else:
        raw = _heuristic_intake_payload(text)
    payload = _parse_intake_payload(raw)
    return write_intake_bundle(
        research_id=research_id,
        source_path=Path(source),
        intake_dir=intake_dir,
        extracted_text=text,
        thesis_summary=payload["thesis"],
        assumptions=payload["assumptions"],
        required_data=payload["required_data"],
        required_features=payload["required_features"],
        signal_logic=payload["signal"],
        execution_logic=payload["execution"],
        parameter_ranges=payload["parameters"],
        failure_modes=payload["failure_modes"],
        testable_hypotheses=payload["hypotheses"],
        translation_notes=payload["notes"],
    )

