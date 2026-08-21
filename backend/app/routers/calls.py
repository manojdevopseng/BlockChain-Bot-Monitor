"""The Second Dashboard's API — one row per call, and the message behind it.

Two readings of the same collection:

    /api/calls          the table. One row per call, newest first.
    /api/calls/tracker  the feed. One entry per message, with its text, the
                        handle it replied to and any picture that came with it.

The difference is only how rows are grouped. A message naming a token that is
live on three chains is three table rows — the table has a chain column and the
chains are genuinely three findings — but one tracker entry, because it is one
thing somebody said.
"""

from __future__ import annotations

import time
from datetime import datetime

from fastapi import (APIRouter, Body, Depends, HTTPException, Query, Request,
                     Response)

from .. import calls, csvout, db, security
from ..util import clean_list

router = APIRouter(prefix="/api/calls", tags=["calls"])

CHAINS = "^(all|eth|rbh|sol|bnb|base)$"

# How many rows a search pass reads before counting. Only reached when `q` is
# set; the plain view counts in Mongo instead.
_SCAN_CAP = 20000

# Rows read per row shown. The table groups a token's calls together, so it has
# to see further down the feed than it displays — a token called an hour ago and
# again just now has its earlier row well below the newest hundred. Four is
# enough for a day of this feed and still one small query.
_WINDOW = 4


def _flt(chain: str, date: str | None = None) -> dict:
    f: dict = {} if chain == "all" else {"chain": chain}
    if date:
        f["day"] = date
    return f


def _match(d: dict, q: str) -> bool:
    q = q.lower()
    return any(q in str(d.get(k) or "").lower()
               for k in ("symbol", "name", "address", "group", "username"))


def _cluster(docs: list[dict], limit: int) -> list[dict]:
    """One row per token, carrying every caller — the detections panel's shape.

    That panel holds one document per token, lists the groups that called it
    newest-first, and lifts the row back to the top each time somebody new
    calls it. This builds the same thing out of the per-call rows, so the
    Second Dashboard reads the same way without the collection changing.

    Nothing is lost by folding the rows together: each caller keeps its own
    chip and its own link to its own message, so the individual calls are still
    one click away.

    One chip per group, not per post — the same rule the detections panel uses.
    A group that called the same token three times is one caller, and its chip
    opens the newest of those calls. `calls` keeps the post count for the
    tooltip, because "three posts from one group" and "three groups" are
    different things and the row should be able to say which it is.

    `docs` arrives newest-first, so each group's first row is that token's
    latest call and the tokens sort on it directly.
    """
    tokens: dict[tuple, list[dict]] = {}
    for d in docs:
        tokens.setdefault((d.get("chain"), d.get("address")), []).append(d)

    out: list[dict] = []
    for rows in sorted(tokens.values(),
                       key=lambda r: r[0].get("ts") or 0, reverse=True):
        # The newest call carries the token's details — a symbol or name that
        # was blank when it was first called may have resolved since.
        head = dict(rows[0])
        entries: list[dict] = []
        seen: set = set()
        for d in rows:
            cid = d.get("chat_id")
            if cid in seen:
                continue
            seen.add(cid)
            entries.append({
                "chat_id": cid,
                "name": d.get("group") or "",
                "username": d.get("username") or "",
                "msg_id": d.get("msg_id"),
                "post_url": d.get("post_url") or "",
                "ts": d.get("ts"),
            })
        head["group_entries"] = entries
        head["count"] = len(entries)      # callers, as the detections panel counts
        head["calls"] = len(rows)         # posts, which is not the same number
        out.append(head)
    return out[:limit]


async def _page(chain: str, q: str | None, date: str | None, limit: int):
    col = db.get_collection("premium_calls")
    flt = _flt(chain, date)
    if not q:
        total = await col.count_documents(flt)
        window = min(limit * _WINDOW, _SCAN_CAP)
        docs = await col.find(flt).sort("ts", -1).limit(window).to_list(window)
        return total, _cluster(docs, limit)
    docs = await col.find(flt).sort("ts", -1).to_list(_SCAN_CAP)
    docs = [d for d in docs if _match(d, q)]
    return len(docs), _cluster(docs, limit)


@router.get("")
async def list_calls(
    chain: str = Query("all", pattern=CHAINS),
    q: str | None = None,
    date: str | None = None,
    limit: int = Query(150, le=500),
):
    total, docs = await _page(chain, q, date, limit)
    return {"total": total, "items": clean_list(docs)}


