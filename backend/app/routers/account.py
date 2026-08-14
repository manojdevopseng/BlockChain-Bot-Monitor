"""Sign-up, email confirmation, password reset, and the account's own page.

Open routes and owner routes, and nothing in between: registering and resetting
cannot require a login, everything else is about the account already logged in.
Admin-facing account management stays where it is, in routers/users.py.

Nothing here tells a stranger whether an address has an account — reset always
answers the same way — and nothing here can extend a trial: `start_trial` is
idempotent and the confirmation link is what spends it.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import accounts, mailer, security

router = APIRouter(prefix="/api/account", tags=["account"])


@router.post("/register")
async def register(payload: dict = Body(...)):
    username = str(payload.get("username") or "").strip()
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    problem = accounts.validate_signup(username, email, password)
    if problem:
        raise HTTPException(400, problem)
    try:
        doc = await accounts.register(username, email, password)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    await mailer.send_verification(doc["email"], doc["username"],
                                   doc["verify_token"])
    return {"username": doc["username"], "email": doc["email"],
            "verification_sent": True,
            "message": "Check your email to confirm the address — your "
                       f"{accounts.TRIAL_DAYS}-day trial starts when you do."}


@router.post("/verify")
async def verify(payload: dict = Body(...)):
    doc = await accounts.verify_email(str(payload.get("token") or ""))
    if doc is None:
        raise HTTPException(400, "That confirmation link is not valid — it may "
                                 "already have been used")
    return {"verified": True, "account": accounts.public(doc)}


@router.post("/resend")
async def resend(payload: dict = Body(...)):
    """A new confirmation link. Answers the same whatever the address is."""
    doc = await accounts.by_email(str(payload.get("email") or ""))
    if doc and not doc.get("email_verified") and doc.get("verify_token"):
        await mailer.send_verification(doc["email"], doc["username"],
                                       doc["verify_token"])
    return {"sent": True}


@router.post("/forgot")
async def forgot(payload: dict = Body(...)):
    got = await accounts.begin_reset(str(payload.get("email") or ""))
    if got:
        doc, token = got
        await mailer.send_reset(doc["email"], doc["username"], token)
    # Same answer either way: which addresses have accounts is not a fact this
    # endpoint hands out.
    return {"sent": True,
            "message": "If that address has an account, a reset link is on its "
                       "way."}


@router.post("/reset")
async def reset(payload: dict = Body(...)):
    try:
        doc = await accounts.finish_reset(str(payload.get("token") or ""),
                                          str(payload.get("password") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if doc is None:
        raise HTTPException(400, "That reset link has expired — ask for a new one")
    return {"reset": True}


# ── the logged-in account ────────────────────────────────────────────────────

@router.get("/me")
async def me(doc: dict = Depends(security.account)):
    """Everything the Profile page and the paywall need, in one answer."""
    state = accounts.access(doc)
    plan = accounts.plan_of(doc)
    return {
        **accounts.public(doc),
        "usable": state.usable,
        "reason": state.reason,
        "usage": {
            "rsi_tokens": await accounts.count_owned("rsi_tokens",
                                                     doc.get("username", "")),
            "mcap_tokens": await accounts.count_owned("mcap_tokens",
                                                      doc.get("username", "")),
            "mcap_checks_today": await accounts.checks_today(
                doc.get("username", "")),
        },
        "plans": [
            {"id": p.id, "label": p.label, "price_usd": p.price_usd,
             "days": p.days, "note": p.note,
             "rsi_tokens": p.rsi_tokens, "mcap_tokens": p.mcap_tokens,
             "mcap_checks_per_day": p.mcap_checks_per_day,
             "min_cadence": p.min_cadence, "min_interval": p.min_interval,
             "telegram_alerts": p.telegram_alerts,
             "current": p.id == plan.id}
            for p in accounts.PLANS.values()
        ],
    }


@router.patch("/me")
async def update_me(payload: dict = Body(...),
                    doc: dict = Depends(security.account)):
    """The few things an account may change about itself."""
    from .. import db, users
    update: dict = {}
    if "password" in payload:
        old = str(payload.get("current_password") or "")
        if not users.check_password(old, doc.get("password", "")):
            raise HTTPException(403, "Your current password does not match")
        problem = users.validate(doc["username"], str(payload["password"]))
        if problem:
            raise HTTPException(400, problem)
        update["password"] = users.hash_password(str(payload["password"]))
    if "email" in payload:
        email = str(payload["email"]).strip().lower()
        if not accounts.EMAIL_RE.match(email):
            raise HTTPException(400, "That does not look like an email address")
        if await accounts.by_email(email) and email != doc.get("email"):
            raise HTTPException(409, "Another account already uses that address")
        # A changed address is unconfirmed until it answers, the same as a new
        # one — otherwise the reset link could be pointed anywhere.
        import secrets
        token = secrets.token_urlsafe(32)
        update.update({"email": email, "email_verified": False,
                       "verify_token": token})
        await mailer.send_verification(email, doc["username"], token)
    if not update:
        raise HTTPException(400, "nothing to change")
    await db.get_collection("users").update_one({"username": doc["username"]},
                                                {"$set": update})
    return {"updated": sorted(update)}
