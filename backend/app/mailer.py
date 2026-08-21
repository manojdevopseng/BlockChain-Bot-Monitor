"""Email out — confirmations, resets, and the notices an account expects.

SMTP, because it is the one transport every provider speaks and it needs no
SDK: Gmail, Zoho, SES, Postmark and Resend all accept it. Nothing here fails a
request — an account that cannot be emailed is still an account, and the link
is returned to the caller in the log so a stuck sign-up can be finished by
hand rather than lost.

Blank SMTP_HOST means "not configured yet": every send is written to the log
instead, with the link in full. That is what makes registration testable on a
box with no mail server, and it says so loudly rather than pretending it sent.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Optional

from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)


def configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def _base_url() -> str:
    return (settings.public_url or "http://localhost:3000").rstrip("/")


async def send(to: str, subject: str, body: str,
               attachment: Optional[tuple[str, bytes, str]] = None) -> bool:
    """One message. False when it could not be sent — never raises.

    `attachment` is (filename, bytes, mime subtype). One is enough: the only
    thing this app has ever needed to attach is a receipt.
    """
    if not configured():
        log.warning(f"[MAIL] not configured — would have sent to {to}: "
                    f"{subject}\n{body}")
        return False
    try:
        return await asyncio.to_thread(_send_blocking, to, subject, body,
                                       attachment)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[MAIL] send to {to} failed: {exc}")
        return False


def _send_blocking(to: str, subject: str, body: str,
                   attachment: Optional[tuple[str, bytes, str]] = None) -> bool:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        name, blob, subtype = attachment
        msg.add_attachment(blob, maintype="application", subtype=subtype,
                           filename=name)
    port = int(settings.smtp_port or 587)
    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.smtp_host, port, timeout=20)
    else:
        server = smtplib.SMTP(settings.smtp_host, port, timeout=20)
        server.starttls()
    with server:
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    log.info(f"[MAIL] sent '{subject}' to {to}")
    return True


# ── the messages themselves ──────────────────────────────────────────────────

async def send_verification(to: str, username: str, token: str) -> bool:
    link = f"{_base_url()}/verify?token={token}"
    return await send(
        to, "Confirm your email",
        f"Hi {username},\n\n"
        f"Confirm your email address to start your 7-day trial on "
        f"SightLine:\n\n{link}\n\n"
        f"If you did not sign up, ignore this message — nothing happens until "
        f"that link is opened.\n")


async def send_reset(to: str, username: str, token: str) -> bool:
    link = f"{_base_url()}/reset?token={token}"
    return await send(
        to, "Reset your password",
        f"Hi {username},\n\n"
        f"Open this link within the hour to set a new password:\n\n{link}\n\n"
        f"If you did not ask for this, ignore it — your current password still "
        f"works and nothing has changed.\n")


async def send_order_received(to: str, username: str, order: dict) -> bool:
    return await send(
        to, f"Payment seen — order {order.get('id', '')}",
        f"Hi {username},\n\n"
        f"We have seen your payment for the {order.get('plan_label', '')} plan "
        f"and are waiting for confirmations. Your Order Status page updates as "
        f"they arrive:\n\n{_base_url()}/app/billing\n")


async def send_order_activated(to: str, username: str, order: dict,
                               receipt: Optional[tuple[str, bytes]] = None) -> bool:
    """The "you're in" mail, with the receipt attached when one could be built.

    Attached rather than linked: a receipt somebody has to log in to fetch is a
    receipt they end up asking support for.
    """
    return await send(
        to, f"You're in — {order.get('plan_label', '')} active",
        f"Hi {username},\n\n"
        f"Your SightLine {order.get('plan_label', '')} plan is active until "
        f"{order.get('expires_on', '')}.\n\n"
        + ("Your receipt is attached.\n\n" if receipt else "")
        + f"Connect Telegram from your Profile to get alerts on your phone:\n\n"
        f"{_base_url()}/app/profile\n",
        attachment=(receipt[0], receipt[1], "pdf") if receipt else None)


async def notify_admin(subject: str, body: str) -> bool:
    """Something the operator has to see: a payment, or a support ticket."""
    to = settings.admin_email or settings.smtp_from
    if not to:
        log.warning(f"[MAIL] no admin address set — {subject}\n{body}")
        return False
    return await send(to, subject, body)
