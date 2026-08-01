"""Telegram forwarder routes — sources, destinations, per-source toggle,
and the premium-caller address detection panels (ETH / RBH / SOL)."""

from __future__ import annotations

import re
import time
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db, fwd_counters, registry
from ..scanners import scfg, userbot
from ..util import gmgn_url, clean_list, ist_date_str

router = APIRouter(prefix="/api/forwarder", tags=["forwarder"])

# How many rows a search pass reads before counting. Only reached when `q` is
# set; the plain view counts in Mongo instead.
_MATCH_SCAN_CAP = 20000

# GMGN token-page slug per chain (for the "view on GMGN" link).
def _gmgn_url(chain: str, address: str) -> str:
    return gmgn_url(chain, address)


# The four signal channels the userbot listens to, and the registry switch that
# gates each one. Names come from .env — nothing hardcoded. The gate ids come
# from the userbot's own GATE_* constants rather than being retyped here, so
# this page and the handler that actually reads the switch cannot drift apart.
def _signal_channels() -> list[tuple[str, str, str]]:
    """(channel name, registry service id, what it feeds)"""
    return [
        (scfg.SOURCE_CALL,   userbot.GATE_CALL,   "first-call signals → DEST_SIGNALS"),
        (scfg.SOURCE_BUYBOT, userbot.GATE_BUYBOT, "new-group signals → DEST_SIGNALS"),
        (scfg.SOURCE_DEXS,   userbot.GATE_DEXS,   "DEX signals → DEST_DEXS"),
        (scfg.SOURCE_OTTO,   userbot.GATE_OTTO,   "Otto deployments → DEST_OTTO"),
    ]


def _destinations() -> list[tuple[str, str, str]]:
    """(env key, chat id, what gets sent there)"""
    return [
        ("DEST_OTTO",               scfg.DEST_OTTO,               "Otto method/function hash matches"),
        ("DEST_SIGNALS",            scfg.DEST_SIGNALS,            "CallAnalyser2 first-calls + BuyBotTracker"),
        ("DEST_DEXS",               scfg.DEST_DEXS,               "DEX signals from dexssignal"),
        ("DEST_PREMIUM_ALL",        scfg.DEST_PREMIUM_ALL,        "Raw mirror of every premium message"),
        ("DEST_PREMIUM_ETH",        scfg.DEST_PREMIUM_BY_CHAIN["eth"], "ETH premium-caller detections"),
        ("DEST_PREMIUM_RBH",        scfg.DEST_PREMIUM_BY_CHAIN["rbh"], "RBH premium-caller detections"),
        ("DEST_PREMIUM_BNB",        scfg.DEST_PREMIUM_BY_CHAIN["bnb"], "BNB premium-caller detections"),
        ("DEST_PREMIUM_SOL",        scfg.DEST_PREMIUM_BY_CHAIN["sol"], "SOL premium-caller detections"),
        ("DEST_IMPORTANT_CALLER",   scfg.DEST_IMPORTANT_CALLER,   "Messages from starred callers only"),
    ]


@router.get("/sources")
async def sources():
    """What the userbot actually reads: the four signal channels plus every
    enabled premium group.

    This used to list the `forwarder_sources` collection, which only ever holds
    groups added from the dashboard — so it was empty while the userbot was
    reading 111 groups, and its toggle wrote to a field nothing consumed.
    """
    counts = await fwd_counters.today(fwd_counters.SOURCE)
    enabled = await registry.enabled_map()

    items = [
        {
            "key": name, "name": name, "kind": "channel",
            "subtitle": feeds,
            "enabled": bool(enabled.get(service, True)),
            "service": service,
            "today": counts.get(name, 0),
        }
        for name, service, feeds in _signal_channels() if name
    ]

    rows = await db.get_collection("premium_groups").find({}).to_list(5000)
    await _backfill_names(rows)

    for g in rows:
        gid = g.get("id")
        if gid is None:
            continue
        # The chat id is always on the row, next to the name — knowing which
        # group an id belongs to is the whole point of showing the name. Both
        # forms are shown: ids are stored bare, but -100… is what Telegram
        # displays and therefore what gets pasted into the search box.
        full = f"-100{gid}"
        subtitle = f"{full} · {gid}"
        if g.get("username"):
            subtitle = f"@{g['username']} · {subtitle}"
        items.append({
            "key": str(gid), "kind": "group",
            "name": g.get("name") or str(gid),
            "chat_id": gid,
            "subtitle": subtitle,
            "named": bool(g.get("name")),
            "enabled": g.get("enabled", True) is not False,
            "today": counts.get(fwd_counters.bare_key(gid), 0),
            "chip": g.get("chip") or None,
            "ic": bool(g.get("ic")),
        })
    return {"items": items}


