"""
Free Render web service that:
  1. Stays awake via cron-job.org hitting /health every 10 minutes.
  2. Runs a daily TradingView chart screenshot → Telegram job at 5:49 PM IST.

Design notes
------------
- The keep-awake ping must return fast (< 1 second) so we never trip
  cron-job.org's 30-second HTTP timeout.
- The heavy capture job runs in a background thread / scheduler so the
  HTTP request handler returns immediately.
- Render free web services have an ephemeral filesystem, so we do not
  persist the chart PNG — we send it straight to Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Thread
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from playwright.async_api import async_playwright
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

TZ = timezone(timedelta(hours=5, minutes=30))  # Asia/Kolkata (IST)
# Fall back to env if someone sets TZ explicitly.
if os.environ.get("TZ"):
    # APScheduler uses IANA names; IST is Asia/Kolkata.
    TZ = timezone(timedelta(hours=5, minutes=30))

CAPTUREsymbol = os.environ.get("SYMBOL", "MCX:XAUUSD")
CAPTURE_INTERVAL = os.environ.get("INTERVAL", "15")
CAPTURE_THEME = os.environ.get("THEME", "dark")
CAPTURE_CAPTION = os.environ.get(
    "CAPTION",
    f"📊 <b>Daily {CAPTUREsymbol}</b> Chart (<b>{CAPTURE_INTERVAL}</b> timeframe)",
)


def tradingview_url(symbol: str, interval: str, theme: str) -> str:
    return (
        f"https://www.tradingview.com/chart/"
        f"?symbol={symbol}&interval={interval}&theme={theme}&timezone=exchange"
    )


# ---------------------------------------------------------------------------
# Chart capture (ported from capture_and_send.py)
# ---------------------------------------------------------------------------

async def capture_chart(
    session_id: str,
    session_id_sign: str,
    symbol: str,
    interval: str,
    theme: str,
    width: int = 1400,
    height: int = 800,
) -> bytes:
    url = tradingview_url(symbol, interval, theme)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120 Safari/537.36"
            ),
        )
        await context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": session_id,
                    "domain": ".tradingview.com",
                    "path": "/",
                },
                {
                    "name": "sessionid_sign",
                    "value": session_id_sign,
                    "domain": ".tradingview.com",
                    "path": "/",
                },
            ]
        )
        page = await context.new_page()
        log.info("Loading %s", url)
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(6000)

        log.info("Capturing screenshot …")
        clip = {"x": 0, "y": 0, "width": width, "height": height}
        screenshot_bytes = await page.screenshot(full_page=False, clip=clip)
        await browser.close()
        return screenshot_bytes


async def send_telegram_photo(
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


async def run_daily_capture() -> None:
    """One-shot: screenshot TradingView → send to Telegram."""
    log.info("Starting daily chart capture …")
    try:
        session_id = os.environ["TRADINGVIEW_SESSION_ID"]
        session_id_sign = os.environ["TRADINGVIEW_SESSION_ID_SIGN"]
        tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
        chat_id = os.environ["TELEGRAM_CHAT_ID"]
    except KeyError as exc:
        log.error("Missing env var: %s", exc)
        return

    try:
        chart_bytes = await capture_chart(
            session_id=session_id,
            session_id_sign=session_id_sign,
            symbol=CAPTUREsymbol,
            interval=CAPTURE_INTERVAL,
            theme=CAPTURE_THEME,
        )
        log.info("Chart captured (%d bytes)", len(chart_bytes))
    except Exception as exc:  # noqa: BLE001 — log and move on
        log.error("Chart capture failed: %s", exc)
        return

    caption = os.environ.get("CAPTION", CAPTURE_CAPTION)
    sent = await send_telegram_photo(
        token=tg_token,
        chat_id=chat_id,
        photo_bytes=chart_bytes,
        caption=caption,
    )
    if not sent:
        log.error("❌ Failed to send Telegram message — will retry tomorrow")


# ---------------------------------------------------------------------------
# Scheduler — daily at 5:49 PM IST
# ---------------------------------------------------------------------------

scheduler: Optional[AsyncIOScheduler] = None


def _build_trigger() -> CronTrigger:
    # 5:49 PM IST every day.
    # IST = UTC+5:30 → 17:49 IST = 12:19 UTC.
    return CronTrigger(
        minute=49,
        hour=17,
        timezone=str(TZ),  # APScheduler accepts IANA "Asia/Kolkata" too,
        # but we use our fixed IST offset for clarity.
    )


def start_scheduler() -> None:
    global scheduler
    if scheduler is not None and scheduler.running:
        return

    # APScheduler's CronTrigger accepts a timezone string. We use the IANA
    # name so the trigger fires at the right local time regardless of the
    # machine's system clock.
    trigger = CronTrigger(minute=49, hour=17, timezone="Asia/Kolkata")

    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        run_daily_capture,
        trigger=trigger,
        id="daily_chart",
        name="Daily TradingView chart → Telegram",
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "Scheduler started — next daily capture at 5:49 PM IST "
        "(next fire: %s)",
        scheduler.get_next_job_times("daily_chart")[0] if scheduler.get_next_job_times("daily_chart") else "unknown",
    )


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    """Keep-awake ping for cron-job.org. Returns instantly."""
    return JSONResponse({"status": "ok", "time": datetime.now(TZ).isoformat()})


@app.get("/")
async def root() -> JSONResponse:
    """Friendly landing page (also counts as inbound traffic)."""
    return JSONResponse(
        {
            "service": "TradingView Daily Chart → Telegram",
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
