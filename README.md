# TradingView Daily Chart → Telegram (Render free + cron-job.org)

Free Render web service that stays awake via **cron-job.org** and
posts a TradingView chart screenshot + your text to Telegram every
day at **5:49 PM IST**.

This is the Render free-tier version. The cleaner free option is
still GitHub Actions — use this only if you specifically want
Render.

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
                              Playwright screenshot
                              TradingView chart
                                      │
                                      ▼
                              POST PNG + caption
                              to Telegram Bot API
```

Two jobs, two layers:

1. **Keep-awake** — cron-job.org pings `/health` every 10 min so
   Render never sleeps (free web services sleep after 15 min idle).
2. **Daily capture** — APScheduler inside the service fires once at
   5:49 PM IST and runs the heavy work in a background task. The
   HTTP handler returns immediately, so we never trip
   cron-job.org's 30-second HTTP timeout.

---

## 1. Telegram

1. Telegram → **@BotFather** → `/newbot` → copy the token.
2. Get your `chat_id`:
   - Send any message to the bot (or the channel).
   - Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find `"chat":{"id": 123456789}`.

---

## 2. TradingView cookies

1. Log into TradingView in your browser (free account works).
2. DevTools (F12) → **Application / Storage** → Cookies →
   `https://www.tradingview.com`.
3. Copy the values of `sessionid` and `sessionid_sign`.
4. These expire in ~30 days. When the chart stops showing, re-copy
   and update the env var on Render.

---

## 3. Render — create the free web service

1. Push this repo to GitHub (public or private).
2. Render Dashboard → **New → Blueprint** → connect the repo.
   - Or: **New → Web Service** → connect the repo manually.
3. Service type: **Web Service**.
4. Runtime: **Python 3**.
5. Build command: `pip install -r requirements.txt && python -m playwright install chromium`
6. Start command: `uvicorn chart_service:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 120`
7. Plan: **Free**.
8. Environment variables (Render Dashboard → Settings → Environment):

| Key | Value |
|---|---|
| `TRADINGVIEW_SESSION_ID` | `sessionid` cookie |
| `TRADINGVIEW_SESSION_ID_SIGN` | `sessionid_sign` cookie |
| `TELEGRAM_BOT_TOKEN` | token from BotFather |
| `TELEGRAM_CHAT_ID` | chat id |
| `SYMBOL` | `MCX:XAUUSD` |
| `INTERVAL` | `15` |
| `THEME` | `dark` |
| `CAPTION` | your HTML caption |
| `TZ` | `Asia/Kolkata` (optional) |

You can also set `SYMBOL`, `INTERVAL`, `THEME`, `CAPTION` to
anything you like — see the table below.

---

## 4. cron-job.org — keep-awake pings

1. Sign up at https://cron-job.org (free).
2. **New cron job** → URL:
   ```
   https://tradingview-daily-chart.onrender.com/health
   ```
   (use your actual Render URL).
3. Schedule: **every 10 minutes** (or any interval < 15 min).
   - cron-job.org minimum is 1 minute; 10 min is plenty.
4. Method: `GET`.
5. Timeout: leave default. The `/health` endpoint returns in < 1 s.
6. Save.

Now your Render service stays awake because it receives inbound
traffic every 10 minutes.

---

## 5. Test the capture manually

1. Render Dashboard → your service → **Logs** — watch for
   `Scheduler started`.
2. The scheduler fires at 5:49 PM IST. To test earlier, temporarily
   lower the hour in `chart_service.py` line:
   ```python
   trigger = CronTrigger(minute=49, hour=17, timezone="Asia/Kolkata")
   ```
   change `hour=17` to the current hour + 1, redeploy, and watch the
   logs. Revert after.
3. Or: use Render's manual trigger (if available on free tier) — but
   the scheduler is in-process, so there's no one-click "run now"
   button the way GitHub Actions has `workflow_dispatch`.

---

## Customising the chart

| Env var | Example | Description |
|---|---|---|
| `SYMBOL` | `MCX:XAUUSD` | TradingView symbol |
| `INTERVAL` | `15` | `1`,`5`,`15`,`30`,`60`,`240`,`D`,`W`,`M` |
| `THEME` | `dark` | `dark` or `light` |
| `CAPTION` | `XAUUSD 15m · IST 5:49 PM` | HTML sent with the chart |

---

## Cron math (why 5:49 PM IST)

- IST = UTC + 5:30.
- 17:49 IST = 12:19 UTC.
- APScheduler runs with `timezone="Asia/Kolkata"`, so the cron
  `minute=49, hour=17` fires at **5:49 PM IST** on the machine's
  clock, regardless of UTC.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Service sleeps / chart not sent | Confirm cron-job.org job is active and pings every < 15 min. Check Render logs for "spinning down". |
| "Missing env var" in logs | One of `TRADINGVIEW_SESSION_ID`, `TRADINGVIEW_SESSION_ID_SIGN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` is unset on Render. |
| Chart shows login wall | Cookies expired (~30 days). Re-copy `sessionid` and `sessionid_sign` and update the env vars on Render. |
| Telegram not receiving | Verify `chat_id` and token. Send a test message to the bot first. |
| Build fails / Chromium missing | The build command installs Playwright + Chromium. If it times out, increase build time in Render settings. |

---

## Free tier limits you should know

- Render free web services sleep after **15 min** without inbound
  traffic. We work around it with cron-job.org.
- Render free web services have an **ephemeral filesystem** —
  fine for us because we send the chart to Telegram, not store it.
- Render may **restart** the service at any time. The in-process
  scheduler restarts on boot; as long as the service is awake at
  5:49 PM IST, the daily capture fires.
- cron-job.org is **free** but may have slight delays; your 5:49 PM
  run could land a few seconds late. Keep-awake pings also aren't
  perfectly punctual.