async def _backfill_names(rows: list[dict], limit: int = 8) -> None:
    """Give every group its real Telegram title and @username.

    Rows arrived by three different routes — seeded from JSON with a slug for a
    name, added by chat id before we asked Telegram, or added while the userbot
    was logged out — so they showed inconsistent detail. Each is resolved once
    against Telegram and written back.

    `resolved_at` records that we tried, whether or not it worked. Without it a
    group with no @username (most private ones) would be looked up again on
    every single page load. Bounded to `limit` per request, so a long list
    fills in over a few loads instead of one slow one.
    """
    todo = [r for r in rows if r.get("id") is not None and not r.get("resolved_at")][:limit]
    if not todo:
        return
    col = db.get_collection("premium_groups")
    for row in todo:
        name, username = await _title_of(int(row["id"]))
        # The seeded name is a fallback, not the truth — Telegram's own title
        # wins where we can get it.
        update = {"resolved_at": time.time()}
        if name:
            update["name"] = name
        if username:
            update["username"] = username
        row.update(update)
        await col.update_one({"id": row["id"]}, {"$set": update})


@router.get("/destinations")
async def destinations():
    """Where the userbot forwards, straight from .env."""
    counts = await fwd_counters.today(fwd_counters.DEST)
    return {"items": [
        {
            "key": key, "chat_id": cid, "purpose": purpose,
            "configured": bool(cid),
            "today": counts.get(fwd_counters.bare_key(cid), 0) if cid else 0,
        }
        for key, cid, purpose in _destinations()
    ]}


@router.get("/stats")
async def stats():
    src_counts = await fwd_counters.today(fwd_counters.SOURCE)
    dst_counts = await fwd_counters.today(fwd_counters.DEST)
    groups = await db.get_collection("premium_groups").count_documents({"enabled": {"$ne": False}})
    channels = len([n for n, _s, _f in _signal_channels() if n])
    return {
        "total_sources": groups + channels,
        "total_groups": groups,
        "messages_today": sum(src_counts.values()),
        "forwarded_today": sum(dst_counts.values()),
        "destinations": len([c for _k, c, _p in _destinations() if c]),
    }


@router.patch("/sources/{key}")
async def toggle_source(key: str, payload: dict = Body(...)):
    """Switch a source off where the forwarder actually looks.

    A premium group flips `premium_groups.enabled` — the field the userbot's
    `store.load_premium_ids` reads. A signal channel flips its registry service,
    the same switch as Settings → Bots, so the two can never disagree.
    """
    if "enabled" not in payload:
        raise HTTPException(400, "body must include 'enabled'")
    enabled = bool(payload["enabled"])

    for name, service, _feeds in _signal_channels():
        if name and key == name:
            svc = await registry.set_enabled(service, enabled)
            if svc is None:
                raise HTTPException(404, f"unknown service '{service}'")
            return {"key": key, "kind": "channel", "enabled": enabled}

    try:
        gid = int(key)
    except ValueError:
        raise HTTPException(404, f"unknown source '{key}'")
    res = await db.get_collection("premium_groups").update_one(
        {"id": gid}, {"$set": {"enabled": enabled}}
    )
    if not res.matched_count:
        raise HTTPException(404, f"unknown premium group '{key}'")
    return {"key": key, "kind": "group", "enabled": enabled}


