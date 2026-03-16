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
from datetime import datetime, timezone, date
from math import floor

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from scraper import run_scraper, init_db, DB_PATH, SERVICES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SCRAPE_INTERVAL_SECONDS = 10 * 60  # 10 minutes — also prevents Render free tier spin-down

# Date formats Microsoft Bookings uses in aria-labels
DATE_FORMATS = [
    "%A, %B %d, %Y",   # Wednesday, April 22, 2026
    "%A, %d %B %Y",    # Wednesday, 22 April 2026
]


def parse_first_available(date_str: str | None) -> date | None:
    """Try to parse the first_available string into a date object."""
    if not date_str:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def weeks_and_days(first_available: date) -> str:
    """Return a human-readable wait time from today to first_available."""
    today = datetime.now(timezone.utc).date()
    delta = (first_available - today).days
    if delta < 0:
        return "Date has passed"
    if delta == 0:
        return "Today"
    if delta < 7:
        return f"{delta} day{'s' if delta != 1 else ''}"
    weeks = delta // 7
    days = delta % 7
    parts = [f"{weeks} week{'s' if weeks != 1 else ''}"]
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    return " and ".join(parts)


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
    asyncio.create_task(scrape_loop())
    yield


app = FastAPI(
    title="Appointment Availability API",
    description="Returns the first available appointment date for each booking service.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    rows = get_all_rows()

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


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    rows = get_all_rows()

    if not rows:
        services = [
            {"label": s["label"], "first_available": None, "wait": None, "status": "pending", "last_checked": None}
            for s in SERVICES
        ]
        last_updated_str = "—"
    else:
        last_checked_times = [r["last_checked"] for r in rows if r["last_checked"]]
        if last_checked_times:
            last_updated_dt = datetime.fromisoformat(max(last_checked_times))
            last_updated_str = last_updated_dt.strftime("%-d %B %Y at %H:%M UTC")
        else:
            last_updated_str = "—"

        services = []
        for r in rows:
            parsed = parse_first_available(r["first_available"])
            wait = weeks_and_days(parsed) if parsed else None
            services.append({
                "label": r["label"],
                "first_available": r["first_available"] or "—",
                "wait": wait or "—",
                "status": r["status"],
                "last_checked": r["last_checked"],
            })

    # Build table rows
    table_rows = ""
    for s in services:
        if s["status"] == "ok":
            badge = '<span class="badge badge-ok">Available</span>'
        elif s["status"] == "none_found":
            badge = '<span class="badge badge-warn">None found</span>'
        elif s["status"] == "pending":
            badge = '<span class="badge badge-pending">Checking…</span>'
        else:
            badge = '<span class="badge badge-error">Error</span>'

        table_rows += f"""
        <tr>
            <td class="service-name">{s['label']}</td>
            <td>{s['first_available']}</td>
            <td>{s['wait']}</td>
            <td>{badge}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="600">
  <title>Appointment Availability</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f4f6f9;
      color: #1a1a2e;
      min-height: 100vh;
      padding: 2rem;
    }}

    header {{
      margin-bottom: 2rem;
    }}

    h1 {{
      font-size: 1.6rem;
      font-weight: 600;
      color: #1a1a2e;
    }}

    .subtitle {{
      font-size: 0.85rem;
      color: #6b7280;
      margin-top: 0.3rem;
    }}

    .card {{
      background: #ffffff;
      border-radius: 12px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      overflow: hidden;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
    }}

    thead {{
      background: #f9fafb;
      border-bottom: 1px solid #e5e7eb;
    }}

    th {{
      padding: 0.85rem 1.25rem;
      text-align: left;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #6b7280;
    }}

    td {{
      padding: 1rem 1.25rem;
      font-size: 0.9rem;
      border-bottom: 1px solid #f3f4f6;
      vertical-align: middle;
    }}

    tr:last-child td {{
      border-bottom: none;
    }}

    tr:hover td {{
      background: #fafafa;
    }}

    .service-name {{
      font-weight: 500;
      color: #111827;
    }}

    .badge {{
      display: inline-block;
      padding: 0.25rem 0.65rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
    }}

    .badge-ok      {{ background: #dcfce7; color: #166534; }}
    .badge-warn    {{ background: #fef9c3; color: #854d0e; }}
    .badge-error   {{ background: #fee2e2; color: #991b1b; }}
    .badge-pending {{ background: #e0f2fe; color: #075985; }}

    footer {{
      margin-top: 1.25rem;
      font-size: 0.78rem;
      color: #9ca3af;
      text-align: right;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Appointment Availability</h1>
    <p class="subtitle">Last updated: {last_updated_str} &nbsp;·&nbsp; Refreshes every 10 minutes</p>
  </header>

  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Service</th>
          <th>First Available</th>
          <th>Wait Time</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>

  <footer>Data sourced from Microsoft Bookings · Auto-refresh enabled</footer>
</body>
</html>"""

    return HTMLResponse(content=html)
