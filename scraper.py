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
        "id": "f2f-ADP",
        "label": "Bathgate - Adult Disability Payment",
        "url": "https://outlook.office.com/book/AdviceShopJimWalkerPartnershipCentreCopy@westlothian.gov.uk/s/sftSX3pA2EK05NlKZHI73A2?ismsaljsauthenabled",
    },
    {
        "id": "f2f-CDP",
        "label": "Bathgate - Child Disability Payment",
        "url": "https://outlook.office.com/book/AdviceShopJimWalkerPartnershipCentreCopy@westlothian.gov.uk/s/c5tycRhTaEaN9fYFtZu3Fg2?ismsaljsauthenabled",
    },
    {
        "id": "f2f-PADP",
        "label": "Bathgate - Pension Age Disability Payment",
        "url": "https://outlook.office.com/book/AdviceShopJimWalkerPartnershipCentreCopy@westlothian.gov.uk/s/qEIJOofjTUO6K4LzEVpAoQ2?ismsaljsauthenabled",
    },
    {
        "id": "f2f-WCA",
        "label": "Bathgate - Work Capability Assessment",
        "url": "https://outlook.office.com/book/AdviceShopJimWalkerPartnershipCentreCopy@westlothian.gov.uk/s/KIR6DbXbW0WBtlkIVEhvew2?ismsaljsauthenabled",
    },
        {
        "id": "phone-ADP",
        "label": "Telephone - Adult Disability Payment",
        "url": "https://outlook.office.com/book/AdviceShopTelephoneAppointments@westlothian.gov.uk/s/sftSX3pA2EK05NlKZHI73A2?ismsaljsauthenabled",
    },
    {
        "id": "phone-CDP",
        "label": "Telephone - Child Disability Payment",
        "url": "https://outlook.office.com/book/AdviceShopTelephoneAppointments@westlothian.gov.uk/s/c5tycRhTaEaN9fYFtZu3Fg2?ismsaljsauthenabled",
    },
    {
        "id": "phone-PADP",
        "label": "Telephone - Pension Age Disability Payment",
        "url": "https://outlook.office.com/book/AdviceShopTelephoneAppointments@westlothian.gov.uk/s/qEIJOofjTUO6K4LzEVpAoQ2?ismsaljsauthenabled",
    },
    {
        "id": "phone-WCA",
        "label": "Telephone - Work Capability Assessment",
        "url": "https://outlook.office.com/book/AdviceShopTelephoneAppointments@westlothian.gov.uk/s/KIR6DbXbW0WBtlkIVEhvew2?ismsaljsauthenabled",
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

    # Wait for the SPA to render the calendar
    await page.wait_for_timeout(10_000)

    for month_num in range(MAX_MONTHS_TO_CHECK):
        # Calendar day cells use role="button", available ones do NOT have aria-disabled="true"
        # and their aria-label does not contain "No available times"
        day_cells = page.locator('[role="button"][aria-label]')
        count = await day_cells.count()
        log.info("Month %d: checking %d role=button elements", month_num + 1, count)

        for i in range(count):
            cell = day_cells.nth(i)
            aria = await cell.get_attribute("aria-label")
            disabled = await cell.get_attribute("aria-disabled")

            if not aria:
                continue

            # Skip navigation buttons (Prev/Next month)
            if "month" in aria.lower():
                continue

            # Skip days with no availability
            if "no available times" in aria.lower():
                continue

            # Skip explicitly disabled cells
            if disabled == "true":
                continue

            clean = aria.split(".")[0].strip()
            log.info("First available: %s", clean)
            return clean, "ok"

        # No available day found this month — go to next month
        next_btn = page.locator('[role="button"][aria-label="Next month"]')
        if await next_btn.count() == 0:
            log.warning("Could not find Next month button")
            return None, "error"

        await next_btn.click()
        await page.wait_for_timeout(2_000)

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
