"""Crypto order-ack latency resolver for backtest replay.

Resolution rungs per LATENCY.md §3:
  1. cli_latency_ms supplied → validate in-band, return.
  2. Measured summary present (paper_order_latency.measured == true,
     order_ack_p99_ms > 0) → return authoritative value.
  3. Else raise ValueError (UNMEASURED).

LATENCY.md §10 and CRYPTO_LIVE.md §7: latency_ms_at_promotion is drawn
from this resolver at bundle promotion time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CRYPTO_LATENCY_BANDS_MS: list[float] = [5.0, 50.0, 200.0]

_BAND_MIN_MS: float = CRYPTO_LATENCY_BANDS_MS[0]   # 5.0
_BAND_MAX_MS: float = CRYPTO_LATENCY_BANDS_MS[-1]  # 200.0

DEFAULT_CRYPTO_SUMMARY_REL = Path("runtime/crypto_latency/latency_summary.json")


def default_crypto_summary_path(repo_root: Path) -> Path:
    """Return absolute path to the default crypto latency summary artifact."""
    return repo_root / DEFAULT_CRYPTO_SUMMARY_REL


def validate_crypto_replay_latency_ms(value: float) -> float:
    """Raise ValueError if *value* is outside LATENCY.md §3 band [5, 200] ms."""
    ms = float(value)
    if not (_BAND_MIN_MS <= ms <= _BAND_MAX_MS):
        raise ValueError(
            f"Crypto replay latency {ms} ms outside LATENCY.md §3 band "
            f"[{_BAND_MIN_MS}, {_BAND_MAX_MS}] ms"
        )
    return ms


def resolve_crypto_replay_latency_ms(
    *,
    cli_latency_ms: float | None = None,
    summary_path: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[float, str]:
    """Return (latency_ms, source_label) following LATENCY.md §3 resolution rungs.

    Rung 1 — cli_latency_ms not None: validate and return immediately.
    Rung 2 — load summary JSON; if paper_order_latency.measured == true AND
              order_ack_p99_ms present and > 0, return authoritative value.
    Rung 3 — raise ValueError (UNMEASURED).

    LATENCY.md §3/§10, CRYPTO_LIVE.md §7.
    """
    if cli_latency_ms is not None:
        ms = validate_crypto_replay_latency_ms(float(cli_latency_ms))
        return ms, "cli:--latency-ms"

    resolved_path: Path
    if summary_path is not None:
        resolved_path = summary_path
    else:
        root = repo_root if repo_root is not None else Path.cwd()
        resolved_path = default_crypto_summary_path(root)

    if not resolved_path.is_file():
        raise ValueError(
            f"UNMEASURED: crypto order-ack not measured; pass --latency-ms (LATENCY.md §3). "
            f"Summary not found: {resolved_path}"
        )

    try:
        summary: dict[str, Any] = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(
            f"UNMEASURED: crypto order-ack not measured; pass --latency-ms (LATENCY.md §3). "
            f"Summary unparseable at {resolved_path}: {exc}"
        ) from exc

    paper = summary.get("paper_order_latency") or {}
    p99 = summary.get("order_ack_p99_ms")
    if paper.get("measured") and isinstance(p99, (int, float)) and float(p99) > 0:
        return float(p99), "crypto_paper_order_latency.authoritative"

    raise ValueError(
        "UNMEASURED: crypto order-ack not measured; pass --latency-ms (LATENCY.md §3)"
    )


def is_crypto_latency_measured(summary_path: Path) -> bool:
    """Return True iff summary has a valid measured order-ack p99. Safe — never raises."""
    try:
        if not summary_path.is_file():
            return False
        summary: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))
        paper = summary.get("paper_order_latency") or {}
        p99 = summary.get("order_ack_p99_ms")
        return bool(paper.get("measured") and isinstance(p99, (int, float)) and float(p99) > 0)
    except Exception:
        return False
