"""The operator's desk: accounts, money, and everything waiting on a person.

One rule for the whole router — `require_admin` — because everything here is
somebody else's data. What it exists to do is stop the answer to "did that
payment land?" being a Mongo shell.

The verbs are deliberately few. An operator needs to see the queue, grant time,
settle a payment that arrived as a round number, and suspend an account that is
abusing the thing. Everything else the product already does for itself.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import accounts, db, orders, security, tickets

router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(security.require_admin)])


@router.get("/overview")
async def overview():
    """The five numbers worth a glance, and what is waiting on you."""
    users = db.get_collection("users")
    now = time.time()
    rows = await users.find({}, {"_id": 0, "role": 1, "plan": 1,
                                 "plan_ends_at": 1, "blocked": 1,
                                 "email_verified": 1}).to_list(5000)
    counted = {"total": 0, "trialing": 0, "active": 0, "expired": 0,
               "unverified": 0, "blocked": 0}
    for row in rows:
        if row.get("role") == accounts.ADMIN:
            continue
        counted["total"] += 1
        counted[accounts.access(row).status] = \
            counted.get(accounts.access(row).status, 0) + 1
    return {
        "accounts": counted,
        "waiting": {
            "tickets": await db.get_collection("tickets").count_documents(
                {"status": {"$in": [tickets.OPEN, tickets.WORKING]}}),
            "orders": await db.get_collection("orders").count_documents(
                {"status": orders.OPEN}),
            "unmatched": await db.get_collection(
                "payments_unmatched").count_documents({"settled": False}),
            "contacts": await db.get_collection(
                "contact_messages").count_documents({"handled": False}),
        },
        "revenue": {
            "paid_orders": await db.get_collection("orders").count_documents(
                {"status": orders.ACTIVATED}),
            "usd_30d": round(sum(
                float(o.get("price_usd") or 0)
                for o in await db.get_collection("orders").find(
                    {"status": orders.ACTIVATED,
                     "activated_at": {"$gte": now - 30 * 86400}},
                    {"_id": 0, "price_usd": 1}).to_list(1000)), 2),
        },
    }


# ── accounts ─────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(q: str | None = None, status: str | None = None,
                     limit: int = Query(200, le=1000)):
    rows = await db.get_collection("users").find({}).sort("created_at", -1) \
                   .to_list(limit)
    out = []
    for row in rows:
        state = accounts.access(row)
        if status and state.status != status:
            continue
        if q and q.lower() not in f"{row.get('username','')} {row.get('email','')}".lower():
            continue
        out.append({
            **accounts.public(row),
            "comped": bool(row.get("comped")),
            "blocked": bool(row.get("blocked")),
            "blocked_reason": row.get("blocked_reason", ""),
            "usage": {
                "rsi_tokens": await accounts.count_owned("rsi_tokens",
                                                         row.get("username", "")),
                "mcap_tokens": await accounts.count_owned("mcap_tokens",
                                                          row.get("username", "")),
            },
        })
    return {"items": out}


@router.patch("/users/{username}")
async def edit_user(username: str, payload: dict = Body(...),
                    claims: dict = Depends(security.require_admin)):
    """Grant days, suspend, or lift a suspension. Every change is written down.

    `grant_days` is the one that matters: a payment that arrived as a round
    number, an apology, a friend. It extends from whichever is later — now or
    the current expiry — exactly like paying does, so a grant never shortens
    anything.
    """
    users = db.get_collection("users")
    doc = await users.find_one({"username": username})
    if doc is None:
        raise HTTPException(404, "No such account")

    changed: dict = {}
    cleared: dict = {}
    # The second way in. The first is the link we email; when mail is not
    # configured, or the message never arrived, an unconfirmed account is stuck
    # behind a 402 on every route it has — including Connect Telegram. This is
    # the operator saying "I know who this is".
    if "email_verified" in payload:
        changed["email_verified"] = bool(payload["email_verified"])
        if changed["email_verified"]:
            # Same two steps the emailed link takes: the token is spent, and
            # the trial starts here rather than at sign-up. Without the trial a
            # freshly verified account reads as `expired`, which is not what
            # verifying somebody is meant to do.
            cleared["verify_token"] = ""
        changed["verified_by"] = claims["username"]
    if "grant_days" in payload:
        days = float(payload["grant_days"])
        base = max(time.time(), float(doc.get("plan_ends_at") or 0))
        changed["plan_ends_at"] = base + days * 86400
        # A granted account is on a plan, not on a trial that never ends.
        if str(doc.get("plan")) == "trial" and days > 0:
            changed["plan"] = str(payload.get("plan") or "monthly")
        changed["comped"] = True
        changed["comped_reason"] = str(payload.get("reason") or
                                       f"{days:g} days granted by {claims['username']}")
    if "plan" in payload and "grant_days" not in payload:
        if payload["plan"] not in accounts.PLANS:
            raise HTTPException(400, "Unknown plan")
        changed["plan"] = payload["plan"]
    if "unlimited" in payload:
        # The ceilings, not the keys. role is untouched on purpose: it is what
        # every admin gate reads, and this must not open one of them.
        changed["unlimited"] = bool(payload["unlimited"])
        changed["unlimited_by"] = claims["username"] if payload["unlimited"] else ""
    if "blocked" in payload:
        changed["blocked"] = bool(payload["blocked"])
        changed["blocked_reason"] = str(payload.get("reason") or
                                        "suspended by an administrator")
    if not changed:
        raise HTTPException(400, "nothing to change")

    update: dict = {"$set": changed}
    if cleared:
        update["$unset"] = cleared
    await users.update_one({"username": username}, update)
    if changed.get("email_verified"):
        # Idempotent, and it will not extend an account that already had its
        # trial — see accounts.start_trial.
        await accounts.start_trial(username)
    await db.get_collection("admin_audit").insert_one(
        {"at": time.time(), "by": claims["username"], "user": username,
         "changed": {k: v for k, v in changed.items() if k != "password"}})
    return accounts.public(await users.find_one({"username": username}))


# ── money ────────────────────────────────────────────────────────────────────

@router.get("/orders")
async def list_orders(status: str | None = None, limit: int = Query(200, le=1000)):
    flt = {"status": status} if status else {}
    rows = await db.get_collection("orders").find(flt, {"_id": 0}) \
                   .sort("created_at", -1).to_list(limit)
    return {"items": rows}


@router.get("/unmatched")
async def unmatched():
    """Money that arrived matching no quoted figure — almost always a round
    number typed instead of the exact one."""
    rows = await db.get_collection("payments_unmatched").find(
        {"settled": False}, {"_id": 0}).sort("at", -1).to_list(100)
    return {"items": rows}


@router.post("/orders/{order_id}/settle")
async def settle_by_hand(order_id: str, payload: dict = Body(default={}),
                         claims: dict = Depends(security.require_admin)):
    """Settle an order the watcher could not match.

    The same path a matched payment takes — the plan is applied, the buyer is
    emailed, the order says activated — so a hand-settled order is not a second
    kind of order that behaves differently later.

    `method` says how the money really arrived: cash in a hand, crypto the
    watcher could not match, or something else. It is stored on the order
    rather than inferred, because "paid" and "paid in cash" answer different
    questions later.
    """
    row = await orders.get(order_id)
    if row is None:
        raise HTTPException(404, "No such order")
    if row.get("status") == orders.ACTIVATED:
        raise HTTPException(409, "That order is already activated")
    method = str(payload.get("method") or orders.VIA_OTHER).lower()
    if method not in orders.METHODS:
        raise HTTPException(400, f"method must be one of {', '.join(orders.METHODS)}")
    seen = float(payload.get("amount") or row.get("amount") or 0)
    doc = await orders.settle(row, seen, method=method, by=claims["username"])
    if doc is None:
        raise HTTPException(500, "The account behind that order is gone")
    await db.get_collection("admin_audit").insert_one(
        {"at": time.time(), "by": claims["username"], "order": order_id,
         "changed": {"settled_by_hand": seen, "method": method}})
    # Whatever unmatched payment prompted this is no longer waiting.
    await db.get_collection("payments_unmatched").update_many(
        {"asset_id": row.get("asset_id"), "settled": False},
        {"$set": {"settled": True, "settled_order": order_id}})
    return orders.public(await orders.get(order_id))


# ── the front door ───────────────────────────────────────────────────────────

@router.get("/contacts")
async def contacts(handled: bool = False, limit: int = Query(100, le=500)):
    rows = await db.get_collection("contact_messages").find(
        {"handled": handled}, {"_id": 0}).sort("at", -1).to_list(limit)
    return {"items": rows}


@router.post("/contacts/{index}/handled")
async def mark_handled(index: str, payload: dict = Body(default={})):
    """Marked by email + timestamp, because a contact message has no id."""
    email = str(payload.get("email") or "")
    at = float(payload.get("at") or 0)
    res = await db.get_collection("contact_messages").update_one(
        {"email": email, "at": at}, {"$set": {"handled": True}})
    if not res.matched_count:
        raise HTTPException(404, "No such message")
    return {"handled": True}
