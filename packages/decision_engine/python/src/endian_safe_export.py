"""Deprecated: use decision_engine.python.src.walk_forward.export_weights_to_cpp.

This module is retained as a shim so that any external callers importing from
this file path continue to work.  ``export_weights_portable`` now delegates to
the consolidated ``export_weights_to_cpp``, which writes explicit little-endian
output that matches what the C++ ``DecisionEngine::load_model`` expects.

``load_weights_portable`` and ``verify_weights_integrity`` are kept here as
they have no counterpart in walk_forward.py (they serve as a round-trip
verification tool) but they are updated to read the same little-endian v1
format written by ``export_weights_to_cpp``.
"""
from __future__ import annotations

import struct
import warnings
from pathlib import Path
from typing import Dict, List, Tuple

from decision_engine.python.src.walk_forward import export_weights_to_cpp

# ---------------------------------------------------------------------------
# Constants (kept for import compatibility)
# ---------------------------------------------------------------------------
MAGIC = 0x48465433
VERSION = 1  # aligned with export_weights_to_cpp header version


def export_weights_portable(
    weights: List[float],
    output_path: str,
    model_id: int = 1,
    feature_count: int | None = None,
    max_weights: int = 1024,
) -> str:
    """Deprecated wrapper — delegates to ``export_weights_to_cpp``.

    The ``max_weights`` parameter is accepted for backward compatibility but
    the C++ reader always expects exactly 1024 weight slots.
    """
    warnings.warn(
        "export_weights_portable is deprecated; use export_weights_to_cpp "
        "from decision_engine.python.src.walk_forward instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    export_weights_to_cpp(
        weights,
        output_path,
        model_id=model_id,
        feature_count=feature_count,
    )
    return output_path


def load_weights_portable(path: str) -> Tuple[List[float], Dict[str, int]]:
    """Read a model file written by ``export_weights_to_cpp`` (little-endian v1).

    Header layout (little-endian uint32 x4, 16 bytes total):
        magic, version, model_id, feature_count

    Body: 1024 little-endian IEEE-754 doubles.
    """
    HEADER_FORMAT = "<IIII"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    BODY_SIZE = 1024 * 8  # 1024 doubles

    data = Path(path).read_bytes()
    expected = HEADER_SIZE + BODY_SIZE
    if len(data) != expected:
        raise ValueError(
            f"File size {len(data)} != expected {expected} "
            f"(header {HEADER_SIZE} + 1024 * 8 body bytes)"
        )

    magic, version, model_id, feature_count = struct.unpack_from(HEADER_FORMAT, data, 0)
    if magic != MAGIC:
        raise ValueError(f"Invalid magic: 0x{magic:08X}, expected 0x{MAGIC:08X}")

    values = struct.unpack_from(f"<1024d", data, HEADER_SIZE)
    weights = [float(v) for v in values[:feature_count]]
    metadata: Dict[str, int] = {
        "model_id": model_id,
        "feature_count": feature_count,
        "active_weights": feature_count,
        "total_slots": 1024,
        "version": version,
    }
    return weights, metadata


def verify_weights_integrity(path: str) -> bool:
    """Return True if the file passes header validation and has active weights."""
    try:
        _, metadata = load_weights_portable(path)
        return (
            metadata["active_weights"] > 0
            and metadata["total_slots"] >= metadata["active_weights"]
            and metadata["version"] == VERSION
        )
    except Exception:
        return False
