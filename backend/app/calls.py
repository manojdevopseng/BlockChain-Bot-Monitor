"""The Second Dashboard's own store: one document per call, not per token.

The first dashboard's panel answers "which tokens have premium callers named,
and how many named each" — so it merges, keeping one row per token and pushing
every later caller into `group_entries`. That is the right shape for that
question and the wrong shape for this one.

Here every call is its own row. The same token called by four groups is four
rows, and called twice by one group is two, because the point is to read the
sequence: who said it, when, in what words, and what they said the second time.
Nothing merges, nothing is overwritten.

Both dashboards are fed from the same message the userbot already has. Nothing
in this module opens an RPC connection, a Telegram session or a second read of
anything — it is handed what the premium capture already resolved.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import db
from .util import gmgn_url

# Telegram sends photos far larger than a feed row needs, and a document over
# Mongo's 16MB limit is rejected outright. Anything past this is dropped rather
# than stored, and the row simply carries no picture.
MEDIA_MAX_BYTES = 4 * 1024 * 1024

# What the tracker quotes. The panel shows the whole message when it is opened;
# this is the cap on what is stored, so one pasted essay cannot bloat a day's
# worth of rows.
TEXT_MAX = 4000

IST = timezone(timedelta(hours=5, minutes=30))


def _day(ts: float) -> str:
    """The IST calendar day, matching the History dropdowns elsewhere."""
    return datetime.fromtimestamp(ts, IST).strftime("%d-%m-%Y")


async def save_media(raw: bytes, mime: str = "image/jpeg",
                     cap: int = MEDIA_MAX_BYTES) -> Optional[str]:
    """Store one picture and return the id the row will reference.

    Content-addressed: the same picture forwarded by six groups is stored once.
    Callers repeat each other's graphics constantly, so this is not a micro
    optimisation — it is most of the disk.
    """
    if not raw or len(raw) > cap:
        return None
    mid = hashlib.sha256(raw).hexdigest()[:32]
    col = db.get_collection("premium_media")
    now = time.time()
    # `dt` is the TTL anchor every collection here uses. Touching it on a
    # repeat keeps a picture alive while it is still being posted, and lets it
    # expire once it stops.
    await col.update_one(
        {"mid": mid},
        {"$set": {"mid": mid, "mime": mime, "dt": datetime.now(timezone.utc)},
         "$setOnInsert": {"data": raw, "bytes": len(raw), "first_seen": now}},
        upsert=True,
    )
    return mid


async def get_media(mid: str) -> Optional[dict]:
    return await db.get_collection("premium_media").find_one({"mid": mid})


async def record(
    *,
    chain: str,
    address: str,
    symbol: str = "",
    name: str = "",
    chat_id: int,
    group: str = "",
    username: Optional[str] = None,
    followers: Optional[int] = None,
    msg_id: Optional[int] = None,
    post_url: str = "",
    text: str = "",
    reply_to: Optional[str] = None,
    reply_text: str = "",
    media_id: Optional[str] = None,
    keyword: str = "",
    ts: Optional[float] = None,
) -> None:
    """Write one call.

    Idempotent on (chat_id, msg_id, chain, address) so a message the userbot
    happens to see twice — a reconnect replaying it, an edit — does not become
    two rows. Deliberately the only thing here that de-duplicates: a genuine
    second call from the same group is a different message id, and gets its own
    row.
    """
    now = ts or time.time()
    addr = (address or "").strip()
    if not addr or not chat_id:
        return
    key = {
        "chat_id": int(chat_id),
        "msg_id": int(msg_id) if msg_id is not None else None,
        "chain": chain,
        # Solana mints are base58 and case-carrying; only hex addresses fold.
        "address": addr if chain == "sol" else addr.lower(),
    }
    doc: dict[str, Any] = {
        **key,
        "symbol": symbol or "",
        "name": name or "",
        "group": group or "",
        "username": username or None,
        "followers": followers,
        "post_url": post_url or "",
        "text": (text or "")[:TEXT_MAX],
        "reply_to": reply_to or None,
        "reply_text": (reply_text or "")[:280],
        "media_id": media_id or None,
        "keyword": keyword or "",
        "gmgn_url": gmgn_url(chain, addr),
        "ts": now,
        "day": _day(now),
        "dt": datetime.now(timezone.utc),
    }
    res = await db.get_collection("premium_calls").update_one(
        key, {"$setOnInsert": doc}, upsert=True)

    # Push it. The dashboard's poll is a safety net measured in seconds, and a
    # call is the one thing on that screen worth seeing the moment it lands —
    # Telegram shows it instantly, so a feed that trails it by ten seconds is
    # not a feed. Only on a real insert: an upsert that matched an existing row
    # is the same call arriving twice and has nothing to announce.
    if getattr(res, "upserted_id", None) is not None:
        await _push("premium_call", {k: v for k, v in doc.items() if k != "dt"})


async def known_chains(address: str) -> list[dict]:
    """Which chains this address is already confirmed on, and its token facts.

    The repeat path. A group calling the same token a second time must produce
    a second row, but it must not cost a second round of RPC checks — the
    chains were already established the first time anyone posted it, and that
    answer does not change. So the repeat is answered out of what the first
    dashboard already recorded.

    Empty means nobody has confirmed this address yet, in which case there is
    nothing to record and the normal detection path is the one that will do it.
    """
    addr = (address or "").strip()
    if not addr:
        return []
    col = db.get_collection("premium_detections")
    import re as _re
    docs = await col.find(
        {"address": {"$regex": f"^{_re.escape(addr)}$", "$options": "i"}}
    ).to_list(8)
    return [{"chain": d.get("chain") or "", "address": d.get("address") or addr,
             "symbol": d.get("symbol") or "", "name": d.get("name") or "",
             "keyword": d.get("keyword") or ""}
            for d in docs if d.get("chain")]


async def record_all(hits: list[dict], **ctx) -> None:
    """Write one row per chain the address is real on, from a single message."""
    for hit in hits:
        await record(
            chain=hit["chain"], address=hit["address"],
            symbol=hit.get("symbol", ""), name=hit.get("name", ""),
            keyword=hit.get("keyword", ""), **ctx,
        )


# ── the tracker's own store ────────────────────────────────────────────────
#
# One document per message, written the instant it arrives — before any chain
# check has run, and whether or not it carries a token at all. That is the
# whole point: the Premium Callers mirror group shows every message with no
# delay because it forwards on the message and asks nothing, and a tracker that
# waits for an RPC round trip can only ever trail it.
#
# Tokens are attached afterwards, as each chain check comes back. So a call
# appears immediately as text and grows its chain chips a moment later, rather
# than appearing late and complete.

async def record_message(
    *,
    chat_id: int,
    msg_id: int,
    group: str = "",
    username: Optional[str] = None,
    followers: Optional[int] = None,
    post_url: str = "",
    text: str = "",
    reply_to: Optional[str] = None,
    reply_text: str = "",
    media_id: Optional[str] = None,
    kind: str = "text",
    tg_ts: Optional[float] = None,
    ts: Optional[float] = None,
) -> None:
    now = ts or time.time()
    key = {"chat_id": int(chat_id), "msg_id": int(msg_id)}
    doc = {
        **key,
        "group": group or "",
        "username": username or None,
        "followers": followers,
        "post_url": post_url or "",
        "text": (text or "")[:TEXT_MAX],
        "reply_to": reply_to or None,
        "reply_text": (reply_text or "")[:280],
        "media_id": media_id or None,
        # photo / video / gif / voice / sticker / document / text — what the
        # caller actually posted, which is also what decides whether there is
        # anything for the tracker to fetch afterwards.
        "kind": kind or "text",
        "tokens": [],
        # Telegram's clock, and ours. The feed shows the first; the gap between
        # them is what says whether this pipeline is keeping up.
        "tg_ts": tg_ts,
        "ts": now,
        "day": _day(now),
        "dt": datetime.now(timezone.utc),
    }
    res = await db.get_collection("premium_messages").update_one(
        key, {"$setOnInsert": doc}, upsert=True)
    if getattr(res, "upserted_id", None) is not None:
        await _push("premium_message", {k: v for k, v in doc.items() if k != "dt"})


async def update_message(chat_id: int, msg_id: int, **fields) -> None:
    """Fill in what was not known when the message was first written.

    The row is created from what is already in memory, so it can be on screen
    in a millisecond. The reply handle, the subscriber count and the picture
    are all API round trips, and they land here afterwards.
    """
    fields = {k: v for k, v in fields.items() if v is not None and v != ""}
    if not fields:
        return
    key = {"chat_id": int(chat_id), "msg_id": int(msg_id)}
    res = await db.get_collection("premium_messages").update_one(key, {"$set": fields})
    if getattr(res, "modified_count", 0):
        await _push("premium_message_token", {**key, "updated": sorted(fields)})


async def attach_token(chat_id: int, msg_id: Optional[int], chain: str,
                       address: str, symbol: str = "") -> None:
    """Hang a resolved token off the message that named it.

    `$addToSet` on the chain, so the same message checked against a chain twice
    — a retry, a reconnect replaying it — does not grow a duplicate chip.
    """
    if msg_id is None or not chain or not address:
        return
    token = {"chain": chain, "address": address, "symbol": symbol or "",
             "gmgn_url": gmgn_url(chain, address)}
    key = {"chat_id": int(chat_id), "msg_id": int(msg_id)}
    res = await db.get_collection("premium_messages").update_one(
        key, {"$addToSet": {"tokens": token}})
    if getattr(res, "modified_count", 0):
        await _push("premium_message_token", {**key, "token": token})


async def _push(event: str, payload: dict) -> None:
    """Announce something to the dashboards, never at the cost of the write."""
    try:
        from .ws_hub import hub
        await hub.broadcast(event, payload)
    except Exception:  # noqa: BLE001
        pass
