"""
scraper.py
----------
Uses Playwright to load each Microsoft Bookings page and find the first
available appointment date. Results are written to a SQLite database.
"""

import asyncio
import sqlite3
import logging
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DB_PATH = "availability.db"
MAX_MONTHS_TO_CHECK = 6

# ── Add your services here ────────────────────────────────────────────────────
SERVICES = [
    {
        "id": "advice-shop-jim-walker",
        "label": "Advice Shop – Jim Walker Partnership Centre",
        "url": "https://outlook.office.com/book/AdviceShopJimWalkerPartnershipCentreCopy@westlothian.gov.uk/s/sftSX3pA2EK05NlKZHI73A2?ismsaljsauthenabled",
    },
    # Add more services here:
    # {
    #     "id": "service-id",
    #     "label": "Human-readable name",
    #     "url": "https://outlook.office.com/book/...",
    # },
]
# ─────────────────────────────────────────────────────────────────────────────


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS availability (
            id              TEXT PRIMARY KEY,
            label           TEXT NOT NULL,
            url             TEXT NOT NULL,
            first_available TEXT,
            status          TEXT NOT NULL DEFAULT 'unknown',
            last_checked    TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def save_result(service_id, label, url, first_available, status):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT INTO availability (id, label, url, first_available, status, last_checked)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            label           = excluded.label,
            url             = excluded.url,
            first_available = excluded.first_available,
            status          = excluded.status,
            last_checked    = excluded.last_checked
    """, (service_id, label, url, first_available, status, datetime.utcnow().isoformat()))
    con.commit()
    con.close()


async def find_first_available(page, url):
    """
    Returns (first_available_date_string, status).
    status is one of: 'ok', 'none_found', 'error'
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeoutError:
        log.warning("Page load timed out for %s", url)
        return None, "error"

    log.info("Page title: %s", await page.title())

    # Give the React app plenty of time to boot and render
    log.info("Waiting 10s for SPA to render...")
    await page.wait_for_timeout(10_000)

    # Log everything visible to diagnose the page state
    html = await page.content()
    log.info("HTML length after 10s wait: %d chars", len(html))

    # Dump all buttons so we can see what's on screen
    all_buttons = page.locator("button")
    btn_count = await all_buttons.count()
    log.info("Total buttons found: %d", btn_count)
    for i in range(min(btn_count, 40)):
        btn = all_buttons.nth(i)
        aria = await btn.get_attribute("aria-label")
        cls = await btn.get_attribute("class")
        txt = (await btn.inner_text()).strip()
        log.info("  btn[%d] aria-label=%r class=%r text=%r", i, aria, cls, txt[:60] if txt else "")

    # Also log any elements with role=gridcell or role=button that might be date cells
    gridcells = page.locator('[role="gridcell"], [role="button"]')
    gc_count = await gridcells.count()
    log.info("Total gridcell/button role elements: %d", gc_count)
    for i in range(min(gc_count, 20)):
        el = gridcells.nth(i)
        aria = await el.get_attribute("aria-label")
        cls = await el.get_attribute("class")
        disabled = await el.get_attribute("aria-disabled")
        log.info("  gridcell[%d] aria-label=%r class=%r aria-disabled=%r", i, aria, cls, disabled)

    # Log a chunk of the HTML to see the structure
    log.info("HTML SNAPSHOT (chars 3000-6000):\n%s", html[3000:6000])

    return None, "error"


async def run_scraper():
    log.info("Starting scrape run for %d services", len(SERVICES))
    init_db()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )

        for service in SERVICES:
            log.info("Checking: %s", service["label"])
            page = await context.new_page()
            try:
                first_available, status = await find_first_available(page, service["url"])
                save_result(service["id"], service["label"], service["url"], first_available, status)
                log.info("Result for %s: %s (%s)", service["id"], first_available, status)
            except Exception as e:
                log.error("Unexpected error for %s: %s", service["id"], e)
                save_result(service["id"], service["label"], service["url"], None, "error")
            finally:
                await page.close()

        await browser.close()

    log.info("Scrape run complete")


if __name__ == "__main__":
    asyncio.run(run_scraper())