# Premium Calls says "bnb"; the market cap reader says "bsc". One place knows
# both, and it is here rather than in the browser.
_MCAP_CHAIN = {"bnb": "bsc"}
# Base has no market cap reader, so the button is not offered for it at all
# rather than offered and always failing.
_MCAP_UNSUPPORTED = {"base"}


@router.post("/mcap")
async def read_mcap(payload: dict = Body(...),
                    owner: dict = Depends(security.require_customer)):
    """The market cap of one called token, read now and remembered on the row.

    The reading itself is not done here: it is handed to the Market Cap
    feature's own check, which owns the daily allowance, the per-chain
    switches and the reader the watcher uses. Two ways to ask the same
    question would eventually give two different answers, and the number a
    caller is judged on cannot be one of those.

    What this adds is the remembering. The figure is written onto every call
    row for that token, so it is there for the next person to open the page
    and does not cost a second lookup — a market cap checked once is checked
    for everybody.
    """
    chain = str(payload.get("chain") or "").lower()
    address = str(payload.get("address") or "").strip()
    if not address:
        raise HTTPException(400, "no token address")
    if chain in _MCAP_UNSUPPORTED:
        raise HTTPException(409, f"Market cap is not read on {chain.upper()} yet")

    from .mcap import check as mcap_check
    res = await mcap_check({"chain": _MCAP_CHAIN.get(chain, chain),
                            "address": address}, owner)

    mcap = res.get("mcap")
    if mcap:
        # Stored against the chain the call was recorded on, not the chain the
        # reader was asked about — those differ for BNB, and the rows are keyed
        # on ours.
        await db.get_collection("premium_calls").update_many(
            {"chain": chain, "address": address},
            {"$set": {"mcap": mcap, "mcap_at": res.get("checked_at"),
                      "mcap_price_usd": res.get("price_usd")}})
    return res


@router.get("/stats")
async def stats(chain: str = Query("all", pattern=CHAINS)):
    col = db.get_collection("premium_calls")
    base = _flt(chain)
    # Distinct tokens as well as calls: "412 calls on 90 tokens" says something
    # a single number cannot.
    tokens = await col.distinct("address", base)
    callers = await col.distinct("chat_id", base)
    return {
        "chain": chain,
        "calls": await col.count_documents(base),
        "tokens": len(tokens),
        "callers": len(callers),
    }


@router.get("/dates")
async def dates(chain: str = Query("all", pattern=CHAINS)):
    """Days the History dropdown offers, newest first."""
    days = await db.get_collection("premium_calls").distinct("day", _flt(chain))
    days = [d for d in days if d]
    # Parsed before sorting: DD-MM-YYYY sorted as text puts 31-01 after 01-02.
    return {"dates": sorted(days, key=lambda s: datetime.strptime(s, "%d-%m-%Y"),
                            reverse=True)}


CSV_COLUMNS = ["date", "time", "chain", "symbol", "name", "address", "group",
               "username", "keyword", "message"]


def _csv_row(d: dict) -> dict:
    when = datetime.fromtimestamp(d.get("ts") or 0)
    return {
        "date": when.strftime("%d-%m-%Y"),
        "time": when.strftime("%H:%M:%S"),
        "chain": d.get("chain", ""),
        "symbol": d.get("symbol", ""),
        "name": d.get("name", ""),
        "address": d.get("address", ""),
        "group": d.get("group", ""),
        "username": f"@{d['username']}" if d.get("username") else "",
        "keyword": d.get("keyword", ""),
        "message": (d.get("text") or "").replace(chr(10), " ")[:500],
    }


@router.get("/export.csv")
async def export_csv(chain: str = Query("all", pattern=CHAINS),
                     q: str | None = None):
    """Everything held for this section, not the day being viewed.

    No `date` parameter, deliberately: the History dropdown chooses what to
    read on screen, and the export is the whole retention window regardless.
    Chain and search still apply — those are the section's own filters, and an
    export that ignored them would not be the table you were looking at.
    """
    col = db.get_collection("premium_calls")
    flt = _flt(chain)

    async def rows():
        async for d in csvout.paged(col, flt, sort_key="ts"):
            if not q or _match(d, q):
                yield d

    stamp = datetime.now().strftime("%d-%m-%Y")
    return csvout.csv_response(rows(), CSV_COLUMNS,
                               f"premium-calls-{chain}-{stamp}.csv", row=_csv_row)


