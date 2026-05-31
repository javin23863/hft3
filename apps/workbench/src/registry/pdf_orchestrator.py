"""Topological execution of PDF model dependencies with cached instances."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

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


def _closure(model_ids: Iterable[str]) -> Set[str]:
    needed: Set[str] = set(model_ids)
    stack = list(model_ids)
    while stack:
        mid = stack.pop()
        for dep in MODEL_DEPENDENCY_MAP.get(mid, []):
            if dep not in needed:
                needed.add(dep)
                stack.append(dep)
    return needed


class PdfOrchestrator:
    """Run PDF models in dependency order; cache singletons per orchestrator instance."""

    def __init__(self) -> None:
        self._models = {m.model_id: m for m in get_structural_models()}
        self._outputs: Dict[str, Any] = {}

    def _inject_deps(self, mid: str, call_kwargs: dict[str, Any]) -> None:
        for dep in MODEL_DEPENDENCY_MAP.get(mid, []):
            if dep == "BOOK_PRESSURE" and "book_pressure" not in call_kwargs:
                call_kwargs["book_pressure"] = self._outputs.get("BOOK_PRESSURE")
            if dep == "BOOK_PRESSURE" and "book_pressure_by_asset" not in call_kwargs:
                bp = self._outputs.get("BOOK_PRESSURE")
                if bp is not None and bp.payload is not None:
                    sym = call_kwargs.get("symbol", "MES")
                    call_kwargs.setdefault("book_pressure_by_asset", {sym: bp.payload})
            if dep == "VPIN_TOXICITY" and "vpin" not in call_kwargs:
                call_kwargs["vpin"] = self._outputs.get("VPIN_TOXICITY")
            if dep == "HYBRID_EXECUTION":
                h4 = self._outputs.get("HYBRID_EXECUTION")
                if h4 is not None and h4.payload is not None:
                    call_kwargs["hybrid_reservation_price"] = h4.payload.hybrid_reservation_price
                    call_kwargs["pdf_model_4_output"] = h4.payload

    def run_subset(self, model_ids: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Run only requested models plus dependency closure in topo order."""
        needed = _closure(model_ids)
        for mid in _topo_sort():
            if mid not in needed:
                continue
            model = self._models[mid]
            call_kwargs = dict(kwargs)
            self._inject_deps(mid, call_kwargs)
            out = model.evaluate(**call_kwargs)
            self._outputs[mid] = out
        return {k: v for k, v in self._outputs.items() if k in needed}

    def run_all(self, **kwargs: Any) -> Dict[str, Any]:
        return self.run_subset(list(MODEL_DEPENDENCY_MAP.keys()), **kwargs)

    def get_output(self, model_id: str) -> Any:
        return self._outputs.get(model_id)

    def clear(self) -> None:
        self._outputs.clear()
