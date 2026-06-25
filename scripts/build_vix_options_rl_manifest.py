#!/usr/bin/env python3
"""Build a manifest-backed VIX.OPT clue surface for research-only RL campaigns."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "packages"):
    value = str(_path)
    if value not in sys.path:
        sys.path.insert(0, value)

from research_pipeline.rl_training_data import (  # noqa: E402
    VIX_OPTIONS_DEFAULT_RL_FEATURES,
    VIX_OPTIONS_DEFAULT_RL_REWARD_COLUMN,
    VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION,
    VIX_OPTIONS_RL_REWARD_COST_MODEL,
    VIX_OPTIONS_RL_REWARD_UNITS,
    VIX_OPTIONS_SUPPORTED_RL_FEATURES,
    vix_options_feature_schema_hash,
)

VIX_OPTIONS_SYMBOL = "VIX.OPT"
VIX_OPTIONS_SOURCE_FAMILY = "vix_options_clue"
MANIFEST_ROW_SCHEMA_VERSION = "hft3_vix_options_rl_manifest_row_v1"
_FEATURE_SUFFIX = re.compile(r"_features_v1\.npz$", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-store-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--symbol", default=VIX_OPTIONS_SYMBOL)
    parser.add_argument("--feature", action="append", default=None)
    parser.add_argument("--reward-column", default=VIX_OPTIONS_DEFAULT_RL_REWARD_COLUMN)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.feature_store_root)
    symbol = str(args.symbol).strip() or VIX_OPTIONS_SYMBOL
    if symbol != VIX_OPTIONS_SYMBOL:
        raise ValueError(f"this builder only supports {VIX_OPTIONS_SYMBOL}")
    features = tuple(args.feature or VIX_OPTIONS_DEFAULT_RL_FEATURES)
    _require_known_vix_features(features)
    reward_column = str(args.reward_column).strip()
    _require_known_vix_features([reward_column])
    out = Path(args.out) if args.out is not None else root / "vix_options_rl_manifest.jsonl"
    files = sorted((root / symbol).glob(f"{symbol}_*_features_v1.npz"))
    if args.limit is not None:
        limit = _positive_int(args.limit, "limit")
        files = files[:limit]
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in files:
        try:
            rows.append(
                _manifest_row(root=root, symbol=symbol, path=path, features=features, reward_column=reward_column)
            )
        except ValueError as exc:
            if args.strict:
                raise
            skipped.append({"path": str(path), "reason": str(exc)})
    if not rows:
        raise ValueError(f"no VIX.OPT feature files found under {root / symbol}")
    _write_jsonl(out, rows)
    print(
        json.dumps(
            {
                "status": "built",
                "out": str(out),
                "row_count": len(rows),
                "source_rows": sum(int(row["source_rows"]) for row in rows),
                "skipped_file_count": len(skipped),
                "feature_schema_hash": rows[0]["feature_schema_hash"],
                "reward_column": reward_column,
            },
            sort_keys=True,
        )
    )
    return 0


def _manifest_row(
    *,
    root: Path,
    symbol: str,
    path: Path,
    features: Sequence[str],
    reward_column: str,
) -> dict[str, Any]:
    arrays = _load_numeric_arrays(path)
    ts = np.asarray(arrays.get("ts"), dtype=np.int64)
    if ts.ndim != 1 or len(ts) < 2:
        raise ValueError(f"VIX.OPT feature file has insufficient ts rows: {path}")
    if not np.all(np.diff(ts) >= 0):
        raise ValueError(f"VIX.OPT feature file ts not monotonic: {path}")
    ts_event_raw = _required_1d_array(arrays, "ts_event_raw", path)
    ts_recv_raw = _required_1d_array(arrays, "ts_recv_raw", path)
    if ts_event_raw.shape[0] != ts.shape[0] or ts_recv_raw.shape[0] != ts.shape[0]:
        raise ValueError(f"VIX.OPT audit timestamp shape mismatch: {path}")
    if not np.all(np.diff(ts_recv_raw) >= 0):
        raise ValueError(f"VIX.OPT ts_recv_raw not monotonic: {path}")
    causal_mask = (ts_recv_raw <= ts) & (ts_event_raw <= ts_recv_raw)
    causal_row_count = int(np.count_nonzero(causal_mask))
    if causal_row_count < 2:
        raise ValueError(f"VIX.OPT feature file has fewer than two causal timestamp rows: {path}")
    for column in sorted(set(features) | {reward_column}):
        values = np.asarray(arrays.get(column), dtype=np.float64)
        if values.ndim != 1 or values.shape[0] != ts.shape[0]:
            raise ValueError(f"VIX.OPT feature column {column!r} shape mismatch: {path}")
    digest = _sha256_file(path)
    schema_hash = vix_options_feature_schema_hash(feature_names=features, reward_column=reward_column)
    event_id = _event_id_from_feature_path(symbol=symbol, path=path)
    rel = path.relative_to(root)
    return {
        "schema_version": MANIFEST_ROW_SCHEMA_VERSION,
        "symbol": symbol,
        "event_id": event_id,
        "store_path": rel.as_posix(),
        "source_rows": int(ts.shape[0]),
        "source_row_count": int(ts.shape[0]),
        "content_hash": digest,
        "store_sha256": digest,
        "source_family": VIX_OPTIONS_SOURCE_FAMILY,
        "store_schema_version": VIX_OPTIONS_RL_FEATURE_STORE_SCHEMA_VERSION,
        "feature_schema_hash": schema_hash,
        "feature_names": list(features),
        "reward_column": reward_column,
        "timestamp_bounds": {
            "ts_min": int(ts.min()),
            "ts_max": int(ts.max()),
            "ts_recv_raw_min": int(ts_recv_raw.min()),
            "ts_recv_raw_max": int(ts_recv_raw.max()),
            "ts_event_raw_min": int(ts_event_raw.min()),
            "ts_event_raw_max": int(ts_event_raw.max()),
            "ts_recv_raw_lte_ts": bool(np.all(ts_recv_raw <= ts)),
            "ts_event_raw_lte_ts_recv_raw": bool(np.all(ts_event_raw <= ts_recv_raw)),
            "causal_row_count": causal_row_count,
            "noncausal_row_count": int(ts.shape[0] - causal_row_count),
        },
        "reward_rule": {
            "name": "future_vix_options_clue_delta",
            "reward_units": VIX_OPTIONS_RL_REWARD_UNITS,
            "cost_model": VIX_OPTIONS_RL_REWARD_COST_MODEL,
            "label_only": True,
            "execution_claim": False,
        },
        "decision_time_boundary": (
            "VIX options clue rows use ts_recv-derived availability time; "
            "future clue deltas are labels only"
        ),
    }


def _load_numeric_arrays(path: Path) -> dict[str, np.ndarray]:
    with np.load(str(path), allow_pickle=False) as arch:
        arrays: dict[str, np.ndarray] = {}
        for key in arch.files:
            if key == "_attrs_json":
                continue
            arr = arch[key]
            if np.issubdtype(arr.dtype, np.number):
                arrays[key] = arr
        return arrays


def _required_1d_array(arrays: dict[str, np.ndarray], key: str, path: Path) -> np.ndarray:
    if key not in arrays:
        raise ValueError(f"VIX.OPT feature file missing {key}: {path}")
    values = np.asarray(arrays[key], dtype=np.int64)
    if values.ndim != 1:
        raise ValueError(f"VIX.OPT feature file {key} must be 1D: {path}")
    return values


def _event_id_from_feature_path(*, symbol: str, path: Path) -> str:
    name = path.name
    prefix = f"{symbol}_"
    if not name.startswith(prefix):
        raise ValueError(f"unexpected VIX.OPT feature filename: {path}")
    return _FEATURE_SUFFIX.sub("", name[len(prefix) :])


def _require_known_vix_features(features: Sequence[str]) -> None:
    unknown = sorted(set(str(feature) for feature in features) - set(VIX_OPTIONS_SUPPORTED_RL_FEATURES))
    if unknown:
        raise ValueError("unknown VIX options RL features: " + ", ".join(unknown))


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    tmp.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_int(value: Any, label: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
