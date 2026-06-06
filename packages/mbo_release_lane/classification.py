"""Classify vendor files as true MBO before import — reject MBP/L2/snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from mbo_release_lane.blockers import BlockerCode, BlockerReport
from mbo_release_lane.constants import LIFECYCLE_ACTIONS, MBO_SCHEMA


@dataclass(frozen=True)
class DatasetClassification:
    is_true_mbo: bool
    reject_reasons: tuple[str, ...]
    schema: str | None
    sample_has_order_id: bool
    sample_has_lifecycle: bool
    sample_has_ordering: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_mbo": {
                "required": [
                    "order_id",
                    "order lifecycle events",
                    "side",
                    "price",
                    "size",
                    "timestamp",
                    "deterministic ordering",
                ],
                "passed": self.is_true_mbo,
            },
            "reject_from_mbo_lane": {
                "reasons": list(self.reject_reasons),
            },
            "schema": self.schema,
            "sample_has_order_id": self.sample_has_order_id,
            "sample_has_lifecycle": self.sample_has_lifecycle,
            "sample_has_ordering": self.sample_has_ordering,
        }


def _load_dbn_store(path: Path):
    try:
        import databento as db
    except ImportError as exc:
        raise RuntimeError("databento package required for MBO classification") from exc
    return db.DBNStore.from_file(str(path))


def _schema_from_store(store) -> str | None:
    meta = getattr(store, "metadata", None)
    if meta is None:
        return None
    return str(getattr(meta, "schema", None) or getattr(meta, "schema_id", "") or "").lower() or None


def _normalize_action(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    mapping = {
        "a": "add",
        "add": "add",
        "c": "cancel",
        "cancel": "cancel",
        "m": "modify",
        "modify": "modify",
        "t": "trade",
        "trade": "trade",
        "f": "fill",
        "fill": "fill",
        "r": "delete",
        "clear": "delete",
        "delete": "delete",
    }
    if s in mapping:
        return mapping[s]
    if s in LIFECYCLE_ACTIONS:
        return s
    return None


def classify_databento_store(path: Path, *, max_sample: int = 500) -> DatasetClassification:
    """Inspect raw DBN and classify before routing into MBO lane."""
    store = _load_dbn_store(path)
    schema = _schema_from_store(store)
    reject: list[str] = []

    if schema and schema not in ("mbo", MBO_SCHEMA):
        if "mbp" in schema or "mbo" not in schema:
            reject.append(f"schema={schema} (aggregated or non-MBO)")

    has_order_id = False
    has_lifecycle = False
    has_ordering = False
    prev_seq: int | None = None
    n = 0

    for rec in store:
        n += 1
        if n > max_sample:
            break
        oid = getattr(rec, "order_id", None)
        if oid is not None and int(oid) != 0:
            has_order_id = True
        action = _normalize_action(getattr(rec, "action", None))
        if action in LIFECYCLE_ACTIONS:
            has_lifecycle = True
        seq = getattr(rec, "sequence", None)
        if seq is not None:
            seq_i = int(seq)
            if prev_seq is not None and seq_i >= prev_seq:
                has_ordering = True
            elif prev_seq is None:
                has_ordering = True
            prev_seq = seq_i
        ts = getattr(rec, "ts_event", None)
        if ts is not None and int(ts) > 0 and seq is not None:
            has_ordering = True

    if n == 0:
        reject.append("empty file")
    if not has_order_id:
        reject.append("no order_id")
    if not has_lifecycle:
        reject.append("no add/cancel/modify/fill lifecycle")
    if not has_ordering:
        reject.append("missing deterministic event ordering")

    is_true = len(reject) == 0
    return DatasetClassification(
        is_true_mbo=is_true,
        reject_reasons=tuple(reject),
        schema=schema,
        sample_has_order_id=has_order_id,
        sample_has_lifecycle=has_lifecycle,
        sample_has_ordering=has_ordering,
    )


def classify_normalized_events(events: Iterable[dict[str, Any]]) -> DatasetClassification:
    """Classify already-normalized event dicts (for tests)."""
    reject: list[str] = []
    all_have_order_id = True
    has_lifecycle = False
    has_ordering = False
    prev_seq: int | None = None
    n = 0

    for ev in events:
        n += 1
        oid = ev.get("order_id")
        if oid is None or str(oid) in ("", "0", "None"):
            all_have_order_id = False
        action = str(ev.get("action", "")).lower()
        if action in LIFECYCLE_ACTIONS:
            has_lifecycle = True
        seq = ev.get("sequence_number")
        if seq is not None:
            seq_i = int(seq)
            if prev_seq is None or seq_i >= prev_seq:
                has_ordering = True
            prev_seq = seq_i

    has_order_id = all_have_order_id and n > 0

    if n == 0:
        reject.append("empty stream")
    if not has_order_id:
        reject.append("no order_id")
    if not has_lifecycle:
        reject.append("no add/cancel/modify/fill lifecycle")
    if not has_ordering:
        reject.append("missing deterministic event ordering")

    return DatasetClassification(
        is_true_mbo=len(reject) == 0,
        reject_reasons=tuple(reject),
        schema=MBO_SCHEMA,
        sample_has_order_id=has_order_id,
        sample_has_lifecycle=has_lifecycle,
        sample_has_ordering=has_ordering,
    )


def assert_true_mbo(path: Path) -> DatasetClassification:
    """Raise if file cannot enter MBO lane."""
    result = classify_databento_store(path)
    if not result.is_true_mbo:
        reasons = ", ".join(result.reject_reasons)
        raise ValueError(f"Rejected from MBO lane ({path.name}): {reasons}")
    return result


def classification_to_blockers(result: DatasetClassification) -> BlockerReport:
    report = BlockerReport()
    if result.is_true_mbo:
        return report
    for reason in result.reject_reasons:
        if "order_id" in reason:
            report.add(BlockerCode.MISSING_ORDER_ID, reason)
        elif "schema" in reason or "mbp" in reason.lower():
            report.add(BlockerCode.VENDOR_MISLABELED_MBO, reason)
        elif "ordering" in reason:
            report.add(BlockerCode.MISSING_SEQUENCE, reason)
        elif "lifecycle" in reason:
            report.add(BlockerCode.NOT_TRUE_MBO, reason)
        elif "empty" in reason:
            report.add(BlockerCode.EMPTY_EVENT_STREAM, reason)
        else:
            report.add(BlockerCode.NOT_TRUE_MBO, reason)
    return report
