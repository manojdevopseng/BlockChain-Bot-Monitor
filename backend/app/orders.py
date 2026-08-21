"""An order: what was chosen, what to pay, and what happened next.

One row per attempt to buy, and it never changes plan or price after it is
made — a quote that moves while somebody is copying an address into a wallet is
how a payment arrives against the wrong number.

    awaiting_payment  the address, the exact figure, and a clock
    paid              that figure arrived; the plan is being applied
    activated         the plan is running, and the row says until when
    expired           the clock ran out with nothing seen
    cancelled         the buyer said so

An expired order is not a wasted payment: the watcher matches on the amount, so
a late transfer still settles the order it was quoted for. Expiry only stops
that figure being reserved for ever.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

from . import accounts, db, mailer, payments
from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)

OPEN = "awaiting_payment"
PAID = "paid"
ACTIVATED = "activated"
EXPIRED = "expired"
CANCELLED = "cancelled"

# How long a quote is held. Long enough to open a wallet, top it up and send;
# short enough that the unique amounts do not all get used up.
TTL_MINUTES = 45

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no I/O/0/1 to read aloud


def _col():
    return db.get_collection("orders")


def _id() -> str:
    return "ORD-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


# The receipt number. Separate from the order id on purpose: the id is random
# so it cannot be guessed from a URL, and a receipt number has to be sequential
# so a run of them can be seen to have no holes in it.
INVOICE_PREFIX = "SL"
_INVOICE_PAD = 5


def _invoice_no(seq: int) -> str:
    return f"{INVOICE_PREFIX}-{int(seq):0{_INVOICE_PAD}d}"


async def next_invoice_seq() -> int:
    """The next number in the series, taken atomically.

    One document, one $inc, one round trip — so two orders placed in the same
    instant cannot be handed the same number however many workers are running.
    A number is spent whether or not the order is ever paid: a series with gaps
    in it is worse than a series where some entries say CANCELLED, because a
    gap cannot be told apart from a missing record.
    """
    doc = await db.get_collection("counters").find_one_and_update(
        {"_id": "invoice_no"}, {"$inc": {"seq": 1}},
        upsert=True, return_document=True)
    return int((doc or {}).get("seq") or 1)


def public(row: dict) -> dict:
    """An order as its buyer sees it."""
    asset = payments.asset_by_id(row.get("asset_id", ""))
    return {
        "id": row.get("id"),
        "invoice_no": row.get("invoice_no"),
        "status": row.get("status"),
        "plan": row.get("plan"),
        "plan_label": row.get("plan_label"),
        "price_usd": row.get("price_usd"),
        "amount": row.get("amount"),
        "asset_id": row.get("asset_id"),
        "asset_label": asset.label if asset else row.get("asset_id"),
        "symbol": asset.symbol if asset else "",
        "chain": row.get("chain"),
        "address": row.get("address"),
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
        "paid_at": row.get("paid_at"),
        "activated_at": row.get("activated_at"),
        "plan_until": row.get("plan_until"),
        # What actually landed, which is not always what was quoted — the
        # receipt prints the figure that arrived, not the one we asked for.
        "amount_seen": row.get("amount_seen"),
        "email": row.get("email"),
        "seconds_left": max(0, int(float(row.get("expires_at") or 0) - time.time()))
        if row.get("status") == OPEN else 0,
    }


async def create(account: dict, plan_id: str, asset_id: str) -> dict:
    """Quote a plan on one rail. Raises ValueError with something to read."""
    plan = accounts.PLANS.get(plan_id)
    if plan is None or plan.price_usd <= 0:
        raise ValueError("That is not a plan you can buy")
    asset = payments.asset_by_id(asset_id)
    if asset is None:
        raise ValueError("Unknown payment option")
    address = payments.receiving_address(asset.chain)
    if not address or asset not in payments.available():
        raise ValueError(f"{asset.label} is not accepted yet")

    # One open order per account: two quotes at once is how somebody pays the
    # figure from the first screen while the second one is on the clock.
    await _col().update_many(
        {"user_id": account["username"], "status": OPEN},
        {"$set": {"status": CANCELLED, "cancelled_at": time.time(),
                  "cancel_reason": "replaced by a newer order"}})

    taken = {float(r["amount"]) async for r in
             _col().find({"status": OPEN, "asset_id": asset_id},
                         {"_id": 0, "amount": 1})}
    now = time.time()
    row = {
        "id": _id(),
        # Taken here rather than at payment: every attempt to buy gets a
        # number, so the series covers cancelled and expired orders as well as
        # paid ones and can be read straight through.
        "invoice_no": _invoice_no(await next_invoice_seq()),
        "user_id": account["username"],
        "email": account.get("email", ""),
        "plan": plan.id,
        "plan_label": plan.label,
        "price_usd": plan.price_usd,
        "asset_id": asset_id,
        "chain": asset.chain,
        "symbol": asset.symbol,
        "address": address,
        "amount": payments.unique_amount(plan.price_usd, taken),
        "status": OPEN,
        "created_at": now,
        "expires_at": now + TTL_MINUTES * 60,
    }
    await _col().insert_one(dict(row))
    log.info(f"[ORDER] {row['id']} {account['username']} {plan.label} "
             f"{row['amount']} {asset.symbol} on {asset.chain}")
    return row


async def mine(username: str, limit: int = 25) -> list[dict]:
    rows = await _col().find({"user_id": username}).sort("created_at", -1) \
                       .to_list(limit)
    return [public(r) for r in rows]


async def get(order_id: str, username: Optional[str] = None) -> Optional[dict]:
    flt: dict = {"id": order_id}
    if username is not None:
        flt["user_id"] = username
    return await _col().find_one(flt)


async def cancel(order_id: str, username: str) -> bool:
    res = await _col().update_one(
        {"id": order_id, "user_id": username, "status": OPEN},
        {"$set": {"status": CANCELLED, "cancelled_at": time.time(),
                  "cancel_reason": "cancelled by the buyer"}})
    return bool(res.modified_count)


async def expire_stale() -> int:
    """Let go of quotes nobody paid. Their figures become reusable."""
    res = await _col().update_many(
        {"status": OPEN, "expires_at": {"$lt": time.time()}},
        {"$set": {"status": EXPIRED}})
    return int(res.modified_count)


async def open_orders(asset_id: Optional[str] = None) -> list[dict]:
    """Every quote still waiting, oldest first — the watcher's shopping list.

    Expired ones are included on purpose: a transfer that arrives ten minutes
    late is still that person's money, and matching it beats making them ask.
    """
    flt: dict = {"status": {"$in": [OPEN, EXPIRED]}}
    if asset_id:
        flt["asset_id"] = asset_id
    return await _col().find(flt).sort("created_at", 1).to_list(500)


# How an order came to be paid. "chain" is the watcher seeing the money arrive;
# the rest are an operator saying so, and the order records which — a plan
# granted for cash and a plan the chain confirmed are not the same fact, and a
# month later only the order remembers the difference.
VIA_CHAIN = "chain"
VIA_CASH = "cash"
VIA_CRYPTO = "crypto"
VIA_OTHER = "other"
METHODS = (VIA_CHAIN, VIA_CASH, VIA_CRYPTO, VIA_OTHER)

_VIA_LABEL = {
    VIA_CHAIN: "confirmed on-chain",
    VIA_CASH: "cash, taken by hand",
    VIA_CRYPTO: "crypto, confirmed by hand",
    VIA_OTHER: "settled by hand",
}


async def settle(order: dict, seen: float, *, method: str = VIA_CHAIN,
                 by: str = "") -> Optional[dict]:
    """Mark an order paid and put the account on its plan.

    Written before the plan is applied and again after, so a crash between the
    two leaves an order that says `paid` — which is the state an operator can
    finish by hand. The opposite order would leave money taken and nothing said.

    `method` is how the money actually arrived and `by` is who said so when it
    was not the watcher. Both are stored on the order and both are on the
    receipt, so "why is this account on a yearly plan" has an answer that does
    not depend on anybody remembering.
    """
    method = method if method in METHODS else VIA_OTHER
    now = time.time()
    await _col().update_one({"id": order["id"]},
                            {"$set": {"status": PAID, "paid_at": now,
                                      "amount_seen": seen,
                                      "paid_via": method,
                                      "paid_via_label": _VIA_LABEL[method],
                                      "settled_by": by or ""}})
    doc = await accounts.activate(order["user_id"], order["plan"],
                                  source=f"order {order['id']}")
    if doc is None:
        log.error(f"[ORDER] {order['id']} paid but the account "
                  f"{order['user_id']} could not be found")
        return None
    until = float(doc.get("plan_ends_at") or 0)
    await _col().update_one({"id": order["id"]},
                            {"$set": {"status": ACTIVATED, "activated_at": now,
                                      "plan_until": until}})
    log.info(f"[ORDER] {order['id']} activated — {order['user_id']} on "
             f"{order['plan_label']} until "
             f"{time.strftime('%d-%m-%Y', time.localtime(until))}")

    row = {**order, "expires_on": time.strftime("%d %b %Y", time.localtime(until))}
    from . import notifications
    await notifications.notify(
        order["user_id"], notifications.BILLING,
        f"{order['plan_label']} is active",
        f"Payment received. Your plan runs to {row['expires_on']}.",
        f"/orders/{order['id']}")
    # The receipt travels with the mail. Best-effort on purpose: a PDF that
    # will not build must not stop the "you're in" message, and the same
    # document is on the order page either way.
    receipt = None
    try:
        from . import invoice
        settled_row = await _col().find_one({"id": order["id"]}) or order
        pub = public(settled_row)
        receipt = (invoice.filename(pub), invoice.pdf(pub, doc))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[ORDER] {order['id']} receipt not built: {exc}")
    await mailer.send_order_activated(order.get("email", ""),
                                      order["user_id"], row, receipt)
    await mailer.notify_admin(
        f"Payment received — {order['plan_label']} ({order['id']})",
        f"{order['user_id']} <{order.get('email', '')}> paid "
        f"{seen} {order.get('symbol')} on {order.get('chain')}.\n"
        f"Plan runs to {row['expires_on']}.\n")
    await _notify_operator(
        f"💰 <b>Payment received</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"👤 {order['user_id']}\n"
        f"📦 {order['plan_label']} — ${order.get('price_usd')}\n"
        + (f"⛓ {seen} {order.get('symbol')} on {str(order.get('chain')).upper()}\n"
           if method == VIA_CHAIN else
           f"💵 {_VIA_LABEL[method]}"
           + (f" by {by}" if by else "") + "\n")
        + f"🧾 <code>{order['id']}</code>\n"
        f"📅 runs to {row['expires_on']}")
    return doc


async def note_unmatched(asset_id: str, amount: float) -> None:
    """Money arrived that no open order was quoted. Say so, loudly, once.

    Almost always somebody typing the round number instead of the exact one.
    It is recorded rather than swallowed so it can be settled by hand.
    """
    await db.get_collection("payments_unmatched").insert_one(
        {"asset_id": asset_id, "amount": amount, "at": time.time(),
         "settled": False})
    log.warning(f"[PAY] {amount} arrived on {asset_id} matching no open order")
    await _notify_operator(
        f"⚠️ <b>Unmatched payment</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"{amount} on {asset_id}\n"
        f"No open order was quoted that figure — most likely a round number "
        f"instead of the exact one. Match it by hand from Orders.")


async def _notify_operator(text: str) -> None:
    """To the operator's own chat. Never the buyer's."""
    from . import notifier
    chat = settings.pay_alert_chat_id or settings.alert_chat_id
    if chat:
        await notifier.send_to(chat, text)
