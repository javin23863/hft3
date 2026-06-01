"""Post-WFC robust plateau selection (runs only after WFC PASS)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def select_robust_plateau(
    matrix_rows: List[Dict[str, Any]],
    *,
    primary_metric: str = "sharpe",
) -> Optional[Dict[str, Any]]:
    """Pick stable high-IS params; IS-only (no OOS metrics for final selection)."""
    if not matrix_rows:
        return None

    by_hash: Dict[str, Dict[str, Any]] = {}
    for row in matrix_rows:
        ph = str(row.get("parameter_hash", ""))
        if not ph:
            continue
        is_m = row.get("is_metrics") or {}
        is_val = float(is_m.get(primary_metric, 0.0))
        bucket = by_hash.setdefault(ph, {"params": row.get("params"), "is_vals": []})
        bucket["is_vals"].append(is_val)
        if row.get("params"):
            bucket["params"] = row.get("params")

    scored: List[tuple[float, float, Dict[str, Any], str]] = []
    for ph, bucket in by_hash.items():
        is_vals = bucket["is_vals"]
        if not is_vals:
            continue
        mean_is = sum(is_vals) / len(is_vals)
        if mean_is <= 0:
            continue
        stability = min(is_vals)
        params = bucket.get("params")
        if not params:
            continue
        scored.append((mean_is, stability, params, ph))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    n_decile = max(1, len(scored) // 10)
    top = scored[:n_decile]
    best = max(top, key=lambda x: (x[1], x[0]))
    return dict(best[2], __plateau_hash__=best[3])
