"""
api.py
------
FastAPI app that exposes availability data stored by scraper.py.
Also triggers the scraper on startup so there's data immediately.
"""

import sqlite3
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from scraper import run_scraper, init_db, DB_PATH, SERVICES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SCRAPE_INTERVAL_SECONDS = 10 * 60  # 10 minutes — also prevents Render free tier spin-down


async def scrape_loop():
    """Background task: scrape all services every SCRAPE_INTERVAL_SECONDS."""
    while True:
        try:
            await run_scraper()
        except Exception as e:
            log.error("Scrape loop error: %s", e)
        log.info("Next scrape in %d seconds", SCRAPE_INTERVAL_SECONDS)
        await asyncio.sleep(SCRAPE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Run first scrape immediately in the background, then loop
    asyncio.create_task(scrape_loop())
    yield


app = FastAPI(
    title="Appointment Availability API",
    description="Returns the first available appointment date for each booking service.",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow your website to call this API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict to your domain in production, e.g. ["https://yoursite.com"]
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_all_rows() -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM availability ORDER BY label").fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_row(service_id: str) -> dict | None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM availability WHERE id = ?", (service_id,)).fetchone()
    con.close()
    return dict(row) if row else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/availability")
def all_availability():
    """
    Returns availability for all services.

    Example response:
    {
      "last_updated": "2026-03-16T10:00:00",
      "services": [
        {
          "id": "advice-shop-jim-walker",
          "label": "Advice Shop – Jim Walker Partnership Centre",
          "first_available": "Tuesday, 18 March 2026",
          "status": "ok",
          "last_checked": "2026-03-16T09:58:00"
        }
      ]
    }
    """
    rows = get_all_rows()

    # If DB is empty (first run not complete yet), return pending status for all services
    if not rows:
        return {
            "last_updated": None,
            "services": [
                {
                    "id": s["id"],
                    "label": s["label"],
                    "first_available": None,
                    "status": "pending",
                    "last_checked": None,
                }
                for s in SERVICES
            ],
        }

    last_checked_times = [r["last_checked"] for r in rows if r["last_checked"]]
    last_updated = max(last_checked_times) if last_checked_times else None

    return {
        "last_updated": last_updated,
        "services": [
            {
                "id": r["id"],
                "label": r["label"],
                "first_available": r["first_available"],
                "status": r["status"],
                "last_checked": r["last_checked"],
            }
            for r in rows
        ],
    }


@app.get("/availability/{service_id}")
def single_availability(service_id: str):
    """
    Returns availability for a single service by ID.

    Example: GET /availability/advice-shop-jim-walker
    """
    row = get_row(service_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found.")
    return {
        "id": row["id"],
        "label": row["label"],
        "first_available": row["first_available"],
        "status": row["status"],
        "last_checked": row["last_checked"],
    }


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}