# ── Per-caller chip colours ─────────────────────────────────────────────────────
#
# Each premium group can carry its own chip style, so a call from a group you
# trust is recognisable at a glance in the Detections table instead of being
# one more identical grey pill. Three free colours — background, text, border —
# because that is what the chip is made of.
#
# Stored as hex on the group itself, which means one style for both themes: a
# colour picked while in dark mode is the same colour in light mode. The picker
# previews both for that reason. No style stored = the default grey chip, so
# nothing changes for a group until it is given one.

_CHIP_FIELDS = ("bg", "text", "border")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _clean_chip(payload) -> dict | None:
    """Validate a chip style. None means "clear it and go back to default"."""
    if not payload:
        return None
    if not isinstance(payload, dict):
        raise HTTPException(400, "chip must be an object")
    chip = {}
    for field in _CHIP_FIELDS:
        value = str(payload.get(field) or "").strip()
        if not value:
            raise HTTPException(400, f"chip.{field} is required")
        if not _HEX.match(value):
            # Rejected here rather than stored: an unparseable colour reaches
            # the browser as a style that silently does nothing, and the chip
            # looks broken with no clue why.
            raise HTTPException(400, f"chip.{field} must be a hex colour like #7c5cff, got {value!r}")
        chip[field] = value.lower()
    return chip


@router.patch("/sources/{key}/chip")
async def set_source_chip(key: str, payload: dict = Body(default=None)):
    """Set (or clear) one premium group's chip colours.

    Body `{"bg": "#…", "text": "#…", "border": "#…"}`, or an empty body to drop
    back to the default chip.
    """
    try:
        gid = int(key)
    except ValueError:
        raise HTTPException(404, f"unknown premium group '{key}'")
    chip = _clean_chip(payload)
    update = {"$set": {"chip": chip}} if chip else {"$unset": {"chip": ""}}
    res = await db.get_collection("premium_groups").update_one({"id": gid}, update)
    if not res.matched_count:
        raise HTTPException(404, f"unknown premium group '{key}'")
    return {"key": key, "chip": chip}


@router.patch("/sources/{key}/ic")
async def set_source_ic(key: str, payload: dict = Body(...)):
    """Star (or unstar) a premium group for the Important Caller mirror.

    From the next message on: this marks the group, it does not go back over
    what it has already posted.
    """
    if "on" not in payload:
        raise HTTPException(400, "body must include 'on'")
    on = bool(payload["on"])
    try:
        gid = int(key)
    except ValueError:
        raise HTTPException(404, f"unknown premium group '{key}'")
    update = {"$set": {"ic": True}} if on else {"$unset": {"ic": ""}}
    res = await db.get_collection("premium_groups").update_one({"id": gid}, update)
    if not res.matched_count:
        raise HTTPException(404, f"unknown premium group '{key}'")
    # Push it to the running userbot rather than waiting for its reload timer,
    # so the next message from that group is already mirrored.
    fwd = _userbot()
    if fwd is not None:
        try:
            await fwd.reload_ic_ids()
        except Exception:  # noqa: BLE001
            pass  # the timer will pick it up
    return {"key": key, "ic": on}


@router.get("/group-chips")
async def group_chips():
    """chat id -> chip style, for every group that has one.

    Its own endpoint, and deliberately not part of the detections payload: a
    group called by 100 rows would otherwise repeat its three colours 100
    times. It is also cheap enough to poll — unlike /sources, it does no
    Telegram name resolution.
    """
    rows = await db.get_collection("premium_groups").find(
        {"chip": {"$exists": True, "$ne": None}}, {"id": 1, "chip": 1}
    ).to_list(5000)
    return {"chips": {str(r["id"]): r["chip"] for r in rows if r.get("id") is not None}}


