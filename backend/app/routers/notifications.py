"""The bell: what has happened to this account, and what it has not read."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from .. import notifications, security

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def list_notices(doc: dict = Depends(security.account)):
    """Both halves in one answer: the bell needs a count, the panel needs rows,
    and two requests for one dropdown is a request too many."""
    user = doc["username"]
    return {"items": await notifications.recent(user),
            "unread": await notifications.unread(user)}


@router.post("/read")
async def read(payload: dict = Body(default={}),
               doc: dict = Depends(security.account)):
    before = payload.get("before")
    count = await notifications.mark_read(doc["username"],
                                          float(before) if before else None)
    return {"read": count}
