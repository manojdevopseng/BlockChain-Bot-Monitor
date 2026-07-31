# BlockChain-Bot Monitor

Multichain crypto token monitor with a web dashboard. Watches Solana launches,
Ethereum / Robinhood Chain / BNB Chain contracts and a set of premium Telegram
groups, and turns what it finds into alerts.

- **Backend:** FastAPI + MongoDB (Motor) + WebSocket · async throughout
- **Frontend:** Next.js 14 + Tailwind + shadcn/ui + Recharts + Lucide
- **Realtime:** `/ws` push (heartbeat, service changes, new detections)
- **Auth:** JWT login, two roles

Running on EC2 behind Nginx under two systemd units — see [deploy/DEPLOY.md](deploy/DEPLOY.md).

## What it does

| Feature | Where it shows |
|---|---|
| Solana launch discovery (PumpPortal socket + Helius `logsSubscribe`) | Tokens, AI Narrative |
| SOL→ETH and SOL→Robinhood ticker matches | Detections → Cross-Chain Matches |
| Contract addresses posted in premium Telegram groups, verified on chain | Detections → Premium Callers |
| High-gas early buys on new ETH pairs | Detections → ETH Gas Fees |
| Grok judging each launch's X post against a narrative list | AI Narrative |
| Market-cap watch on a link's burst of launches → Telegram | AI Narrative → Telegram tab |
| Forwarding from four signal channels + every premium group | Forwarder |
| What each alert did at 15m / 1h / 6h / 24h | Analytics |

## Dashboard

Fourteen pages: Dashboard, Alerts, Tokens, Detections, AI Narrative, Forwarder,
Commands, Analytics, Chains, Logs, Settings, User Management, RPC Monitor,
System.

**Two roles.** `admin` gets everything. `user` sees the whole dashboard and can
change nothing — Forwarder, Commands, Settings, RPC Monitor and User Management
are closed to it, and any non-GET is refused server-side whatever page it came
from. Set `USER_USERNAME` / `USER_PASSWORD` to create it; leave them blank and
only the admin can log in.

## On/off toggles (Settings)

Twenty-five switches, all live — flipping one persists to the `services`
collection and tells the supervisor to start or stop the backing worker without
a restart.

- **Bots (16):** the four source channels the userbot reads (CallAnalyser2,
  BuyBotTracker, DexSignalCall, ETH Otto Group); premium-group features
  (Premium Callers Signal, and Premium ETH / RBH / SOL / BNB detection, one per
  chain); SOL to ETH, SOL to RBH, ETH Gas Fees; and the infrastructure ones —
  Forwarder (userbot), Bot Commands, Outcome Tracker, Outcome Replies
- **AI (3):** X Links Feed, AI Narrative Agent, Gate Preview (pending)
- **Chains (3)** and **RPCs (3):** ETH, RBH, SOL

Every id and label matches what the rest of the codebase calls the same thing —
the registry's `id`, the Settings label, the `.env` channel name and the log
lines all use one spelling.

## RPC endpoints and failover

Editable live from **RPC Monitor** — the fix for a rate-limited provider is a
new URL, and that should not need an SSH session.

| Chain | Slots |
|---|---|
| Ethereum | 3 discovery WebSockets + 2 HTTP (premium-caller detection) |
| Robinhood | 3 discovery WebSockets + 2 HTTP |
| Solana | 2 WebSockets (discovery + market cap) + 2 HTTP |
| BNB Chain | 2 HTTP (premium-caller detection only — no discovery scanner) |
| ETH Gas Fees | its own WebSocket + HTTP, so its load stays off new-pair detection |

A pool rotates on a rejection and wraps back to the first, so a provider
exhausting its quota costs one reconnect rather than the chain. When every
endpoint in a pool is refusing, one Telegram alert goes out with a
per-endpoint breakdown, and another when it recovers. A single rejection that
self-heals does not alert.

Use a **different provider** for each fallback: a second URL on the same
account shares that account's quota and dies with it.

## Telegram commands

`/start` `/help` `/status` `/services` `/stats` `/watching` `/tokens` `/alerts`
`/gas` `/ping` — all read-only.

`/stop` and `/restart` change what the bot does, so they are checked against
Telegram's own admin list for the chat: whoever the group promotes or demotes
gains or loses access automatically. `/stop` turns off every toggle except Bot
Commands (leaving that on is what keeps `/restart` reachable) and remembers
exactly which ones it touched, so `/restart` undoes that and nothing else.

## Data retention

MongoDB TTL indexes on `dt`, so mongod expires documents in its own background
sweep and the app does no cleanup work: logs 15d, alerts + gas 15d, tokens 30d,
premium archives 15d, AI decisions 15d. All configurable in `.env`.

## Run locally

### Backend (port 8000)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

`.env.example` documents every setting and is kept in step with the `Settings`
class — no orphans, nothing undocumented.

Without MongoDB the backend falls back to an in-memory store, so the dashboard
still runs; nothing persists.

### Frontend (port 3000)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. The frontend proxies `/api/*` to the backend.
Login with `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `backend/.env`.

### Telegram userbot login (one time, for the Forwarder)

The backend runs under uvicorn/systemd, which has no interactive terminal — so
the Telethon login is done once with a separate script, and the app just uses
the resulting `.session` file:

```bash
cd backend
python scripts/telethon_login.py
```

It asks for your phone number, the code Telegram sends you (and your 2FA
password if set), then writes `final_session.session` into `backend/`. Restart
the backend and the Forwarder starts automatically.

You can also run the script on your laptop and copy the `.session` file to the
server — but only ONE process may use that file at a time.

Until the session exists the Forwarder is skipped with a clear message in the
logs; every other scanner keeps running.

## Layout

```
backend/app/
  ai_agent/        the narrative pipeline, in the order it runs:
                   common → grok → notify → tgfilter → judging → feed → reporting
  scanners/
    userbot/       Telethon client, handlers, premium detection, sending
    sol_*.py       Solana discovery + scanner
    eth_scanner.py, robinhood_scanner.py, ws_provider.py, wss_pool.py
  routers/         16 routers, one per dashboard area
  registry.py      every toggle; supervisor.py starts/stops workers to match
frontend/
  app/             one folder per page
  components/
    ui/            primitives (badge, button, card, input, switch)
    layout/        Shell, Sidebar, Topbar, StatusBar, NotificationBell
    features/      pieces that know the domain (DetectionTable, Charts, …)
    <root>         generic pieces that do not (PageHeader, DataTable, …)
```

## Notes that save time later

- **Keywords** match whole words only — `ai` matches "new ai agent", not "main".
- **Groups** can be added by `@username`, a `t.me/…` link, or a numeric chat id.
- **Premium detection** checks the same `0x…` address against Ethereum, Robinhood
  and BNB in parallel: an address does not say which chain it belongs to, and the
  same string can be a live contract on all three. A pool address resolves to the
  token behind it on all three EVM chains; on Solana it is recorded as posted.
- **`AI_DRY_RUN`** records decisions and sets the Telegram flag without sending.
  Read a day of them before turning it off.