# ── Adding / removing premium groups ────────────────────────────────────────────

def _userbot():
    """The running Telethon client, or None. Resolving a name or @username
    needs the userbot — the bot API cannot see groups it is not in."""
    from .. import supervisor
    fwd = supervisor.instance("fwd")
    client = getattr(fwd, "_client", None) if fwd is not None else None
    return client if client is not None and client.is_connected() else None


async def _title_of(bare_id: int) -> tuple[str | None, str | None]:
    """(title, username) for a chat id — the same detection the finder uses.

    Three routes, because no single one covers every group. The userbot sees
    private groups it is a member of; the Bot API's getChat answers for public
    groups and anywhere the bot itself was added, member or not; `chats_seen`
    remembers whatever either has met before. Settings → Find Chat ID resolves
    all three details this way, so adding a group by id does too.

    (None, None) when none of them can answer. That is not an error: the
    forwarder still fills the title in the first time the group posts.
    """
    client = _userbot()
    if client is not None:
        from telethon.tl.types import PeerChannel, PeerChat
        for peer in (PeerChannel(bare_id), PeerChat(bare_id)):
            try:
                ent = await client.get_entity(peer)
            except Exception:  # noqa: BLE001
                continue
            title = getattr(ent, "title", None)
            if title:
                return title, getattr(ent, "username", None)

    from .. import chatid
    chat, _err = await chatid._get_chat(f"-100{bare_id}")
    if chat:
        await chatid.record_chat(chat, "looked up")
        return chat.get("title"), chat.get("username")

    for seen in await chatid.seen_chats():
        if str(seen.get("id", "")).lstrip("-").endswith(str(bare_id)):
            return seen.get("title"), seen.get("username")
    return None, None


async def _resolve_group(value: str) -> dict:
    """Turn whatever was typed into {id, name, username}.

    Accepts a numeric chat id, an @username, a t.me link, or the group's plain
    name — the last one by searching the userbot's own dialog list, which is
    the only place a private group's title can be looked up.
    """
    from ..chatid import parse_ref
    ref = parse_ref(value)
    kind, val = ref["kind"], ref["value"]

    if kind == "empty":
        raise HTTPException(400, "type a group name, @username, t.me link or chat id")
    if kind == "invite":
        raise HTTPException(
            400, "a private invite link cannot be resolved — open the group in "
                 "Telegram and add it by name, or paste its chat id")

    if kind == "chat_id":
        # bare_key, not string trimming: lstrip("100") strips *characters*, so
        # -1001000000123 would come out as 23.
        gid = int(fwd_counters.bare_key(val))
        # Ask Telegram for the title straight away. Waiting for the group's
        # first message left the row showing nothing but its own number, so it
        # could not be found by name in the list.
        name, username = await _title_of(gid)
        return {"id": gid, "name": name, "username": username}

    client = _userbot()
    if client is None:
        raise HTTPException(
            503, "the Telegram userbot is not connected, so a name or @username "
                 "cannot be resolved — paste the numeric chat id instead")

    if kind == "username":
        try:
            ent = await client.get_entity(val)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, f"Telegram could not find '@{val}': {exc}")
        return {"id": abs(int(ent.id)), "name": getattr(ent, "title", None) or val,
                "username": getattr(ent, "username", None)}

    # Plain name — match against the groups this account is actually in.
    needle = val.strip().lower()
    hits = []
    try:
        async for dialog in client.iter_dialogs(limit=500):
            title = str(dialog.name or "")
            if needle in title.lower():
                hits.append((dialog, title))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not read the userbot's group list: {exc}")

    exact = [h for h in hits if h[1].lower() == needle]
    if exact:
        hits = exact
    if not hits:
        raise HTTPException(404, f"no group named '{val}' in this Telegram account")
    if len(hits) > 1:
        names = ", ".join(t for _d, t in hits[:6])
        raise HTTPException(409, f"'{val}' matches several groups ({names}) — "
                                 f"use the exact name, @username or chat id")

    dialog, title = hits[0]
    return {"id": abs(int(dialog.id)), "name": title,
            "username": getattr(getattr(dialog, "entity", None), "username", None)}


