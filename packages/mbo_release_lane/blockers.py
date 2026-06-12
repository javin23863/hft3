"""Explicit blockers for MBO import/replay — no silent bypass."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BlockerCode(str, Enum):
    MISSING_ORDER_ID = "missing_order_id"
    MISSING_SEQUENCE = "missing_deterministic_sequence"
    MISSING_TIMESTAMP = "missing_timestamp"
    MISSING_SIDE = "missing_side"
    MISSING_PRICE = "missing_price"
    MISSING_SIZE = "missing_size"
    SEQUENCE_GAP = "sequence_gap"
    OUT_OF_ORDER_UNRESOLVED = "out_of_order_unresolved"
    NEGATIVE_SIZE = "negative_size"
    INVALID_PRICE = "invalid_price"
    BOOK_RECONSTRUCTION_FAILURE = "book_reconstruction_failure"
    POST_RELEASE_LEAK = "post_release_data_leaking_into_pre_release"
    VENDOR_MISLABELED_MBO = "vendor_file_mislabeled_as_mbo"
    NOT_TRUE_MBO = "not_true_mbo"
    DUPLICATE_EVENT = "duplicate_event"
    EMPTY_EVENT_STREAM = "empty_event_stream"


@dataclass
class Blocker:
    code: BlockerCode
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class BlockerReport:
    blockers: list[Blocker] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return len(self.blockers) > 0

    def add(self, code: BlockerCode, message: str, **context: Any) -> None:
        self.blockers.append(Blocker(code=code, message=message, context=context))

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocker_count": len(self.blockers),
            "blockers": [
                {"code": b.code.value, "message": b.message, "context": b.context}
                for b in self.blockers
            ],
        }
