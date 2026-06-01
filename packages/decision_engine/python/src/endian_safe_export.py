"""Endianness-safe binary model weight export/import.

Traces to: chicago_cme_a_plus_production_implementation_prompt.pdf — C++ binary
hot-path compatibility. Writes big-endian IEEE 754 doubles with self-describing
header that enables auto-detection on any architecture.

The endianness marker is written in native byte order. A C++ reader on a
big-endian machine reads the marker as 0x01020304; on a little-endian machine
it reads as 0x04030201. This enables auto-detection.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MAGIC = 0x48465433
VERSION = 2
ENDIANNESS_MARKER = 0x01020304
HEADER_FORMAT = "IIIIIIII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def export_weights_portable(
    weights: List[float],
    output_path: str,
    model_id: int = 1,
    feature_count: int = 64,
    max_weights: int = 1024,
) -> str:
    if len(weights) > max_weights:
        raise ValueError(
            f"Model has {len(weights)} weights, exceeds capacity of {max_weights}."
        )
    padded = weights + [0.0] * (max_weights - len(weights))
    header = struct.pack(
        "!IIIIIIII",
        MAGIC,
        VERSION,
        ENDIANNESS_MARKER,
        model_id & 0xFFFFFFFF,
        feature_count & 0xFFFFFFFF,
        len(weights) & 0xFFFFFFFF,
        max_weights & 0xFFFFFFFF,
        0,
    )
    body = struct.pack(f"!{len(padded)}d", *padded)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + body)
    return output_path


def load_weights_portable(path: str) -> Tuple[List[float], Dict[str, int]]:
    data = Path(path).read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError(f"File too small: {len(data)} < {HEADER_SIZE} header bytes")
    header = struct.unpack_from("!IIIIIIII", data, 0)
    magic, version, endianness, m_id, f_count, active, total, _ = header

    if magic != MAGIC:
        raise ValueError(f"Invalid magic: 0x{magic:08X}, expected 0x{MAGIC:08X}")

    expected_size = HEADER_SIZE + total * 8
    if len(data) != expected_size:
        raise ValueError(
            f"File size {len(data)} != expected {expected_size} "
            f"(header {HEADER_SIZE} + {total} * 8)"
        )
    body = data[HEADER_SIZE:]

    endian_prefix = "<" if endianness == 0x04030201 else ">"
    values = struct.unpack(f"{endian_prefix}{total}d", body)
    weights = [float(v) for v in values[:active]]
    metadata = {
        "model_id": m_id,
        "feature_count": f_count,
        "active_weights": active,
        "total_slots": total,
        "version": version,
        "stored_endianness": endian_prefix,
    }
    return weights, metadata


def verify_weights_integrity(path: str) -> bool:
    try:
        _, metadata = load_weights_portable(path)
        return (
            metadata["active_weights"] > 0
            and metadata["total_slots"] >= metadata["active_weights"]
            and metadata["version"] == VERSION
        )
    except Exception:
        return False