@router.post("/groups")
async def add_group(payload: dict = Body(...)):
    """Add a premium group by name, @username, t.me link or chat id.

    It goes straight into `premium_groups` — the collection the forwarder
    reads — and the running userbot is told at once, so it is live without a
    restart. This used to write a 'pending' row into forwarder_sources and
    wait for a watcher to notice.
    """
    value = str(payload.get("value") or "").strip()
    resolved = await _resolve_group(value)
    gid = resolved["id"]

    col = db.get_collection("premium_groups")
    if await col.find_one({"id": gid}):
        raise HTTPException(409, f"that group is already in the list ({gid})")

    await col.insert_one({
        "id": gid,
        # A missing name is left empty on purpose: the forwarder writes the
        # real Telegram title the first time the group posts.
        "name": resolved["name"],
        "username": resolved["username"],
        "builtin": False, "enabled": True, "added_at": time.time(),
    })

    live = None
    from .. import supervisor
    fwd = supervisor.instance("fwd")
    if fwd is not None:
        try:
            live = await fwd.reload_premium_ids()
        except Exception as exc:  # noqa: BLE001
            # Not fatal — the 20s reloader picks it up — but worth saying, since
            # the response would otherwise imply it was live immediately.
            print(f"[forwarder] group {gid} added but the live reload failed: {exc}")
    return {"added": True, "id": gid, "name": resolved["name"],
            "username": resolved["username"], "live_groups": live}


@router.delete("/groups/{gid}")
async def remove_group(gid: int):
    res = await db.get_collection("premium_groups").delete_one({"id": gid})
    if not res.deleted_count:
        raise HTTPException(404, f"no premium group with id {gid}")
    from .. import supervisor
    fwd = supervisor.instance("fwd")
    if fwd is not None:
        try:
            await fwd.reload_premium_ids()
        except Exception as exc:  # noqa: BLE001
            print(f"[forwarder] group {gid} removed but the live reload failed: {exc}")
    return {"removed": True, "id": gid}


# ── Premium-caller address detections (ETH / RBH / SOL panels) ──────────────────

def _match_q(doc: dict, q: str) -> bool:
    q = q.lower()
    if q in str(doc.get("symbol", "")).lower():
        return True
    if q in str(doc.get("name", "")).lower():
        return True
    if q in str(doc.get("address", "")).lower():
        return True
    return any(q in str(g).lower() for g in (doc.get("groups") or []))


@router.get("/detections")
async def detections(
    chain: str = Query("eth", pattern="^(all|eth|rbh|sol|bnb)$"),
    q: str | None = None,
    multi: bool = False,              # "Multi 2+" filter — count >= 2 groups
    limit: int = Query(100, le=500),
):
    # "all" is the merged view: one section, one filter row. Leaving the chain
    # key out of the query is what makes it merged — every other filter still
    # applies exactly as it does to a single chain.
    flt: dict = {} if chain == "all" else {"chain": chain}
    if multi:
        flt["count"] = {"$gte": 2}
    col = db.get_collection("premium_detections")
    if not q:
        # No Python-side filter, so Mongo gives the exact count — the previous
        # code counted after slicing to `limit`, which meant the section header
        # read "100" for anything with more than a page of rows.
        total = await col.count_documents(flt)
        docs = await col.find(flt).sort("ts", -1).limit(limit).to_list(limit)
    else:
        docs = await col.find(flt).to_list(_MATCH_SCAN_CAP)
        docs.sort(key=lambda d: d.get("ts", 0), reverse=True)
        docs = [d for d in docs if _match_q(d, q)]
        total = len(docs)
        docs = docs[:limit]
    for d in docs:
        # Per row, not per request: in the merged view a Robinhood token given
        # an ETH GMGN link would open the wrong chain's page.
        d["gmgn_url"] = _gmgn_url(d.get("chain") or chain, d.get("address", ""))
    return {"total": total, "items": clean_list(docs)}


