"""Inline buttons on Telegram alerts, and the mute list behind them.

An alert used to be a dead end: to act on it you copied the address, and to
stop a noisy token you opened the dashboard. These add the two things worth
having on the message itself.

  • Link buttons — GMGN and a trade deep link, address already filled in.
    These need no callback at all: Telegram opens the URL itself.
  • Mute — one callback that writes to `mutes`, which the alert paths check
    before sending. Muting a token silences that token; muting a group stops
    its calls being announced.

Deliberately no buy button. Trading from the server would mean a private key
on the box, which is a different class of risk entirely — the link hands off to
a wallet the user already controls.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from . import db
from .scanners.slog import get_logger

log = get_logger(__name__)

MUTE_SECONDS = 24 * 3600      # what "mute" means, unless a button says otherwise

_GMGN_SLUG = {"eth": "eth", "ethereum": "eth",
              "robinhood": "robinhood", "rbh": "robinhood",
              "sol": "sol", "solana": "sol"}


def _slug(chain: str | None) -> str:
    return _GMGN_SLUG.get((chain or "").lower(), (chain or "eth").lower())


def keyboard(*, chain: str, address: str, symbol: str = "",
             group: str | None = None) -> dict:
    """The inline keyboard for an alert.

    callback_data is capped at 64 bytes by Telegram, so it carries a short
    action plus the address — never a whole payload.
    """
    slug = _slug(chain)
    rows: list[list[dict]] = [[
        {"text": "📊 GMGN", "url": f"https://gmgn.ai/{slug}/token/{address}"},
        {"text": "📈 Chart", "url": f"https://dexscreener.com/{slug}/{address}"},
    ]]
    # Maestro/Banana take a Telegram deep link with the contract prefilled;
    # only meaningful on ETH.
    if slug == "eth":
        rows.append([
            {"text": "🤖 Maestro", "url": f"https://t.me/maestro?start={address}"},
            {"text": "🍌 Banana", "url": f"https://t.me/BananaGunSniper_bot?start=snp_{address}"},
        ])
    mute: list[dict] = [{"text": "🔇 Mute token 24h", "callback_data": f"mt:{address[:56]}"}]
    if group:
        mute.append({"text": "🔇 Mute group 24h", "callback_data": f"mg:{group[:56]}"})
    rows.append(mute)
    return {"inline_keyboard": rows}


# ── Mute list ──────────────────────────────────────────────────────────────────

def _col():
    return db.get_collection("mutes")


async def mute(kind: str, key: str, seconds: int = MUTE_SECONDS) -> float:
    """Silence a token or a group until a moment in the future."""
    until = time.time() + seconds
    await _col().update_one(
        {"kind": kind, "key": key.lower()},
        {"$set": {"kind": kind, "key": key.lower(), "until": until,
                  "muted_at": time.time()}},
        upsert=True,
    )
    return until


async def is_muted(kind: str, key: Optional[str]) -> bool:
    """Checked on the alert path, so it must never raise — a database problem
    should not turn into silence."""
    if not key:
        return False
    try:
        doc = await _col().find_one({"kind": kind, "key": str(key).lower()})
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[MUTE] lookup failed for {kind}:{key}: {exc}")
        return False
    return bool(doc and float(doc.get("until") or 0) > time.time())


async def active() -> list[dict]:
    now = time.time()
    try:
        docs = await _col().find({}).to_list(500)
    except Exception:
        return []
    out = []
    for d in docs:
        if float(d.get("until") or 0) <= now:
            continue
        d.pop("_id", None)
        d["minutes_left"] = round((d["until"] - now) / 60)
        out.append(d)
    out.sort(key=lambda d: d["until"])
    return out


async def unmute(kind: str, key: str) -> bool:
    res = await _col().delete_many({"kind": kind, "key": key.lower()})
    return bool(res.deleted_count)


# ── Callback handling (called by the command poller) ───────────────────────────

async def handle_callback(cb: dict) -> tuple[str, bool]:
    """Act on a button press. Returns (text for the toast, show_alert)."""
    data = str(cb.get("data") or "")
    if data.startswith("mt:"):
        key = data[3:]
        until = await mute("token", key)
        mins = round((until - time.time()) / 60)
        log.info(f"[MUTE] token {key[:12]}… muted for {mins} min")
        return (f"Muted this token for {mins // 60}h", False)
    if data.startswith("mg:"):
        key = data[3:]
        until = await mute("group", key)
        mins = round((until - time.time()) / 60)
        log.info(f"[MUTE] group {key} muted for {mins} min")
        return (f"Muted {key} for {mins // 60}h", False)
    return ("Unknown button", False)
