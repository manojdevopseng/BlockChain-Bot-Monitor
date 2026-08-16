"""The bill for one order — the same bill, twice.

`fields()` is what the invoice says; `pdf()` draws exactly that and nothing
else. The page renders `fields()` too, so what somebody reads on screen and
what they download can never drift apart — which is the usual way a receipt
ends up disagreeing with itself.

Who the seller is comes from .env (INVOICE_*), because it is the one thing on
here this code cannot know. Blank is handled: the invoice prints the product
name and leaves the legal block out rather than inventing a company.

Deliberately called a receipt for a payment, not a tax invoice. A tax invoice
needs a registered entity, a jurisdiction and usually a tax number; if those
are set in .env they appear, and until then this document does not claim to be
something it is not.
"""

from __future__ import annotations

import io
import time
from typing import Optional

from .config import settings

# The one font choice. Helvetica is a PDF core font, so nothing is embedded and
# the file stays a few kilobytes — which matters for something emailed.
_FONT = "Helvetica"


def _date(ts: Optional[float]) -> str:
    return time.strftime("%d %b %Y", time.localtime(ts)) if ts else "—"


def _money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _ansi(text: str) -> str:
    """Text a PDF core font can actually draw.

    The core fonts are WinAnsi (cp1252), not Latin-1 — the difference matters
    here because the em dash, the middot and curly quotes all live in that gap,
    and encoding to Latin-1 turned every "Yearly — SightLine" into "Yearly ?".

    Anything outside even cp1252 becomes '?', which is ugly and honest: the
    alternative is a 500 on the one page somebody wanted a document from.
    """
    return str(text or "").encode("cp1252", "replace").decode("cp1252")


def number(order: dict) -> str:
    """The invoice number. Derived from the order id rather than a counter:
    a counter needs a lock and a migration, and this is already unique, already
    printed on the order page, and already what support will be asked about."""
    return f"SL-{str(order.get('id') or '').replace('ORD-', '')}"


def fields(order: dict, account: Optional[dict] = None) -> dict:
    """Everything on the bill, as data.

    Both the PDF and the page render this, so there is one answer to "what does
    my receipt say" rather than two that agree until someone edits one.
    """
    account = account or {}
    paid_at = order.get("paid_at") or order.get("activated_at")
    settled = str(order.get("status") or "") in ("paid", "activated")
    return {
        "number": number(order),
        "order_id": order.get("id") or "",
        "issued_on": _date(order.get("created_at")),
        "paid_on": _date(paid_at) if paid_at else "",
        "status": "PAID" if settled else str(order.get("status") or "").upper(),
        "settled": settled,

        "seller": {
            "name": settings.invoice_business_name or "SightLine",
            "tagline": "MultiChain Monitor",
            "address": settings.invoice_address or "",
            "email": settings.invoice_email or settings.admin_email or "",
            "tax_id": settings.invoice_tax_id or "",
        },
        "buyer": {
            "name": order.get("user_id") or account.get("username") or "",
            "email": order.get("email") or account.get("email") or "",
        },

        "item": {
            "title": f"{order.get('plan_label') or 'Plan'} — SightLine",
            "detail": "Multi-chain monitoring, alerts and market-cap tracking",
            "period": _period(order, account),
            "price_usd": _money(order.get("price_usd")),
        },
        "total_usd": _money(order.get("price_usd")),
        "paid": {
            "amount": str(order.get("amount_seen") or order.get("amount") or ""),
            "symbol": order.get("symbol") or "",
            "rail": order.get("asset_label") or order.get("asset_id") or "",
            "address": order.get("address") or "",
        },
        "notes": [
            "Paid in cryptocurrency. An on-chain payment is final once "
            "confirmed and cannot be reversed by us.",
            "SightLine reports what is happening on chain and on X. It is not "
            "financial advice.",
        ],
    }


def _period(order: dict, account: dict) -> str:
    """"16 Aug 2026 to 16 Aug 2027", when both ends are known."""
    start = order.get("paid_at") or order.get("activated_at")
    until = order.get("plan_until") or account.get("plan_ends_at")
    if start and until:
        return f"{_date(start)} to {_date(until)}"
    return ""


def pdf(order: dict, account: Optional[dict] = None) -> bytes:
    """The bill as a PDF. Raises RuntimeError when fpdf2 is not installed."""
    try:
        from fpdf import FPDF
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("fpdf2 is not installed — pip install fpdf2") from exc

    f = fields(order, account)
    doc = FPDF(format="A4", unit="mm")
    doc.set_auto_page_break(auto=True, margin=18)
    doc.add_page()
    doc.set_margins(18, 18, 18)

    ink, faint, rule = (17, 24, 39), (110, 120, 135), (215, 220, 228)
    green = (15, 122, 90)

    # ── head ────────────────────────────────────────────────────────────────
    doc.set_font(_FONT, "B", 20)
    doc.set_text_color(*ink)
    doc.cell(100, 9, _ansi(f["seller"]["name"]))
    doc.set_font(_FONT, "B", 14)
    doc.set_text_color(*faint)
    doc.cell(0, 9, "RECEIPT", align="R", new_x="LMARGIN", new_y="NEXT")

    doc.set_font(_FONT, "", 9)
    doc.cell(100, 5, _ansi(f["seller"]["tagline"]))
    doc.set_font(_FONT, "", 9)
    doc.cell(0, 5, _ansi(f["number"]), align="R", new_x="LMARGIN", new_y="NEXT")
    doc.ln(6)

    # ── who / when ──────────────────────────────────────────────────────────
    top = doc.get_y()
    doc.set_font(_FONT, "B", 8)
    doc.set_text_color(*faint)
    doc.cell(90, 5, "BILLED TO", new_x="LMARGIN", new_y="NEXT")
    doc.set_font(_FONT, "", 10)
    doc.set_text_color(*ink)
    doc.cell(90, 5, _ansi(f["buyer"]["name"]), new_x="LMARGIN", new_y="NEXT")
    if f["buyer"]["email"]:
        doc.set_font(_FONT, "", 9)
        doc.set_text_color(*faint)
        doc.cell(90, 5, _ansi(f["buyer"]["email"]), new_x="LMARGIN", new_y="NEXT")
    left_end = doc.get_y()

    doc.set_xy(110, top)
    for label, value in (("Issued", f["issued_on"]),
                         ("Paid on", f["paid_on"] or "—"),
                         ("Status", f["status"]),
                         ("Order", f["order_id"])):
        doc.set_x(110)
        doc.set_font(_FONT, "", 9)
        doc.set_text_color(*faint)
        doc.cell(24, 5, label)
        doc.set_font(_FONT, "B" if label == "Status" else "", 9)
        doc.set_text_color(*(green if label == "Status" and f["settled"] else ink))
        doc.cell(0, 5, _ansi(value), new_x="LMARGIN", new_y="NEXT")
    doc.set_y(max(left_end, doc.get_y()) + 6)

    # ── the line ────────────────────────────────────────────────────────────
    doc.set_draw_color(*rule)
    doc.set_line_width(0.3)
    y = doc.get_y()
    doc.line(18, y, 192, y)
    doc.ln(3)

    doc.set_font(_FONT, "B", 8)
    doc.set_text_color(*faint)
    doc.cell(120, 6, "DESCRIPTION")
    doc.cell(0, 6, "AMOUNT", align="R", new_x="LMARGIN", new_y="NEXT")

    doc.set_font(_FONT, "B", 11)
    doc.set_text_color(*ink)
    doc.cell(120, 6, _ansi(f["item"]["title"]))
    doc.cell(0, 6, _ansi(f["total_usd"]), align="R", new_x="LMARGIN", new_y="NEXT")

    doc.set_font(_FONT, "", 9)
    doc.set_text_color(*faint)
    doc.cell(0, 5, _ansi(f["item"]["detail"]), new_x="LMARGIN", new_y="NEXT")
    if f["item"]["period"]:
        doc.cell(0, 5, _ansi(f"Covers {f['item']['period']}"),
                 new_x="LMARGIN", new_y="NEXT")
    doc.ln(2)

    y = doc.get_y()
    doc.line(18, y, 192, y)
    doc.ln(4)

    # ── totals ──────────────────────────────────────────────────────────────
    doc.set_font(_FONT, "B", 11)
    doc.set_text_color(*ink)
    doc.cell(120, 7, "Total")
    doc.cell(0, 7, _ansi(f["total_usd"]), align="R", new_x="LMARGIN", new_y="NEXT")

    if f["paid"]["amount"]:
        doc.set_font(_FONT, "", 9)
        doc.set_text_color(*faint)
        doc.cell(120, 6, _ansi(f"Settled on {f['paid']['rail']}"))
        doc.set_text_color(*ink)
        doc.cell(0, 6, _ansi(f"{f['paid']['amount']} {f['paid']['symbol']}"),
                 align="R", new_x="LMARGIN", new_y="NEXT")
        if f["paid"]["address"]:
            doc.set_font(_FONT, "", 7.5)
            doc.set_text_color(*faint)
            doc.cell(0, 5, _ansi(f"Paid to {f['paid']['address']}"),
                     new_x="LMARGIN", new_y="NEXT")
    doc.ln(6)

    # ── the small print ─────────────────────────────────────────────────────
    doc.set_font(_FONT, "", 8)
    doc.set_text_color(*faint)
    for note in f["notes"]:
        doc.multi_cell(0, 4.5, _ansi(note))
        doc.ln(1)

    seller = f["seller"]
    tail = " · ".join(x for x in (seller["address"], seller["email"],
                                  (f"Tax ID {seller['tax_id']}"
                                   if seller["tax_id"] else "")) if x)
    if tail:
        doc.ln(2)
        doc.multi_cell(0, 4.5, _ansi(tail))

    out = doc.output()
    return bytes(out) if not isinstance(out, (bytes, bytearray)) else bytes(out)


def filename(order: dict) -> str:
    return f"{number(order)}.pdf"
