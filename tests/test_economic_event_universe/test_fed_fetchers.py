"""Tests for Fed/FRED calendar fetchers (fixtures only — no live network in CI)."""

from __future__ import annotations

FOMC_FIXTURE = """
<div class="row fomc-meeting">
  <strong>Statement:</strong>
  <a href="/monetarypolicy/files/monetary20240612a1.pdf">PDF</a>
  <strong>Press Conference</strong>
</div>
<div class="row fomc-meeting">
  <a href="/monetarypolicy/files/monetary20240731a1.pdf">PDF</a>
</div>
<a href="/monetarypolicy/files/minutes20240612.pdf">Minutes</a>
"""

BEIGE_FIXTURE = '<a href="/monetarypolicy/files/BeigeBook_20240306.pdf">Beige</a>'


def test_parse_fomc_html_statement_press_minutes():
    from economic_event_universe.fetchers.fed import parse_fomc_html

    rows = parse_fomc_html(FOMC_FIXTURE)
    types = {r["event_type"] for r in rows}
    assert "FOMC_STATEMENT" in types
    assert "FOMC_PRESS" in types
    assert "FOMC_MINUTES" in types
    stmt = [r for r in rows if r["event_type"] == "FOMC_STATEMENT" and r["release_date"] == "2024-06-12"]
    assert len(stmt) == 1
    assert stmt[0]["source_url"].startswith("https://www.federalreserve.gov/")


def test_parse_beige_html():
    from economic_event_universe.fetchers.fed_beige import parse_beige_html

    rows = parse_beige_html(
        '<a href="/monetarypolicy/files/BeigeBook_20240306.pdf">Beige</a>',
        source_url="https://www.federalreserve.gov/monetarypolicy/beigebook2024.htm",
    )
    assert len(rows) == 1
    assert rows[0]["event_type"] == "FED_BEIGE_BOOK"
    assert rows[0]["release_date"] == "2024-03-06"


def test_fred_release_dates_mock(monkeypatch):
    from economic_event_universe.fetchers import fred_client

    monkeypatch.setattr(fred_client, "fred_api_key", lambda: "test-key")

    def fake_get(path: str, **params):
        assert path == "/release/dates"
        assert params["release_id"] == 13
        return {"release_dates": [{"date": "2024-01-17"}, {"date": "2024-02-15"}]}

    monkeypatch.setattr(fred_client, "_get", fake_get)
    dates = fred_client.release_dates(13, start="2024-01-01", end="2024-12-31")
    assert dates == ["2024-01-17", "2024-02-15"]


def test_macro_env_loads_desktop_keys(tmp_path, monkeypatch):
    keys = tmp_path / "keys.env"
    keys.write_text("FRED_API_KEY=from-desk\n", encoding="utf-8")
    import economic_event_universe.fetchers.env as env

    monkeypatch.setenv("MACRO_KEYS_ENV", str(keys))
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    env._LOADED = False
    assert env.fred_api_key() == "from-desk"
