#!/usr/bin/env python3
"""Generate event_universe.yaml and seed release_calendars/*.csv (sourced stubs)."""
from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import data_system_root, setup_repo_paths

setup_repo_paths()

OUT_YAML = _REPO / "packages" / "economic_event_universe" / "config" / "event_universe.yaml"
CAL_DIR = data_system_root() / "config" / "release_calendars"

DEFAULT_OFFSETS = [-10, -5, -1, 0, 1, 2, 5, 10]
HFT_RELEASE_WINDOW = {"start_offset_seconds": -60, "end_offset_seconds": 10}
SYM = "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0"
TIGHT = HFT_RELEASE_WINDOW
FOMC_WIN = HFT_RELEASE_WINDOW
CLAIMS_WIN = HFT_RELEASE_WINDOW
MAIN_PROP = HFT_RELEASE_WINDOW


def _ev(
    agency: str,
    freq: str,
    anchor: str,
    tz: str,
    url: str,
    label: str,
    *,
    window: str = "TIGHT",
    dl_win=None,
    holiday: str = "none",
    symbols: str = SYM,
    status: str = "CATALOG",
    schedule: str = "calendar_csv",
    main_context_label: str | None = None,
    context_priority: int = 50,
    regime_class: str = "none",
    regime_boost: bool = False,
) -> dict:
    out = {
        "status": status,
        "agency": agency,
        "frequency": freq,
        "anchor_time": anchor,
        "timezone": tz,
        "official_source_url": url,
        "event_context_label": label,
        "window_name": window,
        "download_window": dl_win or TIGHT,
        "holiday_rule": holiday,
        "symbol_universe": symbols,
        "schedule": schedule,
        "context_priority": context_priority,
        "regime_class": regime_class,
        "regime_boost": regime_boost or regime_class in ("event_shock", "prop_flatten"),
    }
    if main_context_label:
        out["main_context_label"] = main_context_label
    return out


