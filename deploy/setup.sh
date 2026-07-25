#!/usr/bin/env bash
# BlockChain-Bot — one-shot server setup for Ubuntu 24.04 LTS.
#
#   curl -fsSL .../deploy/setup.sh | bash      (or: bash deploy/setup.sh)
#
# Installs MongoDB, Python, Node.js and nginx, builds both apps and registers
# the systemd services. Safe to re-run: every step is idempotent.
#
# It does NOT start the bot — you must fill in backend/.env first (and, for the
# forwarder, upload the Telethon .session). The script tells you what is left.

set -euo pipefail

REPO_URL="https://github.com/manojdevopseng/BlockChain-Bot-Monitor.git"
APP_DIR="${APP_DIR:-/home/ubuntu/BlockChain-Bot-Monitor}"
NODE_MAJOR=20

say()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── Sanity: MongoDB refuses to start on kernel >= 6.19 (SERVER-121912), which
# is what you get on Ubuntu 26.04. Fail early rather than half-installing.
say "Checking OS"
. /etc/os-release
echo "  $PRETTY_NAME · kernel $(uname -r)"
KMAJ=$(uname -r | cut -d. -f1); KMIN=$(uname -r | cut -d. -f2)
if [ "$KMAJ" -gt 6 ] || { [ "$KMAJ" -eq 6 ] && [ "$KMIN" -ge 19 ]; }; then
    die "Kernel $(uname -r) is too new for MongoDB 8.0 (needs < 6.19).
     Use Ubuntu 24.04 LTS — see SERVER-121912."
fi
[ "${VERSION_ID:-}" = "24.04" ] || warn "Tested on 24.04; you are on ${VERSION_ID:-?}"
ok "OS looks fine"

say "Installing base packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git curl ca-certificates gnupg build-essential \
    python3-venv python3-pip nginx >/dev/null
ok "base packages"

say "Installing MongoDB 8.0"
if ! command -v mongod >/dev/null 2>&1; then
    curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc \
        | sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor --yes
    echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse" \
        | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list >/dev/null
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mongodb-org >/dev/null
fi
sudo systemctl enable --now mongod
sleep 3
systemctl is-active --quiet mongod || die "mongod failed to start — check: journalctl -u mongod -n 30"
ok "MongoDB $(mongod --version | head -1 | awk '{print $3}') running"

say "Installing Node.js ${NODE_MAJOR}"
if ! command -v node >/dev/null 2>&1; then
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | sudo -E bash - >/dev/null 2>&1
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs >/dev/null
fi
ok "node $(node --version) · npm $(npm --version)"

say "Fetching the app"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$APP_DIR"
fi
ok "$APP_DIR"

say "Building backend"
cd "$APP_DIR/backend"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
ok "python deps installed"

if [ ! -f .env ]; then
    cp .env.example .env
    warn "backend/.env created from the template — it is EMPTY, fill it in"
fi

say "Building frontend"
cd "$APP_DIR/frontend"
npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null
npm run build >/dev/null
ok "next build complete"

say "Registering services"
sudo cp "$APP_DIR/deploy/blockchain-bot-api.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/blockchain-bot-web.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable blockchain-bot-api blockchain-bot-web >/dev/null 2>&1
ok "systemd units installed (not started yet)"

say "Configuring nginx"
sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/blockchain-bot
sudo ln -sf /etc/nginx/sites-available/blockchain-bot /etc/nginx/sites-enabled/blockchain-bot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t >/dev/null 2>&1 || die "nginx config test failed — run: sudo nginx -t"
sudo systemctl reload nginx
ok "nginx proxying :80 → dashboard + /api + /ws"

cat <<EOF

──────────────────────────────────────────────────────────────
  Setup complete. Two things left before starting:

  1. Fill in the config (chat ids, RPC urls, GMGN keys):
         nano $APP_DIR/backend/.env

  2. Only if you want the Telegram Forwarder — log in once:
         cd $APP_DIR/backend && ./.venv/bin/python scripts/telethon_login.py
     (or upload an existing final_session.session into backend/)

  Then start everything:
         sudo systemctl start blockchain-bot-api blockchain-bot-web

  Watch it come up:
         journalctl -u blockchain-bot-api -f

  Dashboard:  http://$(curl -s -m 5 ifconfig.me 2>/dev/null || echo '<server-ip>')/
──────────────────────────────────────────────────────────────
EOF
