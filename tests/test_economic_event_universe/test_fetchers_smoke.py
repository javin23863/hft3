from economic_event_universe.fetchers import bls, bea, fed, census, ism


def test_bls_fixture_parse():
    html = "<tr><td>CPI release 2024-09-11</td></tr><tr><td>2024-10-10</td></tr>"
    rows = bls.parse_bls_html(html, "CPI")
    assert all(r["event_type"] == "CPI" for r in rows)
    assert any(r["release_date"] == "2024-09-11" for r in rows)
    assert bls.parse_bls_html(html, "NFP") == []


def test_fed_fixture_parse():
    html = '<a href="/monetarypolicy/files/monetary20240918a1.pdf">stmt</a>'
    rows = fed.parse_fomc_html(html)
    assert any(r["event_type"] == "FOMC_STATEMENT" for r in rows)
    assert any(r["release_date"] == "2024-09-18" for r in rows)


def test_ism_fixture_parse():
    rows = ism.parse_ism_html("Release 2024-03-01")
    assert len(rows) >= 2


def test_bea_census_smoke():
    assert bea.parse_bea_html("2024-01-26") 
    assert census.parse_census_html("2024-02-15")
