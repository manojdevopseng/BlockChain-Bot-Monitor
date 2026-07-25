# BlockChain-Bot Monitor

Multichain crypto token monitor with a modern web dashboard.

- **Backend:** FastAPI + MongoDB (Motor) + WebSocket · async throughout
- **Frontend:** Next.js 14 + Tailwind + shadcn/ui + Recharts + Lucide + Framer Motion
- **Realtime:** `/ws` push (heartbeat, service changes)
- **Auth:** JWT login

## Status

| Phase | What | State |
|---|---|---|
| 1 | Backend skeleton (FastAPI, registry, 11 routers, supervisor, WS) | ✅ done |
| 2 | Frontend (11 pages, on/off Settings, charts, dark theme) | ✅ done |
| 3 | Port real scanners (Sol/ETH/Robinhood/Forwarder/GMGN) → Mongo | ⏳ next |
| 4 | Per-tx ETH gas fee + realtime + live scanner toggles | ⏳ |
| 5 | Deploy configs (Nginx + Uvicorn + systemd, EC2 Ubuntu) | ⏳ |

> **MongoDB is optional right now.** If Mongo isn't reachable the backend
> auto-falls back to an in-memory store, so the whole dashboard runs and is
> fully demoable. Install/point Mongo before Phase 3 (real persisted data).

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

### Frontend (port 3000)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. The frontend proxies `/api/*` to the backend.

Default login (dev): `admin` / `admin` (change in `backend/.env`).

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

Until the session exists, the Forwarder is skipped with a clear message in the
logs; every other scanner keeps running.

## On/Off toggles (Settings page)

- **Bots:** ETH Gas Fees, Premium Callers Signal, DexSignalCall, BBCAnalyser2,
  ETH otto Group, SOL to ETH, SOL to RBH, Forwarder
- **Chains:** ETH, RBH, SOL
- **RPCs:** ETH, RBH, SOL

Flipping a toggle persists to the `services` collection and tells the supervisor
to start/stop the backing worker live (no restart).

**Keywords** use whole-word matching only — `ai` matches "new ai agent" but not
"main". **Groups** can be added by `@username`, a `t.me/…` link, or a numeric
chat id.
