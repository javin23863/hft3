from __future__ import annotations

SCHEMA_VERSION = "normalized_v1"

REQUIRED_FIELDS = (
    "source",
    "capture_environment",
    "symbol",
    "exchange",
    "event_type",
    "local_receive_timestamp_ns",
    "local_write_timestamp_ns",
)

OPTIONAL_FIELDS = (
    "contract",
    "exchange_timestamp_ns",
    "price",
    "size",
    "side",
    "bid_levels",
    "ask_levels",
    "bid_price",
    "ask_price",
    "bid_size",
    "ask_size",
    "order_id",
    "fill_id",
    "sequence",
    "gateway_metadata",
)
