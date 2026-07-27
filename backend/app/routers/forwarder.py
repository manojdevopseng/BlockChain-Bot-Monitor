"""Telegram forwarder routes — sources, destinations, per-source toggle,
and the premium-caller address detection panels (ETH / RBH / SOL)."""

from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Query

from .. import db, fwd_counters, registry
from ..scanners import scfg
from ..util import clean_list

router = APIRouter(prefix="/api/forwarder", tags=["forwarder"])

# GMGN token-page slug per chain (for the "view on GMGN" link).
_GMGN_SLUG = {"eth": "eth", "rbh": "robinhood", "sol": "sol"}


def _gmgn_url(chain: str, address: str) -> str:
    slug = _GMGN_SLUG.get(chain, chain)
    return f"https://gmgn.ai/{slug}/token/{address}"


# The four signal channels the userbot listens to, and the registry switch that
# gates each one. Names come from .env — nothing hardcoded.
def _signal_channels() -> list[tuple[str, str, str]]:
    """(channel name, registry service id, what it feeds)"""
    return [
        (scfg.SOURCE_CALL,   "bbcanalyser2",           "first-call signals → DEST_SIGNALS"),
        (scfg.SOURCE_BUYBOT, "forwarder",              "new-group signals → DEST_SIGNALS"),
        (scfg.SOURCE_DEXS,   "dexsignalcall",          "DEX signals → DEST_DEXS"),
        (scfg.SOURCE_OTTO,   "eth_otto_group",         "Otto deployments → DEST_OTTO"),
    ]


def _destinations() -> list[tuple[str, str, str]]:
    """(env key, chat id, what gets sent there)"""
    return [
        ("DEST_OTTO",               scfg.DEST_OTTO,               "Otto method/function hash matches"),
        ("DEST_SIGNALS",            scfg.DEST_SIGNALS,            "CallAnalyser2 first-calls + BuyBotTracker"),
        ("DEST_DEXS",               scfg.DEST_DEXS,               "DEX signals from dexssignal"),
        ("DEST_PREMIUM_ETH_CALLER", scfg.DEST_PREMIUM_ETH_CALLER, "ETH addresses seen in premium groups"),
        ("DEST_PREMIUM_ALL",        scfg.DEST_PREMIUM_ALL,        "Raw mirror of every premium message"),
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
        })
    return {"items": items}


async def _backfill_names(rows: list[dict], limit: int = 5) -> None:
    """Fill in titles for groups that were added before we asked Telegram.

    A few rows predate resolving the title at add time, and a group added while
    the userbot was logged out has none either. Resolving them here is bounded
    to `limit` per request and written back, so each group costs one Telegram
    call once, ever — never on the next page load.
    """
    missing = [r for r in rows if not r.get("name") and r.get("id") is not None][:limit]
    if not missing or _userbot() is None:
        return
    col = db.get_collection("premium_groups")
    for row in missing:
        name, username = await _title_of(int(row["id"]))
        if not name:
            continue
        row["name"], row["username"] = name, username
        await col.update_one({"id": row["id"]},
                             {"$set": {"name": name, "username": username}})


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

    A premium group flips `premium_groups.enabled` — the field `_load_premium_ids`
    reads. A signal channel flips its registry service, the same switch as
    Settings → Bots, so the two can never disagree.
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
    chain: str = Query("eth", pattern="^(eth|rbh|sol)$"),
    q: str | None = None,
    multi: bool = False,              # "Multi 2+" filter — count >= 2 groups
    limit: int = Query(100, le=500),
):
    flt: dict = {"chain": chain}
    if multi:
        flt["count"] = {"$gte": 2}
    docs = await db.get_collection("premium_detections").find(flt).to_list(1000)
    docs.sort(key=lambda d: d.get("ts", 0), reverse=True)
    if q:
        docs = [d for d in docs if _match_q(d, q)]
    docs = docs[:limit]
    for d in docs:
        d["gmgn_url"] = _gmgn_url(chain, d.get("address", ""))
    return {"total": len(docs), "items": clean_list(docs)}


@router.get("/detections/stats")
async def detections_stats(chain: str = Query("eth", pattern="^(eth|rbh|sol)$")):
    col = db.get_collection("premium_detections")
    return {
        "chain": chain,
        "total": await col.count_documents({"chain": chain}),
        "multi": await col.count_documents({"chain": chain, "count": {"$gte": 2}}),
    }


@router.get("/detections/dates")
async def detection_dates(chain: str = Query("eth", pattern="^(eth|rbh|sol)$")):
    """Archived dates (History dropdown) for a chain, newest first."""
    docs = await db.get_collection("premium_archive").find({"chain": chain}).to_list(400)
    days = {d.get("date") for d in docs if d.get("date")}
    # Parse before sorting. DD-MM-YYYY sorted as text puts 31-01 after 01-02,
    # so the dropdown ran out of order across every month boundary.
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"), reverse=True)}


@router.get("/detections/history")
async def detection_history(
    chain: str = Query("eth", pattern="^(eth|rbh|sol)$"),
    date: str = "",
):
    """One archived day's detections (same shape as live)."""
    doc = await db.get_collection("premium_archive").find_one({"chain": chain, "date": date})
    items = (doc or {}).get("items", [])
    for d in items:
        d["gmgn_url"] = _gmgn_url(chain, d.get("address", ""))
    return {"date": date, "total": len(items), "items": items}
