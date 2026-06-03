"""Backend-only world-event data models for offline relationship review."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorldEventSource(str, Enum):
    GDELT_EVENTS = "gdelt_events"


@dataclass(frozen=True)
class WorldEventQuery:
    query: str
    start_yyyymmdd: str
    end_yyyymmdd: str
    max_records: int = 250


@dataclass(frozen=True)
class WorldEventActor:
    code: str | None = None
    name: str | None = None
    country_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "country_code": self.country_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldEventActor":
        return cls(
            code=data.get("code"),
            name=data.get("name"),
            country_code=data.get("country_code"),
        )


@dataclass(frozen=True)
class WorldEventLocation:
    name: str | None = None
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "country_code": self.country_code,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldEventLocation":
        return cls(
            name=data.get("name"),
            country_code=data.get("country_code"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )


@dataclass(frozen=True)
class WorldEventProvenance:
    provider: WorldEventSource
    provider_version: str
    request_url: str
    fetched_at_utc: str
    source_url: str | None = None
    transform_version: str = "hft3_world_event_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "provider_version": self.provider_version,
            "request_url": self.request_url,
            "fetched_at_utc": self.fetched_at_utc,
            "source_url": self.source_url,
            "transform_version": self.transform_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldEventProvenance":
        return cls(
            provider=WorldEventSource(data["provider"]),
            provider_version=data["provider_version"],
            request_url=data["request_url"],
            fetched_at_utc=data["fetched_at_utc"],
            source_url=data.get("source_url"),
            transform_version=data.get("transform_version", "hft3_world_event_v1"),
        )


@dataclass(frozen=True)
class WorldEventRecord:
    event_id: str
    event_date: str
    actor1: WorldEventActor
    actor2: WorldEventActor
    event_code: str | None
    event_root_code: str | None
    quad_class: str | None
    goldstein_scale: float | None
    avg_tone: float | None
    num_mentions: int | None
    location: WorldEventLocation
    provenance: WorldEventProvenance
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_date": self.event_date,
            "actor1": self.actor1.to_dict(),
            "actor2": self.actor2.to_dict(),
            "event_code": self.event_code,
            "event_root_code": self.event_root_code,
            "quad_class": self.quad_class,
            "goldstein_scale": self.goldstein_scale,
            "avg_tone": self.avg_tone,
            "num_mentions": self.num_mentions,
            "location": self.location.to_dict(),
            "provenance": self.provenance.to_dict(),
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldEventRecord":
        return cls(
            event_id=data["event_id"],
            event_date=data["event_date"],
            actor1=WorldEventActor.from_dict(data["actor1"]),
            actor2=WorldEventActor.from_dict(data["actor2"]),
            event_code=data.get("event_code"),
            event_root_code=data.get("event_root_code"),
            quad_class=data.get("quad_class"),
            goldstein_scale=data.get("goldstein_scale"),
            avg_tone=data.get("avg_tone"),
            num_mentions=data.get("num_mentions"),
            location=WorldEventLocation.from_dict(data["location"]),
            provenance=WorldEventProvenance.from_dict(data["provenance"]),
            raw=data.get("raw") or {},
        )


@dataclass(frozen=True)
class WorldEventFetchResult:
    query: WorldEventQuery
    request_url: str
    records: tuple[WorldEventRecord, ...]
    fetched_at_utc: str


__all__ = [
    "WorldEventActor",
    "WorldEventFetchResult",
    "WorldEventLocation",
    "WorldEventProvenance",
    "WorldEventQuery",
    "WorldEventRecord",
    "WorldEventSource",
]
