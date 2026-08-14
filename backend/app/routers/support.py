"""Support: reporting a problem, and answering one.

Two audiences on one collection. A customer sees their own tickets and nothing
else — the username is in every query, not in a filter afterwards. An operator
sees the queue, and the queue is sorted by what actually matters first: money
and access before a wrong number on a chart.

Reporting needs a login and nothing more. An account whose subscription ended
because a payment did not land is exactly the account that needs to say so.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import security, tickets

router = APIRouter(prefix="/api/support", tags=["support"])


@router.get("/problems")
async def problems(doc: dict = Depends(security.require_user)):
    """The checkboxes, grouped as the form draws them."""
    return {"items": tickets.catalogue()}


@router.post("/tickets")
async def create(payload: dict = Body(...),
                 doc: dict = Depends(security.account)):
    try:
        row = await tickets.create(
            doc,
            problems=[str(p) for p in (payload.get("problems") or [])],
            message=str(payload.get("message") or ""),
            page=str(payload.get("page") or ""),
            token=str(payload.get("token") or ""),
            order=str(payload.get("order") or ""),
            client=payload.get("client") if isinstance(payload.get("client"), dict)
            else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {k: v for k, v in row.items() if k != "_id"}


@router.get("/tickets")
async def my_tickets(doc: dict = Depends(security.account)):
    return {"items": await tickets.mine(doc["username"])}


@router.get("/tickets/{ticket_id}")
async def one(ticket_id: str, doc: dict = Depends(security.account)):
    row = await tickets.get(ticket_id, doc["username"])
    if row is None:
        raise HTTPException(404, "No such request")
    return row


@router.post("/tickets/{ticket_id}/reply")
async def add_reply(ticket_id: str, payload: dict = Body(...),
                    doc: dict = Depends(security.account)):
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write something first")
    row = await tickets.reply(ticket_id, doc["username"], text, is_admin=False)
    if row is None:
        raise HTTPException(404, "No such request")
    return row


# ── the operator's side ──────────────────────────────────────────────────────

@router.get("/queue")
async def queue(status: str | None = None,
                claims: dict = Depends(security.require_admin)):
    return {"items": await tickets.queue(status)}


@router.post("/queue/{ticket_id}/reply")
async def admin_reply(ticket_id: str, payload: dict = Body(...),
                      claims: dict = Depends(security.require_admin)):
    """Answer, and move the ticket. Answering alone marks it in progress."""
    text = str(payload.get("text") or "").strip()
    status = payload.get("status")
    if not text and not status:
        raise HTTPException(400, "Nothing to say and nothing to change")
    row = await tickets.reply(ticket_id, claims["username"], text or "…",
                              is_admin=True,
                              status=str(status) if status else None)
    if row is None:
        raise HTTPException(404, "No such request")
    return row