def build_events() -> dict:
    bls = "https://www.bls.gov"
    bea = "https://www.bea.gov"
    fed = "https://www.federalreserve.gov"
    census = "https://www.census.gov"
    return {
        "CPI": _ev("BLS", "monthly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/cpi.htm", "CPI_TIGHT", status="RESEARCH_READY", regime_class="event_shock", regime_boost=True),
        "CORE_CPI": _ev("BLS", "monthly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/cpi.htm", "CORE_CPI_TIGHT"),
        "PPI": _ev("BLS", "monthly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/ppi.htm", "PPI_TIGHT"),
        "CORE_PPI": _ev("BLS", "monthly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/ppi.htm", "CORE_PPI_TIGHT"),
        "NFP": _ev("BLS", "monthly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/empsit.htm", "NFP_TIGHT", status="RESEARCH_READY", regime_class="event_shock", regime_boost=True),
        "UNEMPLOYMENT_CLAIMS": _ev(
            "DOL", "weekly", "08:30:00", "America/New_York",
            "https://www.dol.gov/ui/data.pdf", "CLAIMS_TIGHT",
            dl_win=CLAIMS_WIN, holiday="claims_thursday_to_wednesday",
        ),
        "JOLTS": _ev("BLS", "monthly", "10:00:00", "America/New_York", f"{bls}/schedule/news_release/jolts.htm", "JOLTS_TIGHT"),
        "ADP_EMPLOYMENT": _ev("ADP", "monthly", "08:15:00", "America/New_York", "https://adpemploymentreport.com/", "ADP_TIGHT"),
        "PRODUCTIVITY": _ev("BLS", "quarterly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/prod2.htm", "PRODUCTIVITY_TIGHT"),
        "PCE": _ev("BEA", "monthly", "08:30:00", "America/New_York", f"{bea}/news/schedule", "PCE_TIGHT"),
        "CORE_PCE": _ev("BEA", "monthly", "08:30:00", "America/New_York", f"{bea}/news/schedule", "CORE_PCE_TIGHT"),
        "GDP_ADVANCE": _ev("BEA", "quarterly", "08:30:00", "America/New_York", f"{bea}/news/schedule", "GDP_TIGHT"),
        "GDP_SECOND": _ev("BEA", "quarterly", "08:30:00", "America/New_York", f"{bea}/news/schedule", "GDP_TIGHT"),
        "GDP_FINAL": _ev("BEA", "quarterly", "08:30:00", "America/New_York", f"{bea}/news/schedule", "GDP_TIGHT"),
        "ECI": _ev("BLS", "quarterly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/eci.htm", "ECI_TIGHT"),
        "IMPORT_PRICES": _ev("BLS", "monthly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/ximpim.htm", "IMPORT_PRICES_TIGHT"),
        "EXPORT_PRICES": _ev("BLS", "monthly", "08:30:00", "America/New_York", f"{bls}/schedule/news_release/ximpim.htm", "EXPORT_PRICES_TIGHT"),
        "ISM_MANUFACTURING": _ev("ISM", "monthly", "10:00:00", "America/New_York", "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/", "ISM_MFG_TIGHT"),
        "ISM_SERVICES": _ev("ISM", "monthly", "10:00:00", "America/New_York", "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/", "ISM_SVC_TIGHT"),
        "DURABLE_GOODS_ADVANCE": _ev("Census", "monthly", "08:30:00", "America/New_York", f"{census}/economic-indicators/", "DURABLE_GOODS_TIGHT"),
        "DURABLE_GOODS_FULL": _ev("Census", "monthly", "10:00:00", "America/New_York", f"{census}/economic-indicators/", "DURABLE_GOODS_FULL_TIGHT"),
        "RETAIL_SALES": _ev("Census", "monthly", "08:30:00", "America/New_York", f"{census}/retail/marts.html", "RETAIL_SALES_TIGHT"),
        "FACTORY_ORDERS": _ev("Census", "monthly", "10:00:00", "America/New_York", f"{census}/manufacturing/m3/", "FACTORY_ORDERS_TIGHT"),
        "INDUSTRIAL_PRODUCTION": _ev("Fed", "monthly", "09:15:00", "America/New_York", f"{fed}/releases/g17/", "INDPRO_TIGHT"),
        "HOUSING_STARTS": _ev("Census", "monthly", "08:30:00", "America/New_York", f"{census}/construction/nrc/", "HOUSING_STARTS_TIGHT"),
        "BUILDING_PERMITS": _ev("Census", "monthly", "08:30:00", "America/New_York", f"{census}/construction/bps/", "BUILDING_PERMITS_TIGHT"),
        "NEW_HOME_SALES": _ev("Census", "monthly", "10:00:00", "America/New_York", f"{census}/construction/nrs/", "NEW_HOME_SALES_TIGHT"),
        "EXISTING_HOME_SALES": _ev("NAR", "monthly", "10:00:00", "America/New_York", "https://www.nar.realtor/research-and-statistics", "EXISTING_HOME_SALES_TIGHT"),
        "CONSTRUCTION_SPENDING": _ev("Census", "monthly", "10:00:00", "America/New_York", f"{census}/construction/c30/", "CONSTRUCTION_SPENDING_TIGHT"),
        "TRADE_BALANCE": _ev("Census", "monthly", "08:30:00", "America/New_York", f"{census}/foreign-trade/Press-Release/current_press_release/", "TRADE_BALANCE_TIGHT"),
        "FOMC_STATEMENT": _ev("Fed", "scheduled_8x", "14:00:00", "America/New_York", f"{fed}/monetarypolicy/fomccalendars.htm", "FOMC_STATEMENT_TIGHT", dl_win=FOMC_WIN, context_priority=10, regime_class="event_shock", regime_boost=True),
        "FOMC_PRESS": _ev("Fed", "scheduled", "14:30:00", "America/New_York", f"{fed}/monetarypolicy/fomccalendars.htm", "FOMC_PRESS_TIGHT", dl_win=FOMC_WIN, context_priority=5, regime_class="event_shock", regime_boost=True),
        "FOMC_MINUTES": _ev("Fed", "scheduled", "14:00:00", "America/New_York", f"{fed}/monetarypolicy/fomccalendars.htm", "FOMC_MINUTES_TIGHT", dl_win=FOMC_WIN, context_priority=30),
        "FED_BEIGE_BOOK": _ev("Fed", "8x_year", "14:00:00", "America/New_York", f"{fed}/monetarypolicy/beigebook/", "FED_BEIGE_BOOK_TIGHT"),
        "FED_H41": _ev("Fed", "weekly", "16:30:00", "America/New_York", f"{fed}/releases/h41/", "FED_H41_TIGHT"),
        "FED_SPEAKER": _ev("Fed", "ad_hoc", "12:00:00", "America/New_York", f"{fed}/newsevents/speeches.htm", "FED_SPEAKER_TIGHT", status="OPTIONAL", regime_class="none"),
        "TREASURY_AUCTION": _ev("Treasury", "weekly", "13:00:00", "America/New_York", "https://www.treasurydirect.gov/auctions/", "TREASURY_AUCTION_TIGHT"),
        "TREASURY_REFUNDING": _ev("Treasury", "quarterly", "08:30:00", "America/New_York", "https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding", "TREASURY_REFUNDING_TIGHT"),
        "EIA_CRUDE": _ev("EIA", "weekly", "10:30:00", "America/New_York", "https://www.eia.gov/petroleum/supply/weekly/", "EIA_CRUDE_TIGHT"),
        "EIA_NATGAS": _ev("EIA", "weekly", "10:30:00", "America/New_York", "https://www.eia.gov/naturalgas/storage/", "EIA_NATGAS_TIGHT"),
        "BAKER_HUGHES_RIG": _ev("BakerHughes", "weekly", "13:00:00", "America/New_York", "https://rigcount.bakerhughes.com/", "RIG_COUNT_TIGHT"),
        "PROP_FLATTEN_TOPSTEP": _ev(
            "TOPSTEP", "scheduled", "15:10:00", "America/Chicago", "https://www.topstep.com/", "PROP_FLATTEN_TOPSTEP",
            window="MAIN", dl_win=MAIN_PROP, main_context_label="PROP_FLATTEN_TOPSTEP",
            status="RESEARCH_READY", regime_class="prop_flatten", regime_boost=True,
        ),
        "PROP_REOPEN": _ev("TOPSTEP", "scheduled", "17:00:00", "America/Chicago", "https://www.topstep.com/", "PROP_REOPEN", window="MAIN", dl_win=MAIN_PROP, schedule="rule_based", regime_class="session"),
        "CASH_EQUITY_OPEN": _ev("NYSE", "daily", "09:30:00", "America/New_York", "https://www.nyse.com/markets/hours-calendars", "CASH_EQUITY_OPEN", window="MAIN", schedule="rule_based", regime_class="event_shock", regime_boost=True),
        "FRIDAY_CLOSE": _ev("CME", "weekly", "16:00:00", "America/New_York", "https://www.cmegroup.com/trading-hours.html", "FRIDAY_CLOSE", window="MAIN", schedule="rule_based", regime_class="prop_flatten", regime_boost=True),
    }


