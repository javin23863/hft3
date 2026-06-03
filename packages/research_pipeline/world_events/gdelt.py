"""Independent GDELT world-event connector for offline backend use."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from research_pipeline.world_events.models import (
    WorldEventActor,
    WorldEventFetchResult,
    WorldEventLocation,
    WorldEventProvenance,
    WorldEventQuery,
    WorldEventRecord,
    WorldEventSource,
)

GDELT_EVENTS_ENDPOINT = "https://api.gdeltproject.org/api/v2/events/events"
GDELT_PROVIDER_VERSION = "gdelt_2_1_events"


def build_gdelt_query_url(query: WorldEventQuery) -> str:
    params = {
        "format": "json",
        "query": query.query,
        "startdatetime": f"{query.start_yyyymmdd}000000",
        "enddatetime": f"{query.end_yyyymmdd}235959",
        "maxrecords": str(query.max_records),
    }
    return f"{GDELT_EVENTS_ENDPOINT}?{urlencode(params)}"


def parse_gdelt_events_payload(
    payload: str | bytes | dict[str, Any] | list[dict[str, Any]],
    *,
    request_url: str,
    fetched_at_utc: str | None = None,
) -> tuple[WorldEventRecord, ...]:
    if isinstance(payload, bytes):
        data: Any = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        data = json.loads(payload)
    else:
        data = payload

    rows = data.get("events", []) if isinstance(data, dict) else data
    fetched_at = fetched_at_utc or datetime.now(timezone.utc).isoformat()
    return tuple(
        normalize_gdelt_event(row, request_url=request_url, fetched_at_utc=fetched_at)
        for row in rows
    )


def normalize_gdelt_event(
    row: dict[str, Any],
    *,
    request_url: str,
    fetched_at_utc: str,
) -> WorldEventRecord:
    event_id = _required_text(row, "GLOBALEVENTID", "GlobalEventID", "globalEventID")
    event_date = _required_text(row, "SQLDATE", "EventTimeDate", "eventDate")[:8]
    source_url = _text(row, "SOURCEURL", "SourceURL", "sourceUrl")
    provenance = WorldEventProvenance(
        provider=WorldEventSource.GDELT_EVENTS,
        provider_version=GDELT_PROVIDER_VERSION,
        request_url=request_url,
        fetched_at_utc=fetched_at_utc,
        source_url=source_url,
    )
    return WorldEventRecord(
        event_id=event_id,
        event_date=event_date,
        actor1=WorldEventActor(
            code=_text(row, "Actor1Code"),
            name=_text(row, "Actor1Name"),
            country_code=_text(row, "Actor1CountryCode"),
        ),
        actor2=WorldEventActor(
            code=_text(row, "Actor2Code"),
            name=_text(row, "Actor2Name"),
            country_code=_text(row, "Actor2CountryCode"),
        ),
        event_code=_text(row, "EventCode"),
        event_root_code=_text(row, "EventRootCode"),
        quad_class=_text(row, "QuadClass"),
        goldstein_scale=_float(row, "GoldsteinScale"),
        avg_tone=_float(row, "AvgTone"),
        num_mentions=_int(row, "NumMentions"),
        location=WorldEventLocation(
            name=_text(row, "ActionGeo_FullName"),
            country_code=_text(row, "ActionGeo_CountryCode"),
            latitude=_float(row, "ActionGeo_Lat"),
            longitude=_float(row, "ActionGeo_Long"),
        ),
        provenance=provenance,
        raw=dict(row),
    )


class GdeltWorldEventConnector:
    """Explicit network connector; importing this module performs no fetch."""

    def fetch(self, query: WorldEventQuery, *, timeout_s: float = 30.0) -> WorldEventFetchResult:
        request_url = build_gdelt_query_url(query)
        fetched_at = datetime.now(timezone.utc).isoformat()
        with urlopen(request_url, timeout=timeout_s) as response:  # nosec B310: explicit user-run fetcher
            payload = response.read()
        records = parse_gdelt_events_payload(
            payload,
            request_url=request_url,
            fetched_at_utc=fetched_at,
        )
        return WorldEventFetchResult(
            query=query,
            request_url=request_url,
            records=records,
            fetched_at_utc=fetched_at,
        )


def _text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _required_text(row: dict[str, Any], *keys: str) -> str:
    value = _text(row, *keys)
    if not value:
        raise ValueError(f"missing required GDELT field: {'/'.join(keys)}")
    return value


def _float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    return float(value)


def _int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    return int(value)


__all__ = [
    "GDELT_EVENTS_ENDPOINT",
    "GDELT_PROVIDER_VERSION",
    "GdeltWorldEventConnector",
    "build_gdelt_query_url",
    "normalize_gdelt_event",
    "parse_gdelt_events_payload",
]
