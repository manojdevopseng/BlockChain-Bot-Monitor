"""Small shared helpers.

Routers and scanners both pull from here. Everything in this module is pure —
no database, no config, no imports from the rest of the app — so anything may
import it without risking a cycle.

Several of these existed in three or four places, drifting apart as they went:
IST was declared in four modules, `_esc` had three implementations that
disagreed about None and 0, and a chat id was normalised by one function that
returned an int and another that returned a str.
"""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta, timezone
from typing import Any

# ── Response cleaning ──────────────────────────────────────────────────────────

# Internal fields never returned to the client: Mongo's ObjectId and the
# datetime the TTL index runs on (the float `ts`/`created_at` carries the time).
_INTERNAL = ("_id", "dt")


def clean(doc: dict | None) -> dict | None:
    """Strip internal fields so responses are JSON-serializable."""
    if not doc:
        return doc
    for k in _INTERNAL:
        doc.pop(k, None)
    return doc


def clean_list(docs: list[dict]) -> list[dict]:
    for d in docs:
        for k in _INTERNAL:
            d.pop(k, None)
    return docs


def pct_change(current: float, previous: float) -> float:
    if not previous:
        return 0.0
    return round((current - previous) / previous * 100, 1)


# ── Time (everything user-facing in this app is IST) ───────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))


def ist_day(ts: float | None = None) -> date:
    """The IST calendar day a timestamp falls in. Used by the daily rollover
    and the per-day counters, which must agree on where midnight is."""
    return datetime.fromtimestamp(ts if ts is not None else datetime.now(IST).timestamp(),
                                  IST).date()


def ist_date_str(when: float | date | None = None) -> str:
    """DD-MM-YYYY in IST — the format the History dropdowns and archive keys use.

    Takes either a timestamp or a date, because the two callers had one each.
    """
    if isinstance(when, date):
        return when.strftime("%d-%m-%Y")
    return ist_day(when).strftime("%d-%m-%Y")


# ── Telegram HTML ──────────────────────────────────────────────────────────────

def esc(text: Any) -> str:
    """Escape a value for Telegram's HTML parse mode.

    None becomes empty, but 0 and False stay visible — one of the three old
    copies used `text or ""`, which silently blanked a zero. Only ever called
    on names and symbols today, so nothing rendered differently, but the next
    caller would have found it the hard way.
    """
    return html.escape(str(text if text is not None else ""), quote=False)


# ── Chat ids ───────────────────────────────────────────────────────────────────

def bare_key(chat_id: Any) -> str:
    """Normalise a chat id to its bare form, as a string.

    Telegram hands the same chat around as -1003952803806, 3952803806 or
    -5015581029 depending on where it came from. Anything non-numeric (a
    channel name) is returned unchanged, which is what the per-source counters
    rely on.
    """
    s = str(chat_id).strip()
    if not s.lstrip("-").isdigit():
        return s
    if s.startswith("-100"):
        return s[4:]
    return s.lstrip("-")


def bare_chat_id(chat_id: Any) -> int:
    """Same rule, as an int — for code that stores or compares numeric ids."""
    return int(bare_key(chat_id))


# Chain names as GMGN spells them in a token URL. Ours differ in one place —
# we say "rbh" internally, GMGN says "robinhood" — and anything already in
# GMGN's spelling passes through.
_GMGN_SLUG = {"eth": "eth", "ethereum": "eth", "rbh": "robinhood",
              "robinhood": "robinhood", "sol": "sol", "solana": "sol",
              "bnb": "bsc", "bsc": "bsc"}


def tg_message_url(chat_id: Any, message_id: Any, username: str | None = None) -> str:
    """Deep link to one message, or "" when it cannot be linked to.

    A public group is addressed by @username; a private supergroup by the
    `t.me/c/<bare id>/<msg>` form, which opens only for members of that chat —
    which is the point, these are premium groups.

    `chat_id` must be Telegram's signed id, not the bare one: the -100 prefix
    is the only thing that distinguishes a supergroup from a plain group, and
    plain groups have no message links at all. Without it, return "" rather
    than a link that opens nothing.
    """
    if not message_id:
        return ""
    if username:
        return f"https://t.me/{str(username).lstrip('@')}/{message_id}"
    if str(chat_id).strip().startswith("-100"):
        return f"https://t.me/c/{bare_key(chat_id)}/{message_id}"
    return ""


def gmgn_url(chain: str | None, address: str | None) -> str:
    """A token's page on GMGN, or "" when there is nothing to link to."""
    if not address:
        return ""
    slug = _GMGN_SLUG.get((chain or "").lower(), (chain or "sol").lower())
    return f"https://gmgn.ai/{slug}/token/{address}"
