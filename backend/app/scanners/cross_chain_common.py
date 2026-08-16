"""Shared helpers for on-chain cross-chain flows (SOL→ETH, SOL→Robinhood).

Ported from the reference repo (core/cross_chain_common.py); only the imports
changed (storage → Mongo repo). Telegram HTML sender + immediate lean-alert
formatter are unchanged.
"""

from __future__ import annotations

import time

import aiohttp

from app.scanners.onchain_detector import ChainSpec, DetectedToken
from app.scanners.sol_scanner import _launchpad_display
from app.scanners import storage_repo as storage
from app.scanners import scfg as config
from app.scanners.slog import get_logger
from app.util import esc
from app import heartbeat, outcomes

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"

_DEX_LABEL = {"v2": "Uniswap V2", "v3": "Uniswap V3", "v4": "Uniswap V4", "noxa": "Noxa"}


def _token_link(spec: ChainSpec, address: str) -> str:
    if spec.gmgn_slug:
        return f'🔗 <a href="https://gmgn.ai/{spec.gmgn_slug}/token/{address}">View on GMGN</a>'
    if spec.explorer_token_url:
        return f'🔗 <a href="{spec.explorer_token_url.format(addr=address)}">View on Explorer</a>'
    return f'🔗 <a href="https://dexscreener.com/search?q={address}">View on DexScreener</a>'


def record_alert(sol_data: dict, tok: DetectedToken, spec: ChainSpec,
                 fee_eth: float | None = None,
                 tg_chat_id: str | int | None = None,
                 tg_message_id: int | None = None) -> None:
    heartbeat.beat("xchain_match")
    chain = spec.gmgn_slug or spec.name.lower()
    storage._schedule(outcomes.track(
        source=outcomes.SRC_XCHAIN_RBH if chain == "robinhood" else outcomes.SRC_XCHAIN_ETH,
        chain=chain, address=tok.address, symbol=tok.symbol,
        sol_symbol=sol_data.get("symbol"), dex=tok.dex,
        # Robinhood has no price aggregator, so its outcome is read off the
        # pool. That needs the pool's own identity, which is only known here at
        # detection — looking it up later costs extra calls and fails for v4,
        # whose pools have no address of their own.
        pair=tok.pair, pool_id=tok.pool_id, weth_is_token0=tok.weth_is_token0,
        # Where this alert landed in Telegram, so the result can be posted as a
        # reply to it rather than as a message with no context.
        tg_chat_id=str(tg_chat_id) if tg_chat_id else None,
        tg_message_id=tg_message_id,
    ))
    try:
        storage.save_alert_record({
            "token_symbol":    tok.symbol,
            "token_address":   tok.address,
            "chain":           spec.gmgn_slug or spec.name.lower(),
            "wallet_tag":      tok.dex,
            "dex":             tok.dex,
            "tx_hash":         tok.tx_hash,
            "fee_eth":         fee_eth,
            "sol_symbol":      sol_data.get("symbol"),
            "sol_address":     sol_data.get("address"),
            "sol_mcap_usd":    sol_data.get("mcap_usd"),
            "alert_timestamp": time.time(),
        })
    except Exception as exc:
        # Not debug: this is the only thing that puts a fired match on the
        # dashboard. A silent failure here is how a NameError in the storage
        # layer went unnoticed while alerts kept reaching Telegram.
        log.error(f"record_alert failed — match not saved to the dashboard: {exc}")


async def send_telegram(
    session: aiohttp.ClientSession, chat_id, text: str, tag: str = "XCHAIN",
    buttons: dict | None = None,
) -> int | None:
    """Send an alert. Returns Telegram's message id, or None if it did not send.

    The id is what lets the outcome tracker reply to this very message later
    with how the call actually did, instead of the result only existing on a
    dashboard nobody has open. Callers that just check truthiness still work.
    """
    if not config.TELEGRAM_ENABLED:
        log.info(f"[DRY-RUN] {tag} alert (Telegram disabled) → chat {chat_id}")
        return None
    url = f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = buttons
    try:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                log.info(f"[{tag}] ✅ Alert sent → {chat_id}")
                body = await resp.json(content_type=None)
                return ((body or {}).get("result") or {}).get("message_id")
            body = await resp.text()
            log.error(f"[{tag}] Telegram error {resp.status}: {body[:200]}")
            return None
    except Exception as exc:
        log.error(f"[{tag}] Send error: {exc}")
        return None


def format_immediate_lean_alert(
    sol_data: dict, tok: DetectedToken, spec: ChainSpec
) -> str:
    """A cross-chain match, in the house style (see app/tgstyle.py).

    Two tokens in one message, so they get a block each rather than three facts
    to a line. The destination address is the one that gets acted on, so it is
    the one alone at the bottom; the SOL side is context and keeps its address
    inline.
    """
    from app import tgstyle

    expires_at = sol_data.get("expires_at", 0)
    remaining = max(0, int((expires_at - time.time()) / 60))
    lp_display = _launchpad_display(sol_data.get("launchpad") or "")

    lines = [
        f"🟣 <b>on Solana</b> · {tgstyle.esc(lp_display)}",
        f"   {tgstyle.usd(sol_data['mcap_usd'])} mcap · "
        f"{sol_data['fees_sol']:.3f} SOL fees · {sol_data['holders']} holders",
        f"   <code>{tgstyle.esc(sol_data['address'])}</code>",
        "",
        f"🔵 <b>now on {tgstyle.esc(spec.name)}</b> · "
        f"{tgstyle.esc(_DEX_LABEL.get(tok.dex, tok.dex.upper()))} pair",
        f"   watch has {remaining}m left",
    ]
    return tgstyle.card(
        icon="⚡", kind="CROSS-CHAIN MATCH",
        when=sol_data.get("triggered_at"),
        symbol=sol_data["symbol"], name=tok.name or "",
        lines=lines, address=tok.address)