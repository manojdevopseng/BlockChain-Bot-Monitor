"""Buying a plan: the rails on offer, the order, and its status.

An order can be created by an account whose subscription has ended — that is
the whole point of it — so these routes need a login and nothing more. Every
query carries the buyer's username, so an order id guessed from another account
answers 404 rather than showing somebody else's payment.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from .. import accounts, invoice, orders, payments, security

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/options")
async def options(doc: dict = Depends(security.account)):
    """What can be bought, and what it can be paid with."""
    plan = accounts.plan_of(doc)
    return {
        "plans": [
            {"id": p.id, "label": p.label, "price_usd": p.price_usd,
             "days": p.days, "note": p.note, "current": p.id == plan.id}
            for p in accounts.PLANS.values() if p.price_usd > 0
        ],
        "assets": [
            {"id": next(k for k, v in payments.ASSETS.items() if v is a),
             "label": a.label, "symbol": a.symbol, "chain": a.chain,
             "fee_note": a.fee_note}
            for a in payments.available()
        ],
        "ttl_minutes": orders.TTL_MINUTES,
    }


@router.post("/orders")
async def create_order(payload: dict = Body(...),
                       doc: dict = Depends(security.account)):
    try:
        row = await orders.create(doc, str(payload.get("plan") or ""),
                                  str(payload.get("asset") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return orders.public(row)


@router.get("/orders")
async def my_orders(doc: dict = Depends(security.account)):
    return {"items": await orders.mine(doc["username"])}


@router.get("/orders/{order_id}")
async def one_order(order_id: str, doc: dict = Depends(security.account)):
    row = await orders.get(order_id, doc["username"])
    if row is None:
        raise HTTPException(404, "No such order")
    out = orders.public(row)
    # The QR is built here rather than in the browser: one dependency on the
    # server beats one in every page, and the payload is a wallet URI that has
    # to match the address and amount exactly.
    out["pay_uri"] = _pay_uri(row)
    out["qr_svg"] = _qr(out["pay_uri"] or row.get("address", ""))
    return out


@router.get("/orders/{order_id}/receipt")
async def receipt(order_id: str, doc: dict = Depends(security.account)):
    """What the bill says, as data — the page draws this.

    The same call the PDF is drawn from, so what somebody reads on screen and
    what they download cannot drift apart.
    """
    row = await orders.get(order_id, doc["username"])
    if row is None:
        raise HTTPException(404, "No such order")
    return invoice.fields(orders.public(row), doc)


@router.get("/orders/{order_id}/receipt.pdf")
async def receipt_pdf(order_id: str, doc: dict = Depends(security.account)):
    """The bill as a file, for whoever has to keep one.

    Available before payment as well as after: an unpaid order's receipt says
    AWAITING PAYMENT on it, which is a perfectly good thing to send to whoever
    approves the spend.
    """
    row = await orders.get(order_id, doc["username"])
    if row is None:
        raise HTTPException(404, "No such order")
    public = orders.public(row)
    try:
        body = invoice.pdf(public, doc)
    except RuntimeError as exc:
        # A missing library is ours to fix, and saying so beats a 500 with a
        # stack trace on the one page somebody wanted a document from.
        raise HTTPException(503, f"The receipt cannot be built right now "
                                 f"({exc}). Everything else about this order "
                                 f"is unaffected.")
    return Response(
        content=body, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="{invoice.filename(public)}"'})


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, doc: dict = Depends(security.account)):
    if not await orders.cancel(order_id, doc["username"]):
        raise HTTPException(404, "No open order with that id")
    return {"cancelled": True}


def _pay_uri(row: dict) -> str:
    """A wallet-openable link, where the chain has one worth using.

    EIP-681 for the EVM chains and Solana Pay for Solana, both carrying the
    exact amount so the payer does not retype it — mistyping the figure is the
    one thing that stops an order settling by itself. Tron has no equivalent
    that wallets agree on, so it gets the plain address.
    """
    asset = payments.asset_by_id(row.get("asset_id", ""))
    if asset is None:
        return ""
    address, amount = row.get("address", ""), row.get("amount", 0)
    if asset.chain in ("eth", "bsc"):
        chain_id = 1 if asset.chain == "eth" else 56
        units = int(round(float(amount) * (10 ** asset.decimals)))
        return (f"ethereum:{asset.contract}@{chain_id}/transfer"
                f"?address={address}&uint256={units}")
    if asset.chain == "sol":
        return (f"solana:{address}?amount={amount}"
                f"&spl-token={asset.contract}")
    return address


def _qr(data: str) -> str:
    """An inline SVG QR, or "" if the library is missing.

    Never fatal: an order with an address and an amount is payable by hand, and
    a checkout that 500s because a drawing failed is worse than one without a
    picture.
    """
    if not data:
        return ""
    try:
        import io

        import segno
        # BytesIO, not StringIO: segno's SVG writer emits encoded bytes, and a
        # text buffer raises "string argument expected, got 'bytes'" — which the
        # except below would have swallowed into a checkout with no QR at all.
        buf = io.BytesIO()
        segno.make(data, error="m").save(buf, kind="svg", xmldecl=False,
                                         svgclass=None, lineclass=None,
                                         omitsize=True, dark="#e5e7eb",
                                         light=None)
        return buf.getvalue().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        # Not fatal — an order with an address and an amount is payable by
        # hand — but say so, because a silently missing QR looks like a design
        # choice rather than a broken dependency.
        from ..scanners.slog import get_logger
        get_logger(__name__).warning(f"[BILLING] QR not drawn: {exc}")
        return ""
