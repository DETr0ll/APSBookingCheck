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
        await page.goto(url, wait_until="networkidle", timeout=60_000)
    except PlaywrightTimeoutError:
        log.warning("Page load timed out for %s", url)
        return None, "error"

    # Log page title to confirm something loaded
    title = await page.title()
    log.info("Page title: %s", title)

    # Wait for the calendar to render
    try:
        await page.wait_for_selector(
            '[data-testid="date-picker"], .ms-DatePicker, [role="grid"], button[class*="day"]',
            timeout=30_000
        )
    except PlaywrightTimeoutError:
        log.warning("Calendar did not appear in time for %s", url)
        # Dump page HTML so we can see what actually loaded
        html = await page.content()
        log.warning("PAGE HTML SNAPSHOT (first 3000 chars):\n%s", html[:3000])
        return None, "error"

    await page.wait_for_timeout(2_000)

    for month_num in range(MAX_MONTHS_TO_CHECK):
        # Log all buttons on first month pass to identify correct selectors
        if month_num == 0:
            all_buttons = page.locator("button")
            btn_count = await all_buttons.count()
            log.info("Total buttons found on page: %d", btn_count)
            for i in range(min(btn_count, 30)):
                btn = all_buttons.nth(i)
                label = await btn.get_attribute("aria-label")
                cls = await btn.get_attribute("class")
                text = await btn.inner_text()
                log.info("Button %d: aria-label=%r class=%r text=%r", i, label, cls, text[:40] if text else "")

        available = page.locator(
            'button[class*="day"]:not([disabled]):not([aria-disabled="true"]):not([class*="disabled"]):not([class*="outside"])'
        )

        count = await available.count()
        log.info("Month %d: found %d potentially available day buttons", month_num + 1, count)

        for i in range(count):
            btn = available.nth(i)
            label = await btn.get_attribute("aria-label")
            if label and len(label) > 5:
                log.info("First available: %s", label)
                return label, "ok"

        # Move to next month
        next_btn = page.locator(
            'button[aria-label*="next"], button[aria-label*="Next"], '
            'button[class*="nextMonth"], button[class*="next-month"], '
            'button[title*="next"], button[title*="Next"]'
        ).first

        if await next_btn.count() == 0:
            log.warning("Could not find 'next month' button")
            return None, "error"

        await next_btn.click()
        await page.wait_for_timeout(1_500)

    return None, "none_found"


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
