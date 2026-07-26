# Deploying to EC2 (Ubuntu 24.04 LTS)

## Before you launch the instance

| Setting | Value | Why |
|---|---|---|
| AMI | **Ubuntu Server 24.04 LTS** | MongoDB 8.0 refuses to start on kernel ≥ 6.19 ([SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912)), which is what Ubuntu 26.04 ships. 24.04 is on 6.8. |
| Instance | t3.medium / c7i-flex.large (2 vCPU, 4 GB) | Mongo ~500 MB + backend ~400 MB + Next.js ~300 MB |
| Storage | 30 GB gp3 | TTL retention keeps the DB small; this is mostly headroom |
| Elastic IP | yes | The IP must not change — nginx, DNS and any API allow-lists depend on it |
| Security group | 22 (your IP only), 80, 443 | **Do not open 3000/8000** — both apps bind to 127.0.0.1 and are reached through nginx |

> **Check gmgn.ai from the new box first.** Some IPs are blocked by its
> Cloudflare. Use the *full* browser header set — a bare `curl` with only a
> User-Agent returns 403 even from a working IP:
>
> ```bash
> curl -s -o /dev/null -w '%{http_code}\n' \
>   -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36' \
>   -H 'Accept: application/json, text/plain, */*' \
>   -H 'Accept-Language: en-US,en;q=0.9' \
>   -H 'Referer: https://gmgn.ai/' -H 'Origin: https://gmgn.ai' \
>   'https://gmgn.ai/defi/quotation/v1/pairs/sol/new_pairs/24h?limit=1&period=24h'
> ```
>
> `200` → good. `403` → that IP is blocked; swap the Elastic IP and retry.
> (Only the Solana scanner needs gmgn.ai. ETH/RBH detection uses your own RPC.)

## Install

```bash
ssh -i your-key.pem ubuntu@<server-ip>
git clone https://github.com/manojdevopseng/BlockChain-Bot-Monitor.git
bash BlockChain-Bot-Monitor/deploy/setup.sh
```

The script installs MongoDB, Python, Node.js and nginx, builds both apps and
registers the systemd units. It is idempotent — re-run it any time.

## Configure

```bash
nano /home/ubuntu/BlockChain-Bot-Monitor/backend/.env
```

Every variable is documented in the file. The ones that actually matter:

| Group | Keys |
|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN`, `ALERT_CHAT_ID`, `GAS_ALERT_CHAT_ID`, `CROSS_CHAIN_CHAT_ID`, `ROBINHOOD_CHAT_ID`, `DEST_*` |
| Userbot | `TELETHON_API_ID`, `TELETHON_API_HASH` |
| RPC | `ETH_RPC_WSS` + `ETH_RPC_HTTP`, `RBH_RPC_WSS` + `RBH_RPC_HTTP`, `SOL_RPC_HTTP` |
| GMGN | `GMGN_API_KEY`, `GMGN_CLIENT_ID`, `GMGN_DEVICE_ID`, `GMGN_FP_DID` |
| Security | `JWT_SECRET`, `ADMIN_PASSWORD` — **change both** |

A blank value means "use the default"; a blank chat id disables that route
rather than erroring.

## Telegram userbot login (only for the Forwarder)

The backend runs under systemd, which has no terminal — so Telethon can't ask
for a phone number there. Log in once with the helper script:

```bash
cd /home/ubuntu/BlockChain-Bot-Monitor/backend
./.venv/bin/python scripts/telethon_login.py
```

Or upload a session created elsewhere:

```bash
scp -i your-key.pem final_session.session ubuntu@<server-ip>:/home/ubuntu/BlockChain-Bot-Monitor/backend/
```

Only one process may use a `.session` file at a time — don't run the bot
locally and on the server against the same file.

Until the session exists the Forwarder is skipped with a clear log line; every
other scanner runs normally.

## Start

```bash
sudo systemctl start blockchain-bot-api blockchain-bot-web
journalctl -u blockchain-bot-api -f
```

Healthy startup looks like:

```
[SOL] Scanner started — interval: 5.0s | trigger: MCap >= $40,000 AND fees >= 1.0 SOL
[ETH] WebSocket connected ✓        versions: V2+V3+V4
[ROBINHOOD] WebSocket connected ✓  versions: NOXA
[GasMonitor] ETH Gas Fees armed — alert when an early buy pays >= 0.0001 ETH gas
```

Then open `http://<server-ip>/` and log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

