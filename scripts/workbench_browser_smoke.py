#!/usr/bin/env python3
"""Browser smoke test: every workbench tab, no Streamlit exceptions."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "research_cards" / "workbench_browser_smoke"
URL = "http://localhost:8501"

MAIN_TABS = [
    "Model Selector",
    "Personal Runs",
    "Backtest Results",
    "Latency Viability",
    "Signal Diagnostics",
    "Robustness",
    "Optimisation",
    "Report",
]

CATALOG_TABS = [
    "Alpha catalog",
    "Hybrid catalog",
    "Defensive catalog",
    "Stack builder",
]


def _exception_text(page) -> str:
    loc = page.locator('[data-testid="stException"]')
    if loc.count() == 0:
        return ""
    return loc.first.inner_text(timeout=2000)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; pip install playwright", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-testid="stApp"]', timeout=60000)
        page.wait_for_timeout(2000)

        exc = _exception_text(page)
        if exc:
            failures.append(f"initial load exception: {exc[:500]}")
            page.screenshot(path=str(OUT / "FAIL_initial.png"), full_page=True)

        page.screenshot(path=str(OUT / "00_initial.png"), full_page=True)

        for i, tab in enumerate(MAIN_TABS):
            page.get_by_role("tab", name=tab, exact=True).click()
            page.wait_for_timeout(1500)
            exc = _exception_text(page)
            slug = tab.lower().replace(" ", "_")
            page.screenshot(path=str(OUT / f"{i+1:02d}_{slug}.png"), full_page=True)
            if exc:
                failures.append(f"tab {tab!r}: {exc[:500]}")
                page.screenshot(path=str(OUT / f"FAIL_{slug}.png"), full_page=True)

        page.get_by_role("tab", name="Model Selector", exact=True).click()
        page.wait_for_timeout(1000)
        for cat in CATALOG_TABS:
            page.get_by_role("tab", name=cat, exact=True).click()
            page.wait_for_timeout(1200)
            exc = _exception_text(page)
            slug = cat.lower().replace(" ", "_")
            page.screenshot(path=str(OUT / f"cat_{slug}.png"), full_page=True)
            if exc:
                failures.append(f"catalog tab {cat!r}: {exc[:500]}")

        browser.close()

    if failures:
        print("BROWSER SMOKE FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(f"Screenshots: {OUT}", file=sys.stderr)
        return 1

    print(f"BROWSER SMOKE OK — {len(MAIN_TABS)} main tabs + {len(CATALOG_TABS)} catalog tabs")
    print(f"Screenshots: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
