"""Cross-asset normalized metadata envelope for imbalance features."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ImbalanceLineageEnvelope:
    asset_class: str
    source: str
    venue: str
    instrument_id: str
    root_symbol: str = ""
    contract_symbol: str = ""
    underlying_symbol: str = ""
    expiry: str = ""
    strike: Optional[float] = None
    option_type: str = ""
    futures_contract_month: str = ""
    tick_size: float = 0.0
    multiplier: float = 1.0
    session_id: str = ""
    event_window_id: str = ""
    timestamp_event_ns: int = 0
    timestamp_receive_ns: Optional[int] = None
    timestamp_process_ns: Optional[int] = None
    data_schema: str = ""
    data_class: str = ""
    data_granularity: str = ""
    feature_source: str = ""
    feature_family: str = ""
    feature_version: str = "1.0.0"
    config_hash: str = ""
    roll_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def config_hash(config: dict) -> str:
    blob = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def build_envelope(
    *,
    asset_class: str,
    source: str,
    venue: str,
    instrument_id: str,
    data_schema: str,
    data_class: str,
    feature_family: str,
    feature_source: str,
    timestamp_event_ns: int,
    config: Optional[dict] = None,
    **kwargs: Any,
) -> ImbalanceLineageEnvelope:
    return ImbalanceLineageEnvelope(
        asset_class=asset_class,
        source=source,
        venue=venue,
        instrument_id=instrument_id,
        data_schema=data_schema,
        data_class=data_class,
        data_granularity=kwargs.pop("data_granularity", data_schema),
        feature_source=feature_source,
        feature_family=feature_family,
        timestamp_event_ns=timestamp_event_ns,
        config_hash=config_hash(config or {}),
        **kwargs,
    )