@router.get("/tracker")
async def tracker(
    chain: str = Query("all", pattern=CHAINS),
    q: str | None = None,
    only_tokens: bool = False,
    limit: int = Query(80, le=300),
):
    """Every premium message, newest first.

    Not only the ones carrying a token: the mirror group shows all of them and
    the point of this panel is to read what a caller is saying around the call,
    which includes the posts either side of it. Tokens, where a message has
    them, arrive as chips on the row a moment after the text — that is the
    chain check finishing, and it is why the text does not wait for it.

    `chain` filters to messages that resolved on that chain, which necessarily
    means messages with a token; "all" is the unfiltered feed.
    """
    col = db.get_collection("premium_messages")
    flt: dict = {}
    if chain != "all":
        flt["tokens.chain"] = chain
    elif only_tokens:
        flt["tokens.0"] = {"$exists": True}

    docs = await col.find(flt).sort("ts", -1).to_list(
        limit if not q else min(_SCAN_CAP, limit * 20))
    if q:
        needle = q.lower()
        docs = [d for d in docs
                if needle in (d.get("text") or "").lower()
                or needle in (d.get("group") or "").lower()
                or needle in (d.get("username") or "").lower()
                or any(needle in str(t.get(k) or "").lower()
                       for t in (d.get("tokens") or [])
                       for k in ("symbol", "address"))][:limit]
    items = clean_list(docs)
    await _echo_ranks(items)
    return {"total": len(items), "items": items}


# How far back "another caller said the same thing" still counts.
#
# Half an hour was the first guess and it was wrong for this feed: at the
# measured rate — roughly five calls an hour, sixty-four in twelve hours — a
# thirty-minute window caught nothing at all, while twelve hours held nine
# tokens with two or more callers and one with three. Three hours is the
# compromise: long enough to fire, short enough that it still means "now"
# rather than "at some point today".
ECHO_WINDOW = 3 * 60 * 60


async def _echo_ranks(items: list[dict]) -> None:
    """Mark each message with where its caller sits in the queue for that token.

    A token named by one group is a call. The same token named by a third group
    twelve minutes later is a different and much louder fact, and the feed had
    no way to say so — every row looked like the first.

    One query for the whole page, not one per row: the addresses on screen are
    collected first and their recent callers fetched together.
    """
    addrs = {t.get("address") for it in items for t in (it.get("tokens") or [])
             if t.get("address")}
    if not addrs:
        return
    since = time.time() - ECHO_WINDOW
    rows = await db.get_collection("premium_calls").find(
        {"address": {"$in": list(addrs)}, "ts": {"$gte": since}}
    ).to_list(_SCAN_CAP)

    # address -> callers in the order they first named it
    order: dict[str, list[int]] = {}
    for r in sorted(rows, key=lambda d: d.get("ts") or 0):
        addr, cid = r.get("address"), r.get("chat_id")
        if not addr or cid is None:
            continue
        seen = order.setdefault(addr, [])
        if cid not in seen:
            seen.append(cid)

    for it in items:
        best = None
        for tok in (it.get("tokens") or []):
            queue = order.get(tok.get("address") or "")
            if not queue or it.get("chat_id") not in queue:
                continue
            rank = queue.index(it["chat_id"]) + 1
            # The loudest thing about a message with two tokens is whichever
            # one the most people are already on.
            if best is None or rank > best[0]:
                best = (rank, len(queue))
        if best and best[0] > 1:
            it["echo_rank"], it["echo_total"] = best


@router.get("/media/{mid}")
async def media(mid: str, request: Request):
    """One stored picture, GIF or clip.

    Range requests are answered properly because a <video> needs them: without
    a 206 the browser cannot seek, and Safari will not play the file at all.
    Everything is content-addressed, so an id is always the same bytes and can
    be cached hard — the TTL decides how long it exists, not the browser.
    """
    doc = await calls.get_media(mid)
    if not doc or not doc.get("data"):
        raise HTTPException(404, "No such media")
    data: bytes = doc["data"]
    mime = doc.get("mime") or "image/jpeg"
    common = {
        "Cache-Control": "public, max-age=86400, immutable",
        "Accept-Ranges": "bytes",
    }

    rng = request.headers.get("range") or request.headers.get("Range")
    if rng and rng.startswith("bytes="):
        spec = rng[6:].split(",")[0].strip()
        start_s, _, end_s = spec.partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else len(data) - 1
        except ValueError:
            start, end = 0, len(data) - 1
        start = max(0, min(start, len(data) - 1))
        end = max(start, min(end, len(data) - 1))
        chunk = data[start:end + 1]
        return Response(
            chunk, status_code=206, media_type=mime,
            headers={**common,
                     "Content-Range": f"bytes {start}-{end}/{len(data)}",
                     "Content-Length": str(len(chunk))},
        )

    return Response(data, media_type=mime, headers=common)