def _thursdays(start: date, end: date) -> list[date]:
    d = start
    while d.weekday() != 3:
        d += timedelta(days=1)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def _monthly_2nd_week(start: date, end: date) -> list[date]:
    """Approx CPI-style mid-month releases for seeding."""
    out = []
    y = start.year
    while y <= end.year:
        for m in range(1, 13):
            d = date(y, m, min(13, 28))
            if start <= d <= end:
                out.append(d)
        y += 1
    return out


def seed_calendars(events: dict) -> None:
    """SEED scaffolds go to artifacts/; release_calendars/ keeps SOURCED files only."""
    from economic_event_universe.calendar_io import sourced_calendar_filenames

    CAL_DIR.mkdir(parents=True, exist_ok=True)
    PROPOSALS_DIR = _REPO / "artifacts" / "calendar_proposals" / "seed_scaffold"
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    preserve = sourced_calendar_filenames(CAL_DIR)
    for path in CAL_DIR.glob("*.csv"):
        if path.name not in preserve:
            path.unlink()
    start = date(2018, 1, 1)
    end = date(2026, 12, 31)
    hdr = ["release_date", "event_type", "source", "source_url", "timezone", "release_time", "row_status"]

    def _write_seed(fname: str, rows: list[list]) -> None:
        path = PROPOSALS_DIR / fname
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(hdr)
            w.writerows(rows)

    cfg = events["UNEMPLOYMENT_CLAIMS"]
    _write_seed(
        "bls_claims.csv",
        [
            [d.isoformat(), "UNEMPLOYMENT_CLAIMS", cfg["agency"], cfg["official_source_url"], cfg["timezone"], cfg["anchor_time"], "SEED"]
            for d in _thursdays(start, end)
        ],
    )

    monthly_types = [
        "PCE", "CORE_PCE", "PPI", "CORE_PPI", "CORE_CPI", "JOLTS", "RETAIL_SALES",
        "GDP_ADVANCE", "GDP_SECOND", "GDP_FINAL", "ISM_MANUFACTURING", "ISM_SERVICES",
        "DURABLE_GOODS_ADVANCE", "DURABLE_GOODS_FULL", "FACTORY_ORDERS", "HOUSING_STARTS",
        "BUILDING_PERMITS", "NEW_HOME_SALES", "EXISTING_HOME_SALES", "CONSTRUCTION_SPENDING",
        "TRADE_BALANCE", "INDUSTRIAL_PRODUCTION", "ECI", "IMPORT_PRICES", "EXPORT_PRICES", "PRODUCTIVITY",
    ]
    dates = _monthly_2nd_week(start, end)
    by_file: dict[str, list[list]] = {}
    for et in monthly_types:
        c = events[et]
        fname = {
            "BEA": "bea_releases.csv",
            "BLS": "bls_other.csv",
            "Census": "census_releases.csv",
            "ISM": "ism_pmi.csv",
            "Fed": "fed_releases.csv",
            "NAR": "nar_releases.csv",
        }.get(c["agency"], "other_releases.csv")
        for d in dates[::2]:
            by_file.setdefault(fname, []).append(
                [d.isoformat(), et, c["agency"], c["official_source_url"], c["timezone"], c["anchor_time"], "SEED"]
            )
    for fname, rows in by_file.items():
        _write_seed(fname, rows)

    cfg = events["FOMC_STATEMENT"]
    fomc_rows: list[list] = []
    for y in range(2018, 2027):
        for m in [1, 3, 5, 6, 7, 9, 11, 12]:
            d = date(y, m, 15 if m != 6 else 12)
            fomc_rows.append([d.isoformat(), "FOMC_STATEMENT", "Fed", cfg["official_source_url"], cfg["timezone"], "14:00:00", "SEED"])
            fomc_rows.append([d.isoformat(), "FOMC_PRESS", "Fed", events["FOMC_PRESS"]["official_source_url"], cfg["timezone"], "14:30:00", "SEED"])
            mins = d + timedelta(days=21)
            fomc_rows.append([mins.isoformat(), "FOMC_MINUTES", "Fed", events["FOMC_MINUTES"]["official_source_url"], cfg["timezone"], "14:00:00", "SEED"])
    _write_seed("fed_fomc.csv", fomc_rows)

    cfg = events["FED_BEIGE_BOOK"]
    _write_seed(
        "fed_beige_book.csv",
        [
            [date(y, m, 7).isoformat(), "FED_BEIGE_BOOK", "Fed", cfg["official_source_url"], cfg["timezone"], cfg["anchor_time"], "SEED"]
            for y in range(2018, 2027)
            for m in [1, 3, 4, 6, 7, 9, 10, 12]
        ],
    )

    weekly_rows: list[list] = []
    for et in ("EIA_CRUDE", "EIA_NATGAS", "FED_H41", "BAKER_HUGHES_RIG"):
        c = events[et]
        d = start
        while d.weekday() != 2:
            d += timedelta(days=1)
        while d <= end:
            weekly_rows.append([d.isoformat(), et, c["agency"], c["official_source_url"], c["timezone"], c["anchor_time"], "SEED"])
            d += timedelta(days=7)
    _write_seed("weekly_releases.csv", weekly_rows)

    treasury_rows: list[list] = []
    for et in ("TREASURY_AUCTION", "TREASURY_REFUNDING"):
        c = events[et]
        for d in dates[::3]:
            treasury_rows.append([d.isoformat(), et, c["agency"], c["official_source_url"], c["timezone"], c["anchor_time"], "SEED"])
    _write_seed("treasury_auctions.csv", treasury_rows)

    c = events["ADP_EMPLOYMENT"]
    _write_seed(
        "adp_employment.csv",
        [[d.isoformat(), "ADP_EMPLOYMENT", c["agency"], c["official_source_url"], c["timezone"], c["anchor_time"], "SEED"] for d in dates[::3]],
    )
    print(f"SEED scaffolds under {PROPOSALS_DIR} (not release_calendars/)")


def main() -> int:
    events = build_events()
    doc = {
        "authority": ["BLUEPRINT.md §8", "docs/vault/ECONOMIC_EVENT_UNIVERSE.md"],
        "defaults": {
            "snapshot_offsets_sec": DEFAULT_OFFSETS,
            "symbol_universe_default": SYM,
        },
        "events": events,
    }
    OUT_YAML.parent.mkdir(parents=True, exist_ok=True)
    OUT_YAML.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"Wrote {OUT_YAML} ({len(events)} event types)")

    # Only seed new calendar files; preserve bls_cpi/nfp if present
    seed_calendars(events)
    print(f"Seeded calendars under {CAL_DIR}")
    from tools.economic_event_universe.generate_event_context_labels import main as gen_labels

    gen_labels()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
