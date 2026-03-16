# Appointment Availability API

A lightweight API that scrapes Microsoft Bookings pages every 5 minutes and
exposes the first available appointment date for each service.

## Files

| File | Purpose |
|------|---------|
| `scraper.py` | Playwright scraper — finds first available date on each Bookings page |
| `api.py` | FastAPI app — serves cached results, runs scraper in background |
| `requirements.txt` | Python dependencies |

---

## Deploying to Render (free tier)

### 1. Create a GitHub repository

Put all three files (`scraper.py`, `api.py`, `requirements.txt`) in a new
GitHub repo. Render deploys directly from GitHub.

### 2. Create a Render account

Sign up at https://render.com (free, no credit card needed for the free tier).

### 3. Create a new Web Service

1. In Render dashboard → **New** → **Web Service**
2. Connect your GitHub repo
3. Fill in the settings:

| Setting | Value |
|---------|-------|
| **Name** | `availability-api` (or whatever you like) |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt && playwright install chromium && playwright install-deps` |
| **Start Command** | `uvicorn api:app --host 0.0.0.0 --port $PORT` |
| **Instance Type** | Free |

4. Click **Create Web Service**

Render will build and deploy. First deploy takes ~3-5 minutes.

### 4. Your API is live

Render gives you a URL like `https://availability-api.onrender.com`.

Test it:
```
https://availability-api.onrender.com/health
https://availability-api.onrender.com/availability
https://availability-api.onrender.com/availability/advice-shop-jim-walker
```

> **Note:** The 10-minute scrape interval keeps the service active, preventing
> Render's free tier from spinning down due to inactivity.

---

## Adding more services

Edit `scraper.py` and add entries to the `SERVICES` list:

```python
SERVICES = [
    {
        "id": "advice-shop-jim-walker",           # unique slug
        "label": "Advice Shop – Jim Walker Partnership Centre",
        "url": "https://outlook.office.com/book/...",
    },
    {
        "id": "my-other-service",
        "label": "My Other Service",
        "url": "https://outlook.office.com/book/...",
    },
]
```

Commit and push — Render redeploys automatically.

---

## Fetching from your Goss website

```javascript
// Fetch all services
fetch("https://availability-api.onrender.com/availability")
  .then(res => res.json())
  .then(data => {
    data.services.forEach(service => {
      console.log(service.label, "→", service.first_available ?? "No availability");
    });
  });

// Fetch a single service
fetch("https://availability-api.onrender.com/availability/advice-shop-jim-walker")
  .then(res => res.json())
  .then(data => {
    console.log("First available:", data.first_available);
  });
```

### Example API response

```json
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
```

### Status values

| Status | Meaning |
|--------|---------|
| `ok` | First available date found successfully |
| `none_found` | No availability in the next 6 months |
| `error` | Page failed to load or parse |
| `pending` | First scrape not yet complete (just started) |

---

## Adjusting the scrape interval

In `api.py`, change this line:

```python
SCRAPE_INTERVAL_SECONDS = 10 * 60  # 10 minutes
```