@router.get("/detections/stats")
async def detections_stats(chain: str = Query("eth", pattern="^(all|eth|rbh|sol|bnb)$")):
    col = db.get_collection("premium_detections")
    base: dict = {} if chain == "all" else {"chain": chain}
    return {
        "chain": chain,
        "total": await col.count_documents(base),
        "multi": await col.count_documents({**base, "count": {"$gte": 2}}),
    }


@router.get("/detections/dates")
async def detection_dates(chain: str = Query("eth", pattern="^(all|eth|rbh|sol|bnb)$")):
    """Days the History dropdown offers, newest first.

    Both sources, not just the archive. A day only reaches premium_archive when
    it ends, so an archive-only list could never contain today — while every
    other section derives its dates from live rows and offers today as soon as
    it has one. That is the difference the dropdowns were showing.
    """
    flt: dict = {} if chain == "all" else {"chain": chain}
    docs = await db.get_collection("premium_archive").find(flt).to_list(1200)
    days = {d.get("date") for d in docs if d.get("date")}
    live = await db.get_collection("premium_detections").find(flt).to_list(_MATCH_SCAN_CAP)
    days |= {ist_date_str(d["ts"]) for d in live if d.get("ts")}
    days.discard(None)
    # Parse before sorting. DD-MM-YYYY sorted as text puts 31-01 after 01-02,
    # so the dropdown ran out of order across every month boundary.
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}


@router.get("/detections/history")
async def detection_history(
    chain: str = Query("eth", pattern="^(all|eth|rbh|sol|bnb)$"),
    date: str = "",
    q: str | None = None,
    multi: bool = False,
):
    """One archived day's detections (same shape, and same filters, as live).

    `q` and `multi` are applied here for the same reason they exist on the live
    view: the controls stay on screen when a date is picked, so a search that
    quietly did nothing looked like a day with no matches.
    """
    # One archive doc per chain per day, so "all" merges that day's three docs
    # and re-sorts — otherwise the ETH rows would all sit above the SOL ones.
    # One archive doc per chain per day, so "all" merges that day's docs and
    # re-sorts — otherwise the ETH rows would all sit above the SOL ones.
    arch = db.get_collection("premium_archive")
    if chain == "all":
        docs = await arch.find({"date": date}).to_list(20)
    else:
        docs = await arch.find({"chain": chain, "date": date}).to_list(5)
    items = [dict(i, chain=i.get("chain") or d.get("chain"))
             for d in docs for i in (d.get("items") or [])]

    # Rows for a day that has not been archived yet — today, always — are still
    # in the live collection. Without this, picking today returned nothing.
    live_flt: dict = {} if chain == "all" else {"chain": chain}
    live = await db.get_collection("premium_detections").find(live_flt).to_list(_MATCH_SCAN_CAP)
    seen = {(i.get("chain"), str(i.get("address", "")).lower()) for i in items}
    for d in live:
        if not d.get("ts") or ist_date_str(d["ts"]) != date:
            continue
        key = (d.get("chain"), str(d.get("address", "")).lower())
        if key in seen:
            continue
        seen.add(key)
        items.append({k: v for k, v in d.items() if k != "_id"})
    items.sort(key=lambda d: d.get("ts", 0), reverse=True)
    if multi:
        items = [d for d in items if int(d.get("count") or 0) >= 2]
    if q:
        items = [d for d in items if _match_q(d, q)]
    for d in items:
        d["gmgn_url"] = _gmgn_url(d.get("chain") or chain, d.get("address", ""))
    return {"date": date, "total": len(items), "items": items}