## HTTPS (optional, needs a domain)

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d bot.yourdomain.com
```

Certbot rewrites the nginx block and sets up auto-renewal. Afterwards add the
HTTPS origin to `CORS_ORIGINS` in `.env` and restart the API.

## Updating

```bash
cd /home/ubuntu/BlockChain-Bot-Monitor
git pull
backend/.venv/bin/pip install -q -r backend/requirements.txt
cd frontend && npm ci --no-audit --no-fund && npm run build && cd ..
sudo systemctl restart blockchain-bot-api blockchain-bot-web
```

## Day-to-day

```bash
# status / logs
systemctl status blockchain-bot-api blockchain-bot-web mongod
journalctl -u blockchain-bot-api -f
journalctl -u blockchain-bot-api -p err -n 50     # errors only

# restart just the bot (dashboard stays up)
sudo systemctl restart blockchain-bot-api

# what the app thinks about itself
curl -s localhost:8000/api/health | python3 -m json.tool
curl -s localhost:8000/api/system/retention | python3 -m json.tool
```

**Toggles, keywords, groups and the GMGN credentials are all editable from the
dashboard** (Settings) — you rarely need to SSH in after the first setup.
Changing a toggle takes effect immediately; changing GMGN credentials is
applied live without a restart.

## Data retention

MongoDB TTL indexes expire old documents in mongod's own background sweep, so
the app does no cleanup work:

| Collection | Kept | Env var |
|---|---|---|
| `logs` | 15 days | `LOG_RETENTION_DAYS` |
| `alerts`, `gas_alerts` | 15 days | `ALERT_RETENTION_DAYS` |
| `tokens` | 30 days | `TOKEN_RETENTION_DAYS` |
| `premium_archive` | 15 days | `ARCHIVE_RETENTION_DAYS` |
| `forwarder_counters` | 15 days | `LOG_RETENTION_DAYS` |

Change a value, restart the API, and the TTL index is updated in place.

## Backups

Retention deletes old rows on purpose; a backup is for the things that cannot
be rebuilt from the repo:

| | Why it matters |
|---|---|
| MongoDB | premium groups, keywords, otto rules, service toggles, command counters |
| `backend/.env` | bot token, GMGN keys, RPC URLs, every chat id |
| `backend/*.session` | the Telethon authorization — the only file here with no other way back than logging the userbot in again |

`deploy/backup.sh` packs all three into one timestamped `.tar.gz`, verifies the
archive is readable, and keeps the last 14.

```bash
sudo cp deploy/backup.sh /usr/local/bin/bcbot-backup
sudo chmod +x /usr/local/bin/bcbot-backup
( crontab -l 2>/dev/null; echo "17 3 * * * /usr/local/bin/bcbot-backup >/dev/null 2>&1" ) | crontab -
```

Runs nightly at 03:17 UTC. Output goes to `/home/ubuntu/backups/backup.log`.

The archive contains live credentials and a Telegram authorization, so the
directory is `0700` and every file `0600`. Keep it that way — a readable copy of
the session file is as good as handing over the account.

### These live on the instance

If the instance is lost, so are they. Copy them off periodically:

```bash
scp -i your-key.pem ubuntu@YOUR_IP:/home/ubuntu/backups/bcbot-*.tar.gz ./
```

### Restoring

```bash
tar -xzf bcbot-YYYYMMDD-HHMMSS.tar.gz -C /tmp/restore
```

Then, with the API stopped (`sudo systemctl stop blockchain-bot-api`):

```bash
mongorestore --archive=/tmp/restore/mongo.archive --gzip --drop
```

Put `.env` back at `backend/.env` and each `*.session` next to it, `chmod 600`
both, and start the API again.

To check an archive without touching the live database, restore it under a
different name and compare — this is how the script's output was verified:

```bash
mongorestore --archive=/tmp/restore/mongo.archive --gzip --nsFrom='blockchain_bot.*' --nsTo='bcbot_check.*'
```

`logs` will differ by a few rows: the app keeps writing between the dump and the
comparison. Every other collection should match exactly.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `mongod` won't start | Kernel ≥ 6.19 → you're on Ubuntu 26.04. Rebuild on 24.04. |
| Dashboard loads, no data | `journalctl -u blockchain-bot-api -n 50`. Usually a missing RPC URL. |
| `[SOL] Scanner error: HTTP 403` | gmgn.ai blocked this IP, or the GMGN fingerprint expired. Refresh it in Settings → GMGN Credentials. |
| Forwarder never starts | No `.session` file — run `scripts/telethon_login.py`, or restore one from a backup. |
| WebSocket keeps reconnecting | nginx `/ws` block missing or timeouts too low — re-copy `deploy/nginx.conf`. |
| No Telegram messages | `TELEGRAM_BOT_TOKEN` blank, or that route's chat id is blank (both are logged as DRY-RUN). |
