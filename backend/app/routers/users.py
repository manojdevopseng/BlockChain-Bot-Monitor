"""User Management — accounts an admin creates, and nothing else.

Every route here is behind `require_admin`, including the reads: the list of
who can log in is not something a read-only account needs. There is no sign-up
endpoint, deliberately — an account exists because an admin made it.

Passwords go in and never come out. A reset replaces one; nothing here can
return the original, to an admin or to anyone.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import security, users

router = APIRouter(prefix="/api/users", tags=["users"],
                   dependencies=[Depends(security.require_admin)])


@router.get("")
async def list_users():
    return {"items": await users.listing(),
            "min_password": users.MIN_PASSWORD}


@router.post("")
async def create_user(payload: dict = Body(...),
                      claims: dict = Depends(security.require_admin)):
    """Create a login. The admin chooses the password; it is hashed here."""
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if (why := users.validate(username, password)):
        raise HTTPException(400, why)
    if username.lower() == security.settings.admin_username.lower():
        raise HTTPException(400, "that is the admin username")
    try:
        return await users.create(username, password, claims["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.patch("/{username}")
async def update_user(username: str, payload: dict = Body(...)):
    """Change a password, or switch an account off without deleting it."""
    if "password" in payload:
        password = str(payload.get("password") or "")
        if (why := users.validate(username, password)):
            raise HTTPException(400, why)
        if not await users.set_password(username, password):
            raise HTTPException(404, f"no user '{username}'")
        return {"username": username, "updated": "password"}

    if "enabled" in payload:
        # Off keeps the account and refuses the login, which is what you want
        # for somebody who has left but might come back.
        if not await users.set_enabled(username, bool(payload["enabled"])):
            raise HTTPException(404, f"no user '{username}'")
        return {"username": username, "enabled": bool(payload["enabled"])}

    raise HTTPException(400, "body must include 'password' or 'enabled'")


@router.delete("/{username}")
async def delete_user(username: str):
    if not await users.delete(username):
        raise HTTPException(404, f"no user '{username}'")
    return {"username": username, "deleted": True}
