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
    limit: int = Query(100, le=500),
    verdict: str | None = Query(None, pattern="^(matched|launching|rejected|skipped|error)$"),
    q: str | None = None,
):
    items = await ai_agent.recent(limit=limit, verdict=verdict)
    if q:
        needle = q.lower()
        items = [
            d for d in items
            if needle in f"{d.get('symbol','')} {d.get('name','')} "
                         f"{d.get('address','')} {d.get('handle','')} "
                         f"{d.get('narrative','')}".lower()
        ]
    return {"total": len(items), "items": items}


@router.get("/xcheck")
async def xcheck(limit: int = Query(40, ge=1, le=200)):
    """What the X feed loop has found, newest first.

    Served straight from Mongo: the reading happens on a background loop that
    broadcasts each new token over the WebSocket, so this is only what the page
    needs on first paint or after a reconnect — never an upstream call.
    """
    return await ai_agent.x_links(limit=limit)


@router.get("/watching")
async def watching():
    """Profiles reported as Launching, still being re-checked for a contract."""
    rows = await ai_agent.watching()
    return {"total": len(rows), "items": rows}
