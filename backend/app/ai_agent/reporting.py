"""Read-only queries the dashboard calls. Nothing here changes state."""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Optional

from ..config import settings
from .common import _LINKED_KINDS, _col
from .notify import _gmgn


async def why_dropped(mint: str) -> Optional[dict]:
    """The reason a launch never became a row, if it was recorded as dropped."""
    doc = await _col("x_drops").find_one({"mints.mint": mint},
                                         {"reason": 1, "hour": 1, "mints": 1})
    if not doc:
        return None
    entry = next((m for m in doc.get("mints", []) if m.get("mint") == mint), {})
    return {"reason": doc.get("reason"), "hour": doc.get("hour"), **entry}


async def feed_audit(hours: int = 3) -> list[dict]:
    """Per hour: delivered by the socket, stored, dropped, and unexplained.

    `_received` is counted the moment a launch arrives, before any filter can
    touch it, so `received - stored - dropped` is the number of launches that
    reached this process and then went nowhere. If that is zero, anything
    missing was never delivered.
    """
    out: list[dict] = []
    rows = await _col("x_drops").find({}).sort("hour", -1).limit(hours * 10).to_list(200)
    by_hour: dict[str, dict] = {}
    for r in rows:
        h = by_hour.setdefault(r["hour"], {"hour": r["hour"], "received": 0,
                                           "dropped": 0, "reasons": {}})
        if r["reason"] == "_received":
            h["received"] = r["count"]
        else:
            h["dropped"] += r["count"]
            h["reasons"][r["reason"]] = r["count"]

    for hour, h in sorted(by_hour.items(), reverse=True)[:hours]:
        # The bucket label is local time; x_links is stamped in epoch seconds.
        start = time.mktime(time.strptime(hour, "%d-%m-%Y %H:00"))
        h["stored"] = await _col("x_links").count_documents(
            {"found_at": {"$gte": start, "$lt": start + 3600}})
        h["unexplained"] = h["received"] - h["stored"] - h["dropped"]
        out.append(h)
    return out


async def drops(hours: int = 24) -> list[dict]:
    """Drop counts by reason, newest hour first — the audit for what was filtered.

    `_received` shares this collection but is not a drop — it is the arrival
    count the audit measures everything else against, so it is left out here.
    """
    rows = await _col("x_drops").find(
        {"reason": {"$ne": "_received"}}
    ).sort("hour", -1).limit(hours * 8).to_list(200)
    for r in rows:
        r.pop("_id", None)
        r.pop("dt", None)
        r.pop("mints", None)      # the audit trail, not something to render
    return rows


async def x_link_dates(og_only: bool = False) -> list[str]:
    """IST days that have rows, newest first — the History dropdown."""
    days = await _col("x_links").distinct("day", {"og": True} if og_only else {})
    days = [d for d in days if d]
    return sorted(days, key=lambda x: datetime.strptime(x, "%d-%m-%Y"), reverse=True)


async def x_links(limit: int = 40, q: str | None = None,
                  min_followers: int = 0, day: str | None = None,
                  og_only: bool = False) -> dict:
    """Tokens with an X link, newest first. Read from Mongo — no upstream call."""
    # Sorted by Mongo, not in Python. Reading a fixed slice and sorting that
    # returns the newest of the OLDEST documents — which is what this did once
    # the collection outgrew the slice, so the section froze on rows two hours
    # old while fresh ones were being written the whole time.
    # The live view is the verified, linked launches. The OG view is a burst's
    # original, which may have had neither — so it filters on the flag alone.
    flt: dict[str, Any] = ({"og": True} if og_only
                           else {"kind": {"$in": list(_LINKED_KINDS)}, "verified": True})
    if day:
        flt["day"] = day
    if min_followers > 0:
        flt["followers"] = {"$gte": min_followers}
    if q:
        # Address, @handle, or any word in the post text / name / ticker.
        rx = {"$regex": re.escape(q.lstrip("@")), "$options": "i"}
        flt["$or"] = [{f: rx} for f in
                      ("address", "handle", "excerpt", "symbol", "name", "link")]

    # Counted with the same filter, before the limit. `total` was len(rows),
    # so a section holding two thousand launches reported forty — the page size,
    # dressed up as the total.
    total = await _col("x_links").count_documents(flt)
    rows = await _col("x_links").find(flt).sort(
        "found_at", -1).limit(limit).to_list(limit)
    for r in rows:
        r.pop("_id", None)
        r.pop("dt", None)
    return {
        "at": time.time(),
        "newest_age_minutes": (round((time.time() - rows[0]["open_timestamp"]) / 60, 1)
                               if rows and rows[0].get("open_timestamp") else None),
        "total": total,
        "shown": len(rows),
        "resolved": sum(1 for r in rows if r.get("resolved")),
        "verified": sum(1 for r in rows if r.get("verified")),
        "posts": sum(1 for r in rows if r.get("post_found")),
        "items": rows,
    }


async def decision_dates() -> list[str]:
    """IST days that have decisions, newest first — the History dropdown."""
    days = [d for d in await _col("ai_decisions").distinct("day") if d]
    return sorted(days, key=lambda x: datetime.strptime(x, "%d-%m-%Y"), reverse=True)


async def recent(limit: int = 200, verdict: Optional[str] = None,
                 q: Optional[str] = None, min_followers: int = 0,
                 day: Optional[str] = None) -> dict:
    """Decisions newest first, with the count of everything the filter matches.

    Every filter is applied by the query. Searching a page of results and
    calling that a search means the answer changes with the page size, which is
    not a search anyone can trust.
    """
    flt: dict[str, Any] = {}
    # Telegram is not a verdict — a launch can be pending or matched AND have
    # cleared the market cap bar — so it filters on its own flag.
    if verdict == "telegram":
        flt["telegram"] = True
    elif verdict:
        flt["verdict"] = verdict
    if day:
        flt["day"] = day
    if min_followers > 0:
        flt["followers"] = {"$gte": min_followers}
    if q:
        rx = {"$regex": re.escape(q.lstrip("@")), "$options": "i"}
        flt["$or"] = [{f: rx} for f in
                      ("address", "handle", "symbol", "name", "narrative", "reason")]
    total = await _col("ai_decisions").count_documents(flt)
    docs = await _col("ai_decisions").find(flt).sort("at", -1).limit(limit).to_list(limit)
    out: list[dict] = []
    for d in docs:
        d.pop("_id", None)
        d["gmgn_url"] = _gmgn(d.get("address", ""))
        out.append(d)
    return {"total": total, "shown": len(out), "items": out}


async def stats() -> dict:
    col = _col("ai_decisions")
    counts = {v: await col.count_documents({"verdict": v})
              for v in ("matched", "launching", "rejected", "skipped",
                        "pending", "error")}
    return {
        "enabled": bool(settings.xai_api_key),
        "dry_run": settings.ai_dry_run,
        "model": settings.xai_model,
        # Two models now: the fast one reads narratives on every launch, the
        # reasoning one answers Fact check on a click.
        "fact_model": settings.xai_fact_model or settings.xai_model,
        "telegram": await col.count_documents({"telegram": True}),
        "total": await col.count_documents({}),
        **counts,
    }
