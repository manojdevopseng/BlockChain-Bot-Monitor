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

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response

from .. import calls, csvout, db
from ..util import clean_list

router = APIRouter(prefix="/api/calls", tags=["calls"])

CHAINS = "^(all|eth|rbh|sol|bnb|base)$"

# How many rows a search pass reads before counting. Only reached when `q` is
# set; the plain view counts in Mongo instead.
_SCAN_CAP = 20000


def _flt(chain: str, date: str | None = None) -> dict:
    f: dict = {} if chain == "all" else {"chain": chain}
    if date:
        f["day"] = date
    return f


def _match(d: dict, q: str) -> bool:
    q = q.lower()
    return any(q in str(d.get(k) or "").lower()
               for k in ("symbol", "name", "address", "group", "username"))


async def _page(chain: str, q: str | None, date: str | None, limit: int):
    col = db.get_collection("premium_calls")
    flt = _flt(chain, date)
    if not q:
        total = await col.count_documents(flt)
        docs = await col.find(flt).sort("ts", -1).limit(limit).to_list(limit)
        return total, docs
    docs = await col.find(flt).sort("ts", -1).to_list(_SCAN_CAP)
    docs = [d for d in docs if _match(d, q)]
    return len(docs), docs[:limit]


@router.get("")
async def list_calls(
    chain: str = Query("all", pattern=CHAINS),
    q: str | None = None,
    date: str | None = None,
    limit: int = Query(150, le=500),
):
    total, docs = await _page(chain, q, date, limit)
    return {"total": total, "items": clean_list(docs)}


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
    return {"total": len(docs), "items": clean_list(docs)}


@router.get("/media/{mid}")
async def media(mid: str):
    doc = await calls.get_media(mid)
    if not doc or not doc.get("data"):
        raise HTTPException(404, "No such image")
    return Response(
        doc["data"], media_type=doc.get("mime") or "image/jpeg",
        # Content-addressed, so a given id is always the same bytes and can be
        # cached hard. The TTL decides how long it exists, not the browser.
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
