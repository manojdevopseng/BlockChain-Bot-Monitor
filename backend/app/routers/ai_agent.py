"""Read-only view of what the AI narrative agent decided, and why.

Every judgement is served, not just the matches — a filter is only trustable if
you can see what it threw away.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import ai_agent

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/stats")
async def stats():
    return await ai_agent.stats()


@router.get("/decisions")
async def decisions(
    limit: int = Query(200, ge=1, le=5000),
    verdict: str | None = Query(None, pattern="^(matched|launching|rejected|skipped|error)$"),
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
