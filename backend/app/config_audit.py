"""What is switched on but has nowhere to go.

A blank destination is a skipped route, deliberately: `.env` says so, and it is
what lets the app run on a fresh box without a hundred errors. The cost is that
a feature you meant to use can be off for weeks and never say a word — the
switch is on, the code runs, the send finds no chat id and returns quietly.

Every real fault found on this deployment in a day of work was that shape: a
mirror pointing nowhere, a gas alert with no chat, a chain with no endpoint, a
customer group falling back to the operator's own firehose. None of them raised
anything. All of them were only found by going and looking.

So this goes and looks, once at startup and on demand from the Settings page.
It reports rather than raises: a misconfiguration is not a reason to refuse to
start, it is a reason to be told.

Severity means what it says:
  `error`  the feature is on and cannot work at all
  `warn`   it works, but not the way somebody probably intended
"""

from __future__ import annotations

from typing import Any

from . import registry
from .scanners import scfg
from .scanners.slog import get_logger

log = get_logger(__name__)


def _blank(v: Any) -> bool:
    return not str(v or "").strip()


async def audit() -> list[dict]:
    """Every switched-on feature whose configuration cannot carry it."""
    from .config import settings

    enabled = await registry.enabled_map()
    on = lambda key, default=True: bool(enabled.get(key, default))  # noqa: E731
    out: list[dict] = []

    def say(level: str, feature: str, missing: str, effect: str) -> None:
        out.append({"level": level, "feature": feature,
                    "missing": missing, "effect": effect})

    # ── the bot itself ──────────────────────────────────────────────────────
    if _blank(settings.telegram_bot_token):
        say("error", "Telegram bot", "TELEGRAM_BOT_TOKEN",
            "No alert, command or customer message can be sent at all.")

    # ── the userbot ─────────────────────────────────────────────────────────
    if on("forwarder"):
        if not settings.telethon_api_id or _blank(settings.telethon_api_hash):
            say("error", "Forwarder", "TELETHON_API_ID / TELETHON_API_HASH",
                "The userbot cannot log in, so no premium group is read.")

    # ── destinations that are on but point nowhere ──────────────────────────
    routes = [
        ("premium_callers_signal", "Premium mirror", "DEST_PREMIUM_ALL",
         settings.dest_premium_all,
         "Premium messages are recorded but mirrored nowhere."),
        ("important_caller", "Important Caller", "DEST_IMPORTANT_CALLER",
         getattr(settings, "dest_important_caller", ""),
         "Starred callers are not forwarded anywhere."),
        ("eth_gas_fees", "ETH Gas Fees", "GAS_ALERT_CHAT_ID",
         settings.gas_alert_chat_id,
         "Gas alerts fill the panel and reach no chat."),
        ("sol_to_eth", "SOL to ETH", "CROSS_CHAIN_CHAT_ID",
         settings.cross_chain_chat_id,
         "Cross-chain matches are recorded and not announced."),
        ("sol_to_rbh", "SOL to Robinhood", "ROBINHOOD_CHAT_ID",
         getattr(settings, "robinhood_chat_id", ""),
         "Cross-chain matches are recorded and not announced."),
    ]
    for key, feature, env_key, value, effect in routes:
        if on(key) and _blank(value):
            say("warn", feature, env_key, effect)

    # Per-chain premium groups: on, detecting, and announcing nowhere.
    for chain, key in (("ETH", "premium_eth_detection"),
                       ("RBH", "premium_rbh_detection"),
                       ("BNB", "premium_bnb_detection"),
                       ("SOL", "premium_sol_detection")):
        if on(key) and _blank(scfg.DEST_PREMIUM_BY_CHAIN.get(chain.lower())):
            say("warn", f"Premium {chain}", f"DEST_PREMIUM_{chain}",
                f"{chain} detections reach the panel and no Telegram group.")

    # ── chains that are on with no endpoint ─────────────────────────────────
    for chain, key, pool, env_key in (
            ("Ethereum", "premium_eth_detection", scfg.ETH_HTTP_ENDPOINTS, "ETH_RPC_HTTP"),
            ("Robinhood", "premium_rbh_detection", scfg.RBH_HTTP_ENDPOINTS, "RBH_RPC_HTTP"),
            ("BNB", "premium_bnb_detection", scfg.BNB_HTTP_ENDPOINTS, "BNB_RPC_HTTP"),
            ("Solana", "premium_sol_detection", scfg.SOL_HTTP_ENDPOINTS, "SOL_RPC_HTTP")):
        if on(key) and not pool:
            say("error", f"Premium {chain} detection", env_key,
                f"Every {chain} address is skipped without a word — the chain "
                f"check cannot run with no endpoint.")

    # ── the shared customer group ───────────────────────────────────────────
    if on("member_group"):
        member = str(getattr(settings, "member_group_chat_id", "") or "").strip()
        if not member and not _blank(settings.dest_premium_all):
            say("warn", "Premium Callers group", "MEMBER_GROUP_CHAT_ID",
                "Blank, so paying accounts are invited into DEST_PREMIUM_ALL — "
                "the operator's own raw mirror.")
        elif not member:
            say("error", "Premium Callers group", "MEMBER_GROUP_CHAT_ID",
                "Nobody can be invited: there is no group to invite them to.")

    # ── things a product needs and a monitor did not ────────────────────────
    if _blank(getattr(settings, "smtp_host", "")) or _blank(getattr(settings, "smtp_from", "")):
        say("warn", "Email", "SMTP_HOST / SMTP_FROM",
            "Nothing is emailed. A new account cannot confirm its address on "
            "its own and has to be verified by hand from User Management.")

    pub = str(getattr(settings, "public_url", "") or "")
    if not pub.startswith(("http://", "https://")) or "localhost" in pub:
        say("warn", "Public URL", "PUBLIC_URL",
            "Links back into the dashboard are dropped from Telegram messages, "
            "because a button Telegram cannot reach fails the whole message.")

    if on("payments"):
        from . import payments
        if not payments.available():
            say("error", "Payments", "PAY_* addresses",
                "Orders can be created and nothing can ever be seen arriving.")

    # Spending money silently. Live trading is the one feature where "it
    # worked and said nothing" is indistinguishable from "it never ran", and
    # the person finds out by checking their wallet.
    try:
        from . import db as _db
        armed = await _db.get_collection("trading_settings").count_documents(
            {"live_trading": True})
    except Exception:  # noqa: BLE001
        armed = 0
    if armed and _blank(settings.trading_alert_chat_id) and _blank(
            getattr(settings, "alert_chat_id", "")):
        say("warn", "Live trading alerts", "TRADING_ALERT_CHAT_ID",
            f"{armed} account(s) can spend real money and no Telegram "
            f"destination is set, so every buy and sell happens silently.")

    # A relay that has stopped answering is the quietest failure here: every
    # switch stays green, the panel keeps saying "protected", and the orders
    # go out the ordinary way. Nothing raises, because nothing is broken —
    # the endpoint simply is not there any more.
    from . import mev
    for row in await mev.status():
        if not row["supported"] or row["reachable"]:
            continue
        say("warn", f"MEV protection — {row['chain'].upper()}",
            f"{row['chain'].upper()}_MEV_RPC",
            f"{row['relay'] or 'The relay'} is not answering"
            + (f" ({row['why']})" if row["why"] else "")
            + ". Orders on this chain would go out unprotected.")

    return out


async def log_report() -> list[dict]:
    """Say it once at startup, loudest first. Never raises."""
    try:
        rows = await audit()
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[CONFIG] audit skipped: {exc}")
        return []
    errors = [r for r in rows if r["level"] == "error"]
    warns = [r for r in rows if r["level"] == "warn"]
    for r in errors:
        log.error(f"[CONFIG] {r['feature']}: {r['missing']} is not set — {r['effect']}")
    for r in warns:
        log.warning(f"[CONFIG] {r['feature']}: {r['missing']} is not set — {r['effect']}")
    if not rows:
        log.info("[CONFIG] every switched-on feature has somewhere to go")
    return rows
