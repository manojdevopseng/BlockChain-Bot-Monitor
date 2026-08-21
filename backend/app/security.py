"""JWT auth for the dashboard login, and the two roles it issues.

Two accounts, both from env:

  admin — everything. ADMIN_USERNAME / ADMIN_PASSWORD.
  user  — sees the whole dashboard and can change nothing. USER_USERNAME /
          USER_PASSWORD; blank means no such account exists and only the admin
          can log in.

The rule for `user` is deliberately one rule rather than a list of pages: any
request that would change something needs admin, and so does anything that
hands out a secret. A greyed-out nav item is a courtesy to the person using the
dashboard, not a control — the control is here, and it holds whether the
request came from the UI, from curl, or from a tab an admin left open.

A `users` collection can replace the env accounts later without touching
routers; everything downstream depends on these dependencies.

Selling the dashboard adds a third question after "who is this" and "may they
write": is their subscription still running. That one is `require_active`, and
it is deliberately separate — an expired account must still be able to log in,
see its own Profile and pay, or there is no way back in.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from .config import settings

_ALGO = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

ADMIN = "admin"
USER = "user"

# The methods that cannot change anything. Everything else needs admin when the
# caller is a `user`, which is the whole of the write rule.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def authenticate(username: str, password: str) -> Optional[str]:
    """The role these credentials buy, or None.

    The admin comes from env — it has to work before there is a database to
    read, and it is the account that creates all the others. Everyone else
    comes from the `users` collection, made on the User Management page.
    """
    if (username == settings.admin_username
            and password == settings.admin_password):
        return ADMIN
    # The env-configured read-only account, kept as a way in if the database is
    # unavailable. Blank means it does not exist — without this check, empty
    # credentials would log in as `user` on any box that never configured one.
    if (settings.user_username and settings.user_password
            and username == settings.user_username
            and password == settings.user_password):
        return USER

    from . import users
    return await users.verify(username, password)


def create_token(username: str, role: str = ADMIN) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + settings.jwt_expire_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
    except JWTError:
        return None
    if not payload.get("sub"):
        return None
    # Tokens issued before roles existed carry none. Those were all admin
    # tokens, and reading them as `user` would lock an admin out of their own
    # dashboard until the token expired.
    return {"username": payload["sub"], "role": payload.get("role") or ADMIN}


async def require_user(token: Optional[str] = Depends(oauth2_scheme)) -> dict:
    """Rejects unauthenticated requests. Returns {username, role}."""
    if not token or not (claims := decode_token(token)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


async def account(claims: dict = Depends(require_user)) -> dict:
    """The full account row behind a token, or a stand-in for the env admin.

    The env accounts exist so the box is reachable when the database is not,
    so they cannot be looked up — they are answered from settings instead, with
    the same shape every caller downstream expects.
    """
    from . import accounts
    if claims["role"] == ADMIN and claims["username"] == settings.admin_username:
        return {"username": claims["username"], "role": ADMIN,
                "email_verified": True, "plan": "admin"}
    doc = await accounts.by_username(claims["username"])
    if doc is None:
        # A token for an account that has since been deleted.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="This account no longer exists")
    if not doc.get("enabled", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This account has been disabled")
    return doc


async def require_active(doc: dict = Depends(account)) -> dict:
    """For the product itself: a live trial or a paid subscription.

    402 rather than 403 on purpose — "you may, but not until you pay" is a
    different answer from "you may not", and the dashboard shows a paywall for
    one and an error for the other.
    """
    from . import accounts
    state = accounts.access(doc)
    if not state.usable:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail=state.reason or "Your access has ended")
    return doc


async def require_admin(claims: dict = Depends(require_user)) -> dict:
    """For what only an admin may see — a secret, or a page they alone get."""
    if claims["role"] != ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This account has read-only access")
    return claims


async def require_write(request: Request,
                        claims: dict = Depends(require_user)) -> dict:
    """Authenticated to read, admin to change anything.

    For the operator's own surfaces — the panels a customer may look at but
    nobody except an admin may alter. A customer's OWN data is a different
    question, and `require_customer` is the one that answers it.
    """
    if request.method not in READ_METHODS and claims["role"] != ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This account has read-only access")
    return claims


async def require_customer(request: Request,
                           doc: dict = Depends(account)) -> dict:
    """A live account, reading and writing its own rows.

    This is the rule the product runs on, and it is deliberately not
    `require_write`: that one was written when "user" meant read-only staff, so
    it refuses every POST from anyone but an admin — which would mean a paying
    customer could not add a token to their own list.

    What keeps one customer out of another's data is not the method, it is the
    `user_id` in every query. This dependency only answers "is this a live
    account", and the routes answer "whose row is this".
    """
    from . import accounts
    if doc.get("role") == ADMIN:
        return doc
    state = accounts.access(doc)
    if not state.usable:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail=state.reason or "Your access has ended")
    return doc


async def require_no_limits(doc: dict = Depends(account)) -> dict:
    """An account with no ceiling — the operator, or somebody they handed it to.

    Not the same question as `require_admin`, and that is the point. Some
    panels are the operator watching their own machine rather than a feature
    anybody buys: gas fees on their own chain, for instance, which is only
    actionable by somebody who can act on it. Those follow the ceiling, not
    the role, so that handing an account admin limits hands it those panels
    too without handing it the controls.
    """
    from . import accounts
    if doc.get("role") == ADMIN or doc.get("unlimited"):
        return doc
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                        detail="This panel is for accounts with no plan limits")


async def require_customer_read(request: Request,
                                doc: dict = Depends(require_customer)) -> dict:
    """The shared panels: a live account may read them, only an admin writes.

    Detections, Alerts, Tokens, Analytics — one set of data, produced by the
    scanners for everybody. Nobody's to edit but the operator's.
    """
    if request.method not in READ_METHODS and doc.get("role") != ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only an admin can change this")
    return doc
