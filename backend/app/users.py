"""Dashboard accounts, created by an admin.

There is no sign-up and no route that could become one: an account exists
because an admin made it, and every endpoint that touches this collection is
behind `require_admin`. That is the whole access model — the dashboard controls
real money-adjacent scanners, so who gets in is a decision, not a form.

Passwords are stored as bcrypt hashes and never read back. A forgotten one is
replaced, not recovered; nothing here can return the original, including to an
admin.

bcrypt is used directly rather than through passlib: passlib 1.7.4's bcrypt
backend breaks against bcrypt 5.x (it reads `__about__.__version__`, gone since
4.1), which is what is installed here.
"""

from __future__ import annotations

import re
import time
from typing import Optional

import bcrypt

from . import db

# Anything that reads as a login. Deliberately narrow: these end up in a JWT
# `sub` and in log lines, and a username with a space or a slash in it is a
# problem waiting for somewhere to happen.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD = 8
# bcrypt truncates at 72 bytes and raises above it, so the limit is stated here
# rather than discovered as a 500 the first time somebody pastes a passphrase.
MAX_PASSWORD = 72


def _col():
    return db.get_collection("users")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate(username: str, password: Optional[str]) -> Optional[str]:
    """The reason these are unusable, or None. Written for the person typing."""
    if not USERNAME_RE.match(username or ""):
        return ("A username is 3-32 characters, letters, numbers, dot, dash or "
                "underscore")
    if password is None:
        return None
    if len(password) < MIN_PASSWORD:
        return f"A password needs at least {MIN_PASSWORD} characters"
    if len(password.encode("utf-8")) > MAX_PASSWORD:
        return f"A password can be at most {MAX_PASSWORD} bytes"
    return None


def _public(doc: dict) -> dict:
    """A user as the dashboard sees them — never the hash."""
    return {"username": doc["username"],
            "role": doc.get("role", "user"),
            "enabled": bool(doc.get("enabled", True)),
            "created_at": doc.get("created_at"),
            "created_by": doc.get("created_by"),
            "last_login": doc.get("last_login")}


async def listing() -> list[dict]:
    docs = await _col().find({}).sort("created_at", 1).to_list(200)
    return [_public(d) for d in docs]


async def get(username: str) -> Optional[dict]:
    # Case-insensitive so "Ravi" and "ravi" cannot be two accounts.
    return await _col().find_one(
        {"username": {"$regex": f"^{re.escape(username)}$", "$options": "i"}})


async def create(username: str, password: str, by: str) -> dict:
    if await get(username):
        raise ValueError(f"'{username}' already exists")
    doc = {"username": username, "password": hash_password(password),
           "role": "user", "enabled": True,
           "created_at": time.time(), "created_by": by, "last_login": None}
    await _col().insert_one(doc)
    return _public(doc)


async def set_password(username: str, password: str) -> bool:
    r = await _col().update_one({"username": username},
                                {"$set": {"password": hash_password(password),
                                          "password_set_at": time.time()}})
    return r.matched_count > 0


async def set_enabled(username: str, enabled: bool) -> bool:
    r = await _col().update_one({"username": username},
                                {"$set": {"enabled": bool(enabled)}})
    return r.matched_count > 0


async def delete(username: str) -> bool:
    r = await _col().delete_one({"username": username})
    return r.deleted_count > 0


async def verify(username: str, password: str) -> Optional[str]:
    """The role these credentials buy, or None. Disabled accounts get nothing.

    A missing user is still run through a hash comparison so that "no such
    account" and "wrong password" take about the same time to answer.
    """
    doc = await get(username)
    stored = (doc or {}).get("password") or ""
    ok = check_password(password, stored) if stored else check_password(
        password, "$2b$12$" + "." * 53)
    if not doc or not ok or not doc.get("enabled", True):
        return None
    await _col().update_one({"username": doc["username"]},
                            {"$set": {"last_login": time.time()}})
    return doc.get("role", "user")
