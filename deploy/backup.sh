#!/usr/bin/env bash
#
# Nightly backup of everything that cannot be rebuilt from the repo.
#
#   1. MongoDB      — premium groups, keywords, otto rules, service toggles,
#                     command definitions and their counters, detections
#   2. .env         — bot token, GMGN keys, RPC URLs, every chat id
#   3. *.session    — the Telethon authorization. Losing this means logging the
#                     userbot in again from a terminal; nothing else can replace it.
#
# Everything else (code, seed data) is in git.
#
# The archive holds live credentials AND a Telegram authorization, so the
# directory is 0700 and each file 0600. A readable copy of the session file is
# as good as handing over the account.
#
# Install (once):
#   sudo cp deploy/backup.sh /usr/local/bin/bcbot-backup
#   sudo chmod +x /usr/local/bin/bcbot-backup
#   ( crontab -l 2>/dev/null; echo "17 3 * * * /usr/local/bin/bcbot-backup" ) | crontab -
#
# Restore: see deploy/DEPLOY.md — it is a deliberate act, not a script flag.

set -uo pipefail

APP_DIR="${BCBOT_DIR:-/home/ubuntu/BlockChain-Bot-Monitor}"
BACKUP_DIR="${BCBOT_BACKUP_DIR:-/home/ubuntu/backups}"
KEEP="${BCBOT_BACKUP_KEEP:-14}"          # how many nightly archives to hold
DB_NAME="${BCBOT_DB:-blockchain_bot}"
LOG="$BACKUP_DIR/backup.log"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
OUT="$BACKUP_DIR/bcbot-$STAMP.tar.gz"

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
die() { log "FAILED: $*"; rm -rf "$WORK"; exit 1; }

umask 077
mkdir -p "$BACKUP_DIR" || die "cannot create $BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log "backup start -> $OUT"

# ── 1. MongoDB ────────────────────────────────────────────────────────────────
# --archive writes one file; safe to run against a live mongod.
mongodump --db "$DB_NAME" --archive="$WORK/mongo.archive" --gzip --quiet \
  || die "mongodump"
[ -s "$WORK/mongo.archive" ] || die "mongodump produced an empty archive"

# ── 2 & 3. Secrets and the Telethon session ───────────────────────────────────
cp "$APP_DIR/backend/.env" "$WORK/.env" || die "cannot read backend/.env"
SESSIONS=0
for f in "$APP_DIR"/backend/*.session; do
  [ -e "$f" ] || continue
  cp "$f" "$WORK/" && SESSIONS=$((SESSIONS + 1))
done
# Not fatal — the userbot may simply not be logged in on this host yet — but it
# is the one file that cannot be regenerated, so say so loudly.
[ "$SESSIONS" -gt 0 ] || log "WARNING: no .session file found; a restore will need a fresh Telethon login"

# ── Pack ──────────────────────────────────────────────────────────────────────
tar -czf "$OUT" -C "$WORK" . || die "tar"
chmod 600 "$OUT"

# Prove it is readable before trusting it — a truncated archive is worse than
# no archive, because it looks like one.
tar -tzf "$OUT" >/dev/null 2>&1 || die "archive is not readable"

SIZE="$(du -h "$OUT" | cut -f1)"
FILES="$(tar -tzf "$OUT" | grep -c . || true)"
log "ok — $SIZE, $FILES entries, $SESSIONS session file(s)"

# ── Rotate ────────────────────────────────────────────────────────────────────
mapfile -t OLD < <(ls -1t "$BACKUP_DIR"/bcbot-*.tar.gz 2>/dev/null | tail -n "+$((KEEP + 1))")
for f in "${OLD[@]:-}"; do
  [ -n "$f" ] && rm -f "$f" && log "pruned $(basename "$f")"
done

rm -rf "$WORK"
log "kept $(ls -1 "$BACKUP_DIR"/bcbot-*.tar.gz 2>/dev/null | wc -l) archive(s), keep=$KEEP"
