"""
Render free web service that:
  1. Stays awake via cron-job.org hitting /health every 10 minutes.
  2. Runs a daily TradingView chart screenshot -> Telegram job at 5:49 PM IST.

Chart capture is done via the CHART-IMG free REST API
(https://chart-img.com) instead of Playwright/Chromium, because the
free Render tier cannot install Chromium's ~330 MB system dependencies.

Design notes
------------
- The keep-awake ping must return fast (< 1 second) so we never trip
  cron-job.org's 30-second HTTP timeout.
- The heavy capture job runs in a background scheduler so the HTTP
  request handler returns immediately.
- Render free web services have an ephemeral filesystem, so we do not
  persist the chart PNG - we send it straight to Telegram.
- CHART-IMG is a third-party free API. Sign in at chart-img.com with
  Google and copy your API key into the CHART_IMG_API_KEY secret.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("chart-service")

app = FastAPI(title="TradingView Daily Chart")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CAPTURE_SYMBOL = os.environ.get("SYMBOL", "MCX:XAUUSD")
CAPTURE_INTERVAL = os.environ.get("INTERVAL", "15")
CAPTURE_THEME = os.environ.get("THEME", "dark")
CAPTURE_CAPTION = os.environ.get(
    "CAPTION",
    f"📊 <b>Daily {CAPTURE_SYMBOL}</b> Chart (<b>{CAPTURE_INTERVAL}</b> timeframe)",
)
CHART_IMG_API_KEY = os.environ.get("CHART_IMG_API_KEY", "")

if not CHART_IMG_API_KEY:
    log.warning(
        "CHART_IMG_API_KEY is not set - chart capture will fail. "
        "Sign in at https://chart-img.com with Google and copy your API key "
        "into the CHART_IMG_API_KEY secret on Render."
    )

CHART_IMG_BASE = "https://api.chart-img.com/v2/tradingview/advanced-chart"


# ---------------------------------------------------------------------------
# CHART-IMG capture
# ---------------------------------------------------------------------------

def _chart_img_payload(
    symbol: str,
    interval: str,
    theme: str,
    width: int = 1400,
    height: int = 800,
    timezone: str = "Asia/Kolkata",
    session: str = "regular",
) -> dict:
    """Build the JSON body for the CHART-IMG v2 advanced-chart endpoint."""
    return {
        "symbol": symbol,
        "interval": interval,
        "theme": theme,
        "width": width,
        "height": height,
        "timezone": timezone,
        "session": session,
    }


async def fetch_chart_bytes(payload: dict, api_key: str) -> bytes:
    """POST to CHART-IMG and return the PNG bytes."""
    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(CHART_IMG_BASE, headers=headers, json=payload)
        if r.status_code != 200:
            log.error(
                "CHART-IMG returned %d: %s",
                r.status_code,
                r.text[:500],
            )
            raise RuntimeError(f"CHART-IMG error {r.status_code}: {r.text[:200]}")
        # CHART-IMG returns image/png on success.
        content_type = r.headers.get("content-type", "")
        if "image" not in content_type and not r.content.startswith(b"\x89PNG"):
            log.warning(
                "Unexpected CHART-IMG response content-type: %s (size %d)",
                content_type,
                len(r.content),
            )
        return r.content


async def run_daily_capture() -> None:
    """One-shot: fetch chart via CHART-IMG -> send to Telegram."""
    log.info("Starting daily chart capture …")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    api_key = os.environ.get("CHART_IMG_API_KEY", "")

    if not api_key:
        log.error("CHART_IMG_API_KEY missing - cannot fetch chart")
        return
    if not tg_token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        return

    payload = _chart_img_payload(
        symbol=CAPTURE_SYMBOL,
        interval=CAPTURE_INTERVAL,
        theme=CAPTURE_THEME,
    )

    try:
        chart_bytes = await fetch_chart_bytes(payload, api_key)
        log.info("Chart fetched (%d bytes) from CHART-IMG", len(chart_bytes))
    except Exception as exc:  # noqa: BLE001
        log.error("Chart fetch failed: %s", exc)
        return

    caption = os.environ.get("CAPTION", CAPTURE_CAPTION)
    sent = await _send_telegram_photo(tg_token, chat_id, chart_bytes, caption)
    if not sent:
        log.error("❌ Failed to send Telegram message — will retry tomorrow")


# ---------------------------------------------------------------------------
# Telegram (kept from previous version, untouched except indents)
# ---------------------------------------------------------------------------

async def _send_telegram_photo(
    token: str,
    chat_id: str,
    photo_bytes: bytes,
    caption: str,
) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            url,
            data={
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
            },
            files={"photo": ("chart.png", photo_bytes, "image/png")},
        )
        ok = r.status_code == 200 and r.json().get("ok") is True
        if not ok:
            log.error("Telegram API response: %s", r.text)
        else:
            log.info("Telegram message sent to %s", chat_id)
        return ok


# ---------------------------------------------------------------------------
# Scheduler - daily at 5:49 PM IST
# ---------------------------------------------------------------------------

scheduler: Optional[AsyncIOScheduler] = None


def start_scheduler() -> None:
    global scheduler
    if scheduler is not None and scheduler.running:
        return

    trigger = CronTrigger(minute=49, hour=17, timezone="Asia/Kolkata")

    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        run_daily_capture,
        trigger=trigger,
        id="daily_chart",
        name="Daily TradingView chart -> Telegram",
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "Scheduler started - next daily capture at 5:49 PM IST "
        "(next fire: %s)",
        scheduler.get_next_job_times("daily_chart")[0]
        if scheduler.get_next_job_times("daily_chart")
        else "unknown",
    )


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    """Keep-awake ping for cron-job.org. Returns instantly."""
    return JSONResponse({"status": "ok", "time": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()})


@app.get("/")
async def root() -> JSONResponse:
    """Friendly landing page (also counts as inbound traffic)."""
    return JSONResponse(
        {
            "service": "TradingView Daily Chart -> Telegram",
            "schedule": "5:49 PM IST daily",
            "next_run": (
                scheduler.get_next_job_times("daily_chart")[0].isoformat()
                if scheduler and scheduler.running
                else "scheduler not started"
            ),
        }
    )


@app.on_event("startup")
async def on_startup() -> None:
    start_scheduler()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Scheduler shut down")
