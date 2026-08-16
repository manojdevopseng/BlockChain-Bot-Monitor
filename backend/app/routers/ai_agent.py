"""Read-only view of what the AI narrative agent decided, and why.

Every judgement is served, not just the matches — a filter is only trustable if
you can see what it threw away.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import accounts, ai_agent, db, pump_mcap, security

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/factcheck")
async def factcheck(address: str = Query(..., min_length=32, max_length=64),
                    force: bool = False,
                    owner: dict = Depends(security.require_customer)):
    """Is the post behind this launch real? One token, on a click.

    A POST because it spends a model call. The answer is stored on the
    decision, so asking again is free unless `force` says to ask afresh.

    Which is also why it has a daily allowance: this is the one button on the
    read-only side of the app that costs money every time it is pressed. A
    cached answer is not charged — only a real model call is.
    """
    plan = accounts.plan_of(owner)
    used = await accounts.checks_today(owner["username"], "ai")
    if used >= plan.ai_checks_per_day:
        raise HTTPException(
            402, f"That is {used} fact-checks today — the {plan.label} plan "
                 f"allows {plan.ai_checks_per_day} a day.")
    result = await ai_agent.fact_check(address, force=force)
    # Only a fresh, successful call is counted. Reading back an answer already
    # stored on the decision costs nothing, and neither does a model that could
    # not be reached — charging for either is charging for nothing.
    if (result or {}).get("ok") and not (result or {}).get("cached"):
        used = await accounts.note_check(owner["username"], "ai")
    return {**(result or {}), "checks_today": used,
            "checks_allowed": plan.ai_checks_per_day}


@router.get("/mcap")
async def mcap(address: str = Query(..., min_length=32, max_length=64)):
    """Current and all-time-high market cap for one token, asked for by hand.

    The live watch freezes at the moment a launch crosses the bar, because that
    is the figure that goes out in the message and it must not move afterwards.
    This is the other question — how far it went in the end — and it is asked
    one token at a time rather than measured for every launch.
    """
    out = await pump_mcap.lookup(address)
    # What we recorded ourselves, when we have it, so the two can be compared
    # side by side rather than taken on trust.
    row = await db.get_collection("x_links").find_one(
        {"address": address},
        {"peak_mcap_usd": 1, "symbol": 1, "open_timestamp": 1, "link": 1}) or {}
    out["our_peak_usd"] = row.get("peak_mcap_usd")
    out["our_watch_seconds"] = pump_mcap.watch_seconds()
    if not out.get("symbol") and row.get("symbol"):
        out["symbol"] = row["symbol"]
    return out


@router.get("/stats")
async def stats():
    return await ai_agent.stats()


@router.get("/decisions")
async def decisions(
    limit: int = Query(200, ge=1, le=5000),
    verdict: str | None = Query(None, pattern="^(matched|launching|rejected|skipped|pending|error|telegram)$"),
    q: str | None = None,
    min_followers: int = Query(0, ge=0),
    date: str | None = None,          # DD-MM-YYYY (IST) — History filter
):
    """Decisions, newest first. Every filter is applied by the query."""
    return await ai_agent.recent(limit=limit, verdict=verdict, q=q,
                                 min_followers=min_followers, day=date)


@router.get("/decision-dates")
async def decision_dates():
    """IST days that have decisions, newest first."""
    return {"dates": await ai_agent.decision_dates()}


@router.get("/xdates")
async def xdates(og: bool = False):
    """IST days with rows, newest first — the History dropdown."""
    return {"dates": await ai_agent.x_link_dates(og_only=og)}


@router.get("/og")
async def og(
    limit: int = Query(200, ge=1, le=5000),
    q: str | None = None,
    date: str | None = None,
):
    """The originals: the first launch of a name that then came back to the cap.

    A name relaunched five times in a day is somebody working at it, and the one
    worth looking at is the one that came first — before the copies.
    """
    return await ai_agent.x_links(limit=limit, q=q, day=date, og_only=True)


@router.get("/xcheck")
async def xcheck(
    limit: int = Query(200, ge=1, le=5000),
    q: str | None = None,
    min_followers: int = Query(0, ge=0),
    date: str | None = None,          # DD-MM-YYYY (IST) — History filter
):
    """What the X feed loop has found, newest first.

    Served straight from Mongo: the reading happens on a background loop that
    broadcasts each new token over the WebSocket, so this is only what the page
    needs on first paint or after a reconnect — never an upstream call.
    """
    return await ai_agent.x_links(limit=limit, q=q,
                                  min_followers=min_followers, day=date)


@router.get("/drops")
async def drops(hours: int = Query(24, ge=1, le=168)):
    """What the filters threw away, by reason and hour.

    Most drops are the filter working — thousands a day with no X link or an
    unverified account — so they are counted, not kept one by one. A launch
    dropped because X would not answer keeps its mint, since that is the kind
    worth chasing.
    """
    return {"items": await ai_agent.drops(hours=hours)}


@router.get("/feed-audit")
async def feed_audit(hours: int = Query(3, ge=1, le=48)):
    """Does the feed's arithmetic close? received = stored + dropped, or not."""
    return {"items": await ai_agent.feed_audit(hours=hours)}


@router.get("/why")
async def why(address: str = Query(..., min_length=32, max_length=64)):
    """Why is this launch not in X Links? One query, one answer.

    Answers from the drop record if the launch was filtered, and says so
    plainly if it was never seen at all — which is a different problem and
    used to look identical.
    """
    row = await db.get_collection("x_links").find_one(
        {"address": address},
        {"symbol": 1, "link": 1, "handle": 1, "open_timestamp": 1, "judged": 1})
    if row:
        row.pop("_id", None)
        return {"address": address, "state": "stored", **row}

    dropped = await ai_agent.why_dropped(address)
    if dropped:
        return {"address": address, "state": "dropped", **dropped}
    return {"address": address, "state": "never seen",
            "note": "the feed never handled this launch — it was not filtered, "
                    "it did not arrive"}
