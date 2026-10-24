"""Real-browser screenshots of every tab, for scripts/smoke.py (#26). Playwright, headless Chromium.

    SMOKE_SCREENSHOTS=docs/screenshots python scripts/smoke.py

`capture()` runs after the smoke flows (the DB has sessions, approvals, a rejection and a revision) and returns
the problems it saw: a `stException` on a tab, a browser console error, or an interaction that didn't land.
File names match docs/UI.md. Locally, without a Playwright Chromium, set SMOKE_BROWSER_CHANNEL=msedge (or chrome).
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

DESKTOP, TABLET = {"width": 1366, "height": 768}, {"width": 820, "height": 1180}
TABS = {"Officer console": ("console", "Choose a customer"), "Branch scan": ("scan", "Branch scan"),
        "Approval queue": ("queue", "Approval queue"), "Branch dashboard": ("dashboard", "Audit log")}
EXCEPTION = '[data-testid="stException"]:visible'  # hidden tab panels stay in the DOM: only what's on screen


def idle(page: Any, started: bool = False) -> None:
    """Wait until Streamlit's script run has finished (after a click, first give the rerun up to 1 s to start)."""
    app = '[data-testid="stApp"]'
    if not started:
        with contextlib.suppress(Exception):  # a fast rerun can finish before we look
            page.wait_for_selector(f'{app}[data-test-script-state="running"]', timeout=1000)
    page.wait_for_selector(f'{app}[data-test-script-state="notRunning"]', timeout=60_000)


def visible(page: Any, text: str) -> Any:
    """`text` inside the tab panel on screen (st.tabs keeps every panel in the DOM)."""
    return page.locator('[role="tabpanel"]:visible').get_by_text(text, exact=True).first


def shot(page: Any, path: Path, full: bool = True) -> None:
    """Full page: Streamlit scrolls inside stMain, so grow the viewport to its height for the capture."""
    size = page.viewport_size
    if full:
        h = page.evaluate("document.querySelector('[data-testid=\"stMain\"]').scrollHeight")
        if h > size["height"]:
            page.set_viewport_size({"width": size["width"], "height": h})
            time.sleep(0.5)  # let the charts re-lay out at the new size
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


def capture(ui_url: str, out_dir: Path) -> list[str]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    files: list[str] = []
    console: list[str] = []

    def check_tab(page: Any, tab: str, where: str) -> None:
        for e in page.locator(EXCEPTION).all():
            problems.append(f"{tab} ({where}): exception on the page: {e.inner_text()[:300]}")
        for msg in console:
            problems.append(f"{tab} ({where}): browser console error: {msg[:300]}")
        console.clear()

    def open_tab(page: Any, tab: str, where: str) -> None:
        page.get_by_role("tab", name=tab).click()
        idle(page)
        heading = visible(page, TABS[tab][1])
        heading.or_(page.locator(EXCEPTION)).first.wait_for(timeout=30_000)  # an exception ends the wait early
        if not heading.is_visible():
            problems.append(f"{tab} ({where}): did not finish rendering ('{TABS[tab][1]}' never appeared)")
            return
        if tab == "Branch dashboard":
            page.wait_for_function("document.querySelectorAll('[role=tabpanel] .vega-embed canvas,"
                                   " [role=tabpanel] .vega-embed svg').length >= 3", timeout=30_000)

    def save(page: Any, name: str, full: bool = True) -> None:
        shot(page, out_dir / name, full)
        files.append(name)

    def new_page(browser: Any, viewport: dict) -> Any:
        page = browser.new_page(viewport=viewport)
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(f"uncaught: {e}"))
        page.goto(ui_url)
        idle(page, started=True)
        visible(page, TABS["Officer console"][1]).wait_for(timeout=60_000)
        return page

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=os.getenv("SMOKE_BROWSER_CHANNEL") or None)
        try:
            page = new_page(browser, TABLET)  # tablet first: the desktop pass ends by starting a session
            for tab, (slug, _) in TABS.items():
                open_tab(page, tab, "tablet")
                check_tab(page, tab, "tablet")
                save(page, f"{slug}-tablet-820x1180.png")
            page.close()

            page = new_page(browser, DESKTOP)
            for tab, (slug, _) in TABS.items():
                if tab == "Approval queue":  # as the checker, so Approve / Reject are live
                    page.get_by_role("combobox", name="Acting as").click()
                    page.get_by_role("option", name="Branch Manager").click()
                    idle(page)
                open_tab(page, tab, "1366")
                check_tab(page, tab, "1366")
                save(page, "console-picker-1366x768.png" if slug == "console" else f"{slug}-1366x768.png")
                if tab == "Approval queue" and visible(page, "View page").count():
                    visible(page, "View page").click()
                    page.get_by_text("page 1 of").first.wait_for(timeout=10_000)
                    save(page, "queue-view-page-1366x768.png", full=False)
                    page.keyboard.press("Escape")
            # Click the PMJJBY bar: the 2nd band (30 px each) of "Eligibility by product" (if the tab rendered)
            if visible(page, "Eligibility by product").is_visible():
                page.locator('[role="tabpanel"]:visible .vega-embed').nth(1).click(position={"x": 200, "y": 50})
                idle(page)
                if "Product PMJJBY" in page.locator('[role="tabpanel"]:visible').inner_text():
                    save(page, "dashboard-filtered-1366.png")
                else:
                    problems.append("Branch dashboard (filtered): clicking the PMJJBY bar did not filter the view")
                check_tab(page, "Branch dashboard", "filtered")

            # Last, because it starts a session: the console with Ramesh loaded via the picker
            page.get_by_role("combobox", name="Acting as").click()
            page.get_by_role("option", name="Banking Correspondent").click()
            idle(page)
            open_tab(page, "Officer console", "session")
            page.locator(".st-key-start_C001 button").click()
            idle(page)
            visible(page, "Products").wait_for(timeout=30_000)
            check_tab(page, "Officer console", "session")
            save(page, "console-1366x768.png")
            page.close()  # close the websocket cleanly before the browser goes
        except Exception as e:  # noqa: BLE001 - report, don't crash the smoke run
            problems.append(f"screenshot run stopped: {e!r}"[:500])
        finally:
            browser.close()

    checks = [f"- FAIL {p}" for p in problems] or ["- PASS no exception or console error on any tab"]
    lines = ["# UI screenshots", "", f"From `scripts/smoke.py` against `{ui_url}`.", "", "## Checks", "", *checks,
             "", "## Files", "", *[f"### {f}\n\n![{f}]({f})\n" for f in files]]
    (out_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")
    return problems
