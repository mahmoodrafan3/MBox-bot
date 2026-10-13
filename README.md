# TradingView Daily Chart -> Telegram (Render free + cron-job.org)

Free Render web service that stays awake via **cron-job.org** and
posts a TradingView chart screenshot + your text to Telegram every
day at **5:49 PM IST**.

Chart capture uses the **CHART-IMG free REST API**
(https://chart-img.com) instead of Playwright/Chromium, because the
free Render tier cannot install Chromium's ~330 MB of system
dependencies. CHART-IMG captures the real TradingView chart on their
side and returns the PNG to you via HTTP - no browser on your side.

---

## Architecture

```
cron-job.org  ──every 10 min──▶  GET /health
                                      │
                                      ▼  (returns {"status":"ok"} instantly)
                                  Render free web service
                                      │
                                      ▼  (in-process APScheduler)
                              5:49 PM IST daily
                                      │
                                      ▼
                              POST JSON to CHART-IMG API
                              (symbol, interval, theme,
                               width, height, timezone)
                                      │
                                      ▼
                              PNG returned by CHART-IMG
                                      │
                                      ▼
                              POST PNG + caption
                              to Telegram Bot API
```

Two jobs, two layers:

1. **Keep-awake** - cron-job.org pings `/health` every 10 min so
   Render never sleeps (free web services sleep after 15 min idle).
2. **Daily capture** - APScheduler inside the service fires once at
   5:49 PM IST, calls CHART-IMG to get the chart PNG, then sends it
   to Telegram. The HTTP handler returns immediately, so we never
   trip cron-job.org's 30-second HTTP timeout.

---

## 1. CHART-IMG API key (replaces TradingView cookies)

This is the only new thing vs the old Chromium approach.

1. Go to https://chart-img.com and sign in with your Google account
   (free).
2. Copy your **API key** from the dashboard.
3. Add it as a Render secret called `CHART_IMG_API_KEY`.

No TradingView cookies needed anymore. CHART-IMG authenticates with
its own API key.

---

## 2. Telegram

1. Telegram -> **@BotFather** -> `/newbot` -> copy the token.
2. Get your `chat_id`:
   - Send any message to the bot (or the channel).
   - Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find `"chat":{"id": 123456789}`.

---

## 3. Render - create the free web service

1. Push this repo to GitHub (public or private).
2. Render Dashboard -> **New -> Blueprint** -> connect the repo.
   - Or: **New -> Web Service** -> connect the repo manually.
3. Service type: **Web Service**.
4. Runtime: **Python 3**.
5. Build command: `pip install -r requirements.txt`
6. Start command:
   ```
   sh -c 'exec uvicorn chart_service:app --host 0.0.0.0 --port ${PORT:-10000} --timeout-graceful-shutdown 120'
   ```
7. Plan: **Free**.
8. Environment variables (Render Dashboard -> Settings -> Environment):

| Key | Value |
|---|---|
| `CHART_IMG_API_KEY` | API key from chart-img.com (secret) |
| `TELEGRAM_BOT_TOKEN` | token from BotFather (secret) |
| `TELEGRAM_CHAT_ID` | chat id (secret) |
| `SYMBOL` | `MCX:XAUUSD` |
| `INTERVAL` | `15` |
| `THEME` | `dark` |
| `CAPTION` | your HTML caption |
| `TZ` | `Asia/Kolkata` |

Note: `PYTHON_VERSION=3.11.10` is set in `render.yaml` so you don't
need to add it manually (but if Render defaults to 3.14 for your
service, add it as a variable too).


---

## 4. cron-job.org - keep-awake pings

1. Sign up at https://cron-job.org (free).
2. **New cron job** -> URL:
   ```
   https://<your-service>.onrender.com/health
   ```
   (use your actual Render URL).
3. Schedule: **every 10 minutes** (any interval < 15 min).
4. Method: `GET`.
5. Save.

---

## 5. Customising the chart

| Env var | Example | Description |
|---|---|---|
| `SYMBOL` | `MCX:XAUUSD` | TradingView symbol (e.g. `NASDAQ:AAPL`, `BINANCE:BTCUSDT`, `FX:EURUSD`) |
| `INTERVAL` | `15` | `1`,`5`,`15`,`30`,`60`,`240`,`D`,`W`,`M` |
| `THEME` | `dark` | `dark` or `light` |
| `CAPTION` | `XAUUSD 15m · IST 5:49 PM` | HTML sent with the chart |

CHART-IMG supports many more options (indicators, drawings, range,
session, timezone, etc.). To add those, edit the `_chart_img_payload`
function in `chart_service.py` and re-deploy.

---

## Cron math (why 5:49 PM IST)

- IST = UTC + 5:30.
- 17:49 IST = 12:19 UTC.
- APScheduler runs with `timezone="Asia/Kolkata"`, so the cron
  `minute=49, hour=17` fires at **5:49 PM IST** regardless of UTC.

---

## Why CHART-IMG instead of Playwright/Chromium

| | Playwright + Chromium | CHART-IMG API |
|---|---|---|
| Gets real TradingView chart? | yes (via browser) | yes (via their API) |
| Chromium on Render free tier? | ~330 MB, fails to build | none |
| Python deps | playwright, greenlet, ... | httpx only |
| Build on Render free tier | fails | succeeds |
| Auth | TradingView session cookies (expire ~30 days) | chart-img.com API key (stable) |
| Speed | 15-60s (browser startup + render) | 2-10s (HTTP call) |

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Service starts but no chart sent | CHART_IMG_API_KEY not set or invalid. Check Render logs for "CHART_IMG_API_KEY missing". |
| "CHART-IMG returned 401/403" | API key wrong or expired. Re-copy from chart-img.com and update the Render secret. |
| "CHART-IMG returned 429" | Rate limited (free/basic plan). Wait and retry tomorrow; daily is well within limits. |
| Service sleeps / chart not sent | Confirm cron-job.org job is active and pings every < 15 min. Check Render logs for "spinning down". |
| Telegram not receiving | Verify `chat_id` and token. Send a test message to the bot first. |
| Chart shows wrong symbol/interval | Check `SYMBOL` and `INTERVAL` env vars on Render. |

---

## Free tier limits you should know

- Render free web services sleep after **15 min** without inbound
  traffic. We work around it with cron-job.org.
- Render free web services have an **ephemeral filesystem** - fine
  because we send the chart to Telegram, not store it.
- Render may **restart** the service at any time. The in-process
  scheduler restarts on boot; as long as the service is awake at
  5:49 PM IST, the daily capture fires.
- CHART-IMG free/basic plan has rate, resolution, and storage limits.
  A single daily request is well within them; if you increase
  frequency, check https://doc.chart-img.com/#request-limit.

---

## If you still want the Chromium/Playwright version

It's still in this repo's history and in `capture_and_send.py` (the
standalone script). The Render web-service path now uses CHART-IMG
because the free Render tier cannot install Chromium. If you have a
Render paid plan or a different host that can install Chromium, you
can switch `chart_service.py` back to the Playwright capture function
and restore `playwright` to `requirements.txt`.
