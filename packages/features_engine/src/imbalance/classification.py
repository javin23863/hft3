"""Explicit Databento-style data class labels and downgrade records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class DataClass(str, Enum):
    MBO = "MBO"
    MBP_10 = "MBP_10"
    MBP_1 = "MBP_1"
    TRADES = "TRADES"
    IMBALANCE = "IMBALANCE"
    DEFINITION = "DEFINITION"
    OHLCV = "OHLCV"


_SCHEMA_TO_CLASS = {
    "mbo": DataClass.MBO,
    "mbp-10": DataClass.MBP_10,
    "mbp10": DataClass.MBP_10,
    "mbp-1": DataClass.MBP_1,
    "mbp1": DataClass.MBP_1,
    "trades": DataClass.TRADES,
    "imbalance": DataClass.IMBALANCE,
    "definition": DataClass.DEFINITION,
    "ohlcv-1d": DataClass.OHLCV,
    "cbbo-1m": DataClass.MBP_1,
}


def schema_to_data_class(schema: str) -> DataClass:
    key = schema.lower().replace("_", "-")
    return _SCHEMA_TO_CLASS.get(key, DataClass.MBP_1)


def data_class_label(data_class: DataClass) -> str:
    """Human label; never call MBP_10 Level 3."""
    labels = {
        DataClass.MBO: "order-level MBO",
        DataClass.MBP_10: "aggregated MBP-10 depth (not Level 3)",
        DataClass.MBP_1: "top-of-book MBP-1",
        DataClass.TRADES: "transaction prints",
        DataClass.IMBALANCE: "auction imbalance feed",
        DataClass.DEFINITION: "instrument definition/reference",
        DataClass.OHLCV: "daily OHLCV bars",
    }
    return labels.get(data_class, data_class.value)


@dataclass
class DataClassResolution:
    requested_data_class: DataClass
    resolved_data_class: DataClass
    downgrade_reason: Optional[str] = None
    affected_symbols: List[str] = field(default_factory=list)
    affected_dates: List[str] = field(default_factory=list)
    affected_asset_class: str = ""
    feature_validity_impact: str = "none"
    promotion_eligibility_impact: str = "none"

    @property
    def was_downgraded(self) -> bool:
        return self.requested_data_class != self.resolved_data_class

    def to_dict(self) -> dict:
        return {
            "requested_data_class": self.requested_data_class.value,
            "resolved_data_class": self.resolved_data_class.value,
            "downgrade_reason": self.downgrade_reason,
            "affected_symbols": list(self.affected_symbols),
            "affected_dates": list(self.affected_dates),
            "affected_asset_class": self.affected_asset_class,
            "feature_validity_impact": self.feature_validity_impact,
            "promotion_eligibility_impact": self.promotion_eligibility_impact,
            "requested_label": data_class_label(self.requested_data_class),
            "resolved_label": data_class_label(self.resolved_data_class),
        }


def resolve_data_class(
    requested_schema: str,
    *,
    available_schema: Optional[str] = None,
    asset_class: str = "",
    symbols: Optional[List[str]] = None,
    dates: Optional[List[str]] = None,
) -> DataClassResolution:
    requested = schema_to_data_class(requested_schema)
    resolved = schema_to_data_class(available_schema or requested_schema)
    reason: Optional[str] = None
    validity = "none"
    promo = "none"
    if requested != resolved:
        reason = (
            f"requested {data_class_label(requested)}; "
            f"resolved {data_class_label(resolved)}"
        )
        if resolved in (DataClass.MBP_10, DataClass.MBP_1) and requested == DataClass.MBO:
            validity = "true_ofi_unavailable; book_imbalance_may_be_full_or_proxy"
            promo = "block_true_order_flow_imbalance"
        elif resolved == DataClass.MBP_1:
            validity = "book_proxy; trade_pressure_only_for_ofi"
            promo = "block_true_order_flow_imbalance"
    return DataClassResolution(
        requested_data_class=requested,
        resolved_data_class=resolved,
        downgrade_reason=reason,
        affected_symbols=list(symbols or []),
        affected_dates=list(dates or []),
        affected_asset_class=asset_class,
        feature_validity_impact=validity,
        promotion_eligibility_impact=promo,
    )


def feature_family_for_data_class(data_class: DataClass) -> str:
    if data_class == DataClass.MBO:
        return "order_flow_imbalance"
    if data_class in (DataClass.MBP_10, DataClass.MBP_1):
        return "order_flow_imbalance_proxy"
    if data_class == DataClass.TRADES:
        return "trade_pressure_only"
    if data_class == DataClass.IMBALANCE:
        return "auction_imbalance"
    return "book_imbalance"
