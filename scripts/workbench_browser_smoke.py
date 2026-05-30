#!/usr/bin/env python3
"""Headless Playwright smoke: workflow tabs, primary buttons, screenshots."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "research_cards" / "workbench_browser_smoke"
URL = "http://localhost:8501"

WORKFLOW_TABS = [
    "Model Selector",
    "Backtest Results",
    "Latency Viability",
    "Signal Diagnostics",
    "Robustness",
    "Optimisation",
    "Report",
    "Analyst",
    "Personal Runs",
]

MODEL_BUTTONS = ["Set primary", "Run campaign", "Download missing NPZ"]
CATALOG_EXPANDER = "Advanced — audit grade & full model grid"


def _exception_text(page) -> str:
    loc = page.locator('[data-testid="stException"]')
    if loc.count() == 0:
        return ""
    return loc.first.inner_text(timeout=2000)


def _click_tab(page, name: str) -> None:
    page.get_by_role("tab", name=name, exact=True).click()


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    clicks: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-testid="stApp"]', timeout=60000)
        page.wait_for_timeout(3000)

        exc = _exception_text(page)
        if exc:
            failures.append(f"initial load: {exc[:400]}")
        page.screenshot(path=str(OUT / "00_initial.png"), full_page=True)

        _click_tab(page, "Model Selector")
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "01_model_selector.png"), full_page=True)

        for label in MODEL_BUTTONS:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count() == 0:
                failures.append(f"missing button: {label!r}")
                continue
            btn.first.click()
            clicks.append(label)
            page.wait_for_timeout(2500)
            exc = _exception_text(page)
            slug = label.lower().replace(" ", "_")
            page.screenshot(path=str(OUT / f"btn_{slug}.png"), full_page=True)
            if exc:
                failures.append(f"after {label!r}: {exc[:400]}")

        for i, tab in enumerate(WORKFLOW_TABS):
            _click_tab(page, tab)
            page.wait_for_timeout(1200)
            exc = _exception_text(page)
            slug = tab.lower().replace(" ", "_")
            page.screenshot(path=str(OUT / f"tab_{i+1:02d}_{slug}.png"), full_page=True)
            if exc:
                failures.append(f"tab {tab!r}: {exc[:400]}")

        _click_tab(page, "Model Selector")
        page.wait_for_timeout(800)
        page.get_by_text(CATALOG_EXPANDER, exact=False).click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "expander_catalog.png"), full_page=True)
        if _exception_text(page):
            failures.append(f"catalog expander: {_exception_text(page)[:400]}")

        _click_tab(page, "Optimisation")
        promote = page.get_by_role("button", name="Promote Candidate", exact=True)
        if promote.count() and promote.first.is_enabled():
            promote.first.click()
            clicks.append("Promote Candidate")
        elif promote.count() == 0:
            failures.append("missing button: Promote Candidate")

        browser.close()

    if failures:
        print("BROWSER SMOKE FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(f"Screenshots: {OUT}", file=sys.stderr)
        return 1

    print(f"BROWSER SMOKE OK — {len(clicks)} clicks, {len(WORKFLOW_TABS)} tabs")
    print(f"Screenshots: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
