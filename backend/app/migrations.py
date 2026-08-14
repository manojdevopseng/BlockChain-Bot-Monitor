"""One-time data moves that run at startup, each safe to run again.

Adding accounts to a dashboard that never had them means the rows already in
the database belong to nobody: `mcap_tokens` and `rsi_tokens` were written when
there was one person using it, and a query scoped to an owner would simply stop
finding them. So they are adopted by the admin, once, and the app stops being
single-tenant without losing anything that was in it.

Every migration here is written to be idempotent and to log what it did. None
of them may throw: a box that cannot migrate must still start, because the
scanners are the part that must never stop.
"""

from __future__ import annotations

from . import db
from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)

# The collections whose rows belong to one account.
OWNED = ("mcap_tokens", "rsi_tokens")


async def run() -> None:
    for step in (_grandfather_accounts, _adopt_orphans, _stamp_cadence):
        try:
            await step()
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[MIGRATE] {step.__name__} skipped: {exc}")


async def _grandfather_accounts() -> None:
    """Keep the accounts that existed before subscriptions did.

    An account an admin created by hand has no email, no plan and no
    confirmation — and `access()` reads all three, so without this the people
    already using the dashboard would be told to confirm an address they never
    gave and pay for a trial they never took. They are marked confirmed
    (an admin vouched for them by creating the account) and given a plan that
    does not run out, with `comped` on the row saying why.
    """
    col = db.get_collection("users")
    far_future = 4102444800.0            # 1 Jan 2100
    res = await col.update_many(
        {"plan": {"$exists": False}},
        {"$set": {"plan": "yearly", "comped": True, "email_verified": True,
                  "trial_used": True, "plan_ends_at": far_future,
                  "comped_reason": "account existed before subscriptions"}})
    if res.modified_count:
        log.info(f"[MIGRATE] users: {res.modified_count} existing account(s) "
                 f"kept working, on the house")


async def _adopt_orphans() -> None:
    """Give every ownerless row to the admin.

    The admin username comes from .env, which is the account that existed when
    those rows were written — there is no other candidate, and guessing a
    different one would hand somebody else's watchlist to a stranger.
    """
    owner = settings.admin_username
    for name in OWNED:
        col = db.get_collection(name)
        res = await col.update_many({"user_id": {"$exists": False}},
                                    {"$set": {"user_id": owner}})
        if res.modified_count:
            log.info(f"[MIGRATE] {name}: {res.modified_count} row(s) adopted "
                     f"by {owner}")


async def _stamp_cadence() -> None:
    """Put a cadence on rows written before it lived on the row.

    The worker reads it from there now so a pass never has to look an account
    up; a row without one would fall back to the default forever.
    """
    from .scanners.mcap_tracker import CADENCES, DEFAULT_CADENCE
    col = db.get_collection("mcap_tokens")
    res = await col.update_many({"cadence": {"$exists": False}},
                                {"$set": {"cadence": CADENCES[DEFAULT_CADENCE]}})
    if res.modified_count:
        log.info(f"[MIGRATE] mcap_tokens: {res.modified_count} row(s) given the "
                 f"default cadence")
