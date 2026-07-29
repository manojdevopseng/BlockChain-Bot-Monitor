"""Login / auth routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from .. import security

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    role = security.authenticate(form.username, form.password)
    if not role:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return TokenOut(access_token=security.create_token(form.username, role),
                    username=form.username, role=role)


@router.get("/me")
async def me(claims: dict = Depends(security.require_user)):
    """Who is logged in and what they may do.

    The page reads `role` to decide what to grey out. That is presentation
    only — the same rule is enforced on every request, so a doctored answer
    here buys nothing.
    """
    return {"username": claims["username"], "role": claims["role"],
            "is_admin": claims["role"] == security.ADMIN}
