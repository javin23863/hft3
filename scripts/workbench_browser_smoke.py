#!/usr/bin/env python3
"""Headless Playwright smoke: workflow tabs, primary controls, screenshots."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "research_cards" / "workbench_browser_smoke"
URL = "http://localhost:8501"

sys.path.insert(0, str(REPO))
from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

from workbench.ui.workflow_tabs import WORKFLOW_TABS  # noqa: E402

MODEL_BUTTONS = ["Set primary", "Run campaign", "Download missing NPZ"]
CATALOG_EXPANDER = "Advanced — audit grade & full model grid"
CAMPAIGN_SOURCE = "workbench_campaign"
PREFERRED_MODEL = "SPREAD_BLOWOUT_RECOMPRESSION"


def _exception_text(page) -> str:
    loc = page.locator('[data-testid="stException"]')
    if loc.count() == 0:
        return ""
    return loc.first.inner_text(timeout=2000)


def _click_tab(page, name: str) -> None:
    page.get_by_role("tab", name=name, exact=True).click()


def _wait_for_workbench_ready(page, *, timeout_ms: int = 90000) -> None:
    deadline_steps = max(1, timeout_ms // 1000)
    first_tab = WORKFLOW_TABS[0]
    for _ in range(deadline_steps):
        if page.locator('[data-testid="stException"]').count():
            return
        if page.get_by_role("tab", name=first_tab, exact=True).count():
            return
        page.wait_for_timeout(1000)
    raise RuntimeError(f"Workbench tabs did not render within {timeout_ms}ms")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _append_source(url: str, source: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}source={source}"


def _select_primary_model(page) -> None:
    selector = page.get_by_label("Primary model")
    if selector.count() == 0:
        raise RuntimeError("Primary model selector not found")
    selector.scroll_into_view_if_needed()
    selector.click()
    page.wait_for_timeout(500)
    preferred = page.get_by_role("option", name=re.compile(PREFERRED_MODEL))
    if preferred.count():
        preferred.first.click()
    else:
        options = page.get_by_role("option")
        if options.count() < 2:
            raise RuntimeError("Primary model selector has no model options")
        options.nth(1).click()
    page.get_by_role("button", name="Run campaign", exact=True).wait_for(timeout=30000)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the running HFT3 Workbench Streamlit UI matches the backend tab contract."
    )
    parser.add_argument("--url", default=URL)
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--source", default=CAMPAIGN_SOURCE)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    checks: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(_append_source(args.url, args.source), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-testid="stApp"]', timeout=60000)
        _wait_for_workbench_ready(page)

        exc = _exception_text(page)
        if exc:
            failures.append(f"initial load: {exc[:400]}")
        page.screenshot(path=str(out_dir / "00_initial.png"), full_page=True)

        for tab in WORKFLOW_TABS:
            if page.get_by_role("tab", name=tab, exact=True).count() == 0:
                failures.append(f"missing tab: {tab!r}")

        _click_tab(page, "Registry & Data")
        page.wait_for_timeout(2000)
        page.screenshot(path=str(out_dir / "01_registry_data.png"), full_page=True)

        try:
            _select_primary_model(page)
            checks.append("Primary model selected")
        except Exception as exc_obj:
            failures.append(f"primary model selection: {exc_obj}")

        for label in MODEL_BUTTONS:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count() == 0:
                failures.append(f"missing button: {label!r}")
                continue
            checks.append(f"button present: {label}")

        safe_primary = page.get_by_role("button", name="Set primary", exact=True)
        if safe_primary.count():
            safe_primary.first.click()
            checks.append("Set primary clicked")
            page.wait_for_timeout(2000)
            exc = _exception_text(page)
            page.screenshot(path=str(out_dir / "btn_set_primary.png"), full_page=True)
            if exc:
                failures.append(f"after 'Set primary': {exc[:400]}")

        for i, tab in enumerate(WORKFLOW_TABS):
            _click_tab(page, tab)
            page.wait_for_timeout(1200)
            exc = _exception_text(page)
            page.screenshot(path=str(out_dir / f"tab_{i+1:02d}_{_slug(tab)}.png"), full_page=True)
            if exc:
                failures.append(f"tab {tab!r}: {exc[:400]}")

        _click_tab(page, "Registry & Data")
        page.wait_for_timeout(800)
        expander = page.get_by_text(CATALOG_EXPANDER, exact=False)
        if expander.count():
            expander.first.click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out_dir / "expander_catalog.png"), full_page=True)
            if _exception_text(page):
                failures.append(f"catalog expander: {_exception_text(page)[:400]}")
        else:
            failures.append(f"missing expander: {CATALOG_EXPANDER!r}")

        browser.close()

    if failures:
        print("BROWSER SMOKE FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(f"Screenshots: {out_dir}", file=sys.stderr)
        return 1

    print(f"BROWSER SMOKE OK - {len(checks)} checks, {len(WORKFLOW_TABS)} tabs")
    print(f"Screenshots: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
