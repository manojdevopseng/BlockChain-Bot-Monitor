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


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    if not security.verify_credentials(form.username, form.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return TokenOut(access_token=security.create_token(form.username),
                    username=form.username)


@router.get("/me")
async def me(user: str = Depends(security.require_user)):
    return {"username": user}
