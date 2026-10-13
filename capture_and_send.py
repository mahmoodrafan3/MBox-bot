"""
Capture a TradingView chart screenshot and send it to a Telegram
channel/user along with a custom caption.

Secrets (GitHub repo → Settings → Secrets and variables → Actions):
  TRADINGVIEW_SESSION_ID
  TRADINGVIEW_SESSION_ID_SIGN
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

Variables (GitHub repo → Settings → Variables):
  SYMBOL   - e.g. MCX:XAUUSD
  INTERVAL - e.g. 15  (1,5,15,30,60,240,D,W,M)
  THEME    - dark | light
  CAPTION  - text sent with the chart
"""

import asyncio
import logging
import os
import re
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("daily-chart")


# ---------------------------------------------------------------------------
# TradingView chart screenshot
# ---------------------------------------------------------------------------

TRADINGVIEW_CHART_URL = "https://www.tradingview.com/chart/"


def tradingview_url(symbol: str, interval: str, theme: str) -> str:
    """Return a full TradingView chart URL for the given symbol."""
    # The widget URL loads the chart directly.
    return (
        f"{TRADINGVIEW_CHART_URL}"
        f"?symbol={symbol}"
        f"&interval={interval}"
        f"&theme={theme}"
        f"&timezone=exchange"
    )


async def capture_chart(
    session_id: str,
    session_id_sign: str,
    symbol: str,
    interval: str,
    theme: str,
    width: int = 1400,
    height: int = 800,
) -> bytes:
    """Use Playwright to load TradingView and screenshot the chart area."""
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

        # Inject TradingView session cookies so the chart loads
        # without a login wall.
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

        # TradingView renders the chart on a canvas inside #chart-container
        # Give it a few seconds to fully paint.
        await page.wait_for_timeout(6000)

        log.info("Capturing screenshot …")
        screenshot_bytes = await page.screenshot(
            full_page=False,
            clip={
                "x": 0,
                "y": 0,
                "width": width,
                "height": height,
            },
        )
        await browser.close()
        return screenshot_bytes


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

TELEMGRAM_API = "https://api.telegram.org/bot{token}/{method}"


async def send_telegram_photo(
    token: str,
    chat_id: str,
    photo_bytes: bytes,
    caption: str,
) -> bool:
    url = TELEMGRAM_API.format(token=token, method="sendPhoto")
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
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    session_id = os.environ["TRADINGVIEW_SESSION_ID"]
    session_id_sign = os.environ["TRADINGVIEW_SESSION_ID_SIGN"]
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    symbol = os.environ.get("SYMBOL", "MCX:XAUUSD")
    interval = os.environ.get("INTERVAL", "15")
    theme = os.environ.get("THEME", "dark")
    caption = os.environ.get(
        "CAPTION",
        f"📊 <b>Daily {symbol}</b> Chart (<b>{interval}</b> timeframe)",
    )

    # ------------------------------------------------------------------
    # 1. Screenshot
    # ------------------------------------------------------------------
    chart_bytes = await capture_chart(
        session_id=session_id,
        session_id_sign=session_id_sign,
        symbol=symbol,
        interval=interval,
        theme=theme,
    )
    out_path = Path("chart.png")
    out_path.write_bytes(chart_bytes)
    log.info("Saved chart (%d bytes) → %s", len(chart_bytes), out_path)

    # ------------------------------------------------------------------
    # 2. Telegram
    # ------------------------------------------------------------------
    sent = await send_telegram_photo(
        token=tg_token,
        chat_id=chat_id,
        photo_bytes=chart_bytes,
        caption=caption,
    )
    if not sent:
        raise SystemExit("❌ Failed to send Telegram message")


if __name__ == "__main__":
    asyncio.run(main())
