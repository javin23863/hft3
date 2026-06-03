from __future__ import annotations

from research_pipeline.world_events import (
    WorldEventQuery,
    build_gdelt_query_url,
    parse_gdelt_events_payload,
    read_world_event_cache,
    source_ref_for_world_event,
    write_world_event_cache,
)


def _gdelt_payload():
    return {
        "events": [
            {
                "GLOBALEVENTID": "123456789",
                "SQLDATE": "20240601",
                "Actor1Code": "USA",
                "Actor1Name": "UNITED STATES",
                "Actor1CountryCode": "USA",
                "Actor2Code": "CHN",
                "Actor2Name": "CHINA",
                "Actor2CountryCode": "CHN",
                "EventCode": "042",
                "EventRootCode": "04",
                "QuadClass": "1",
                "GoldsteinScale": "1.9",
                "AvgTone": "-2.5",
                "NumMentions": "12",
                "ActionGeo_FullName": "Taiwan Strait",
                "ActionGeo_CountryCode": "TW",
                "ActionGeo_Lat": "23.7",
                "ActionGeo_Long": "121.0",
                "SOURCEURL": "https://example.test/world-event-source",
            }
        ]
    }


def test_gdelt_connector_builds_backend_only_query_url():
    query = WorldEventQuery(
        query="geopolitical risk energy shipping",
        start_yyyymmdd="20240601",
        end_yyyymmdd="20240602",
        max_records=50,
    )

    url = build_gdelt_query_url(query)

    assert url.startswith("https://api.gdeltproject.org/api/v2/events/events?")
    assert "format=json" in url
    assert "startdatetime=20240601000000" in url
    assert "enddatetime=20240602235959" in url
    assert "maxrecords=50" in url


def test_parse_gdelt_events_payload_normalizes_records_with_provenance():
    records = parse_gdelt_events_payload(
        _gdelt_payload(),
        request_url="https://api.gdeltproject.org/api/v2/events/events?query=x",
        fetched_at_utc="2026-06-03T00:00:00+00:00",
    )

    record = records[0]
    assert record.event_id == "123456789"
    assert record.event_date == "20240601"
    assert record.actor1.name == "UNITED STATES"
    assert record.actor2.country_code == "CHN"
    assert record.event_code == "042"
    assert record.goldstein_scale == 1.9
    assert record.avg_tone == -2.5
    assert record.num_mentions == 12
    assert record.location.name == "Taiwan Strait"
    assert record.provenance.provider.value == "gdelt_events"
    assert record.provenance.provider_version == "gdelt_2_1_events"
    assert record.provenance.source_url == "https://example.test/world-event-source"


def test_world_event_cache_round_trips_with_canonical_source_refs(tmp_path):
    records = parse_gdelt_events_payload(
        _gdelt_payload(),
        request_url="https://api.gdeltproject.org/api/v2/events/events?query=x",
        fetched_at_utc="2026-06-03T00:00:00+00:00",
    )

    written = write_world_event_cache(tmp_path, records)
    loaded = read_world_event_cache(written[0])
    source_ref = source_ref_for_world_event(tmp_path, written[0], loaded[0])

    assert written[0].as_posix().endswith("artifacts/world_events/gdelt/events/20240601.jsonl")
    assert loaded[0].event_id == "123456789"
    assert source_ref == "artifacts/world_events/gdelt/events/20240601.jsonl:123456789"


def test_source_ref_rejects_non_canonical_cache_path(tmp_path):
    records = parse_gdelt_events_payload(
        _gdelt_payload(),
        request_url="https://api.gdeltproject.org/api/v2/events/events?query=x",
        fetched_at_utc="2026-06-03T00:00:00+00:00",
    )
    bad_path = tmp_path / "data" / "world_events.jsonl"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("", encoding="utf-8")

    try:
        source_ref_for_world_event(tmp_path, bad_path, records[0])
    except ValueError as exc:
        assert "canonical GDELT" in str(exc)
    else:
        raise AssertionError("expected non-canonical cache path to fail")


def test_source_ref_rejects_cache_date_mismatch(tmp_path):
    records = parse_gdelt_events_payload(
        _gdelt_payload(),
        request_url="https://api.gdeltproject.org/api/v2/events/events?query=x",
        fetched_at_utc="2026-06-03T00:00:00+00:00",
    )
    bad_path = tmp_path / "artifacts" / "world_events" / "gdelt" / "events" / "20240602.jsonl"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("", encoding="utf-8")

    try:
        source_ref_for_world_event(tmp_path, bad_path, records[0])
    except ValueError as exc:
        assert "date must match" in str(exc)
    else:
        raise AssertionError("expected cache date mismatch to fail")
