"""Topological execution of PDF model dependencies with cached instances."""

from __future__ import annotations

from typing import Any, Dict, List

from features_engine.src.structural_models.registry import MODEL_DEPENDENCY_MAP, get_structural_models


def _topo_sort() -> List[str]:
    visited: set[str] = set()
    order: List[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for dep in MODEL_DEPENDENCY_MAP.get(node, []):
            visit(dep)
        order.append(node)

    for mid in MODEL_DEPENDENCY_MAP:
        visit(mid)
    return order


class PdfOrchestrator:
    """Run PDF models in dependency order; cache singletons per orchestrator instance."""

    def __init__(self) -> None:
        self._models = {m.model_id: m for m in get_structural_models()}
        self._outputs: Dict[str, Any] = {}

    def run_all(self, **kwargs: Any) -> Dict[str, Any]:
        for mid in _topo_sort():
            model = self._models[mid]
            call_kwargs = dict(kwargs)
            for dep in MODEL_DEPENDENCY_MAP.get(mid, []):
                if dep == "PDF_MODEL_1" and "book_pressure" not in call_kwargs:
                    call_kwargs["book_pressure"] = self._outputs.get("PDF_MODEL_1")
                if dep == "PDF_MODEL_1" and "book_pressure_by_asset" not in call_kwargs:
                    bp = self._outputs.get("PDF_MODEL_1")
                    if bp is not None:
                        sym = kwargs.get("symbol", "MES")
                        call_kwargs.setdefault("book_pressure_by_asset", {sym: bp.payload})
                if dep == "PDF_MODEL_3" and "vpin" not in call_kwargs:
                    call_kwargs["vpin"] = self._outputs.get("PDF_MODEL_3")
            out = model.evaluate(**call_kwargs)
            self._outputs[mid] = out
        return self._outputs

    def get_output(self, model_id: str) -> Any:
        return self._outputs.get(model_id)
