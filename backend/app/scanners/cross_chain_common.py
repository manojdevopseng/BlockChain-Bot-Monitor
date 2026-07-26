"""Shared helpers for on-chain cross-chain flows (SOL→ETH, SOL→Robinhood).

Ported from the reference repo (core/cross_chain_common.py); only the imports
changed (storage → Mongo repo). Telegram HTML sender + immediate lean-alert
formatter are unchanged.
"""

from __future__ import annotations

import time
from typing import Optional

import aiohttp

from app.scanners.onchain_detector import ChainSpec, DetectedToken
from app.scanners.sol_scanner import _launchpad_display
from app.scanners import storage_repo as storage
from app.scanners import scfg as config
from app.scanners.slog import get_logger
from app import heartbeat

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"

_DEX_LABEL = {"v2": "Uniswap V2", "v3": "Uniswap V3", "v4": "Uniswap V4", "noxa": "Noxa"}


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _token_link(spec: ChainSpec, address: str) -> str:
    if spec.gmgn_slug:
        return f'🔗 <a href="https://gmgn.ai/{spec.gmgn_slug}/token/{address}">View on GMGN</a>'
    if spec.explorer_token_url:
        return f'🔗 <a href="{spec.explorer_token_url.format(addr=address)}">View on Explorer</a>'
    return f'🔗 <a href="https://dexscreener.com/search?q={address}">View on DexScreener</a>'


def record_alert(sol_data: dict, tok: DetectedToken, spec: ChainSpec,
                 fee_eth: float | None = None) -> None:
    heartbeat.beat("xchain_match")
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
    session: aiohttp.ClientSession, chat_id, text: str, tag: str = "XCHAIN"
) -> bool:
    if not config.TELEGRAM_ENABLED:
        log.info(f"[DRY-RUN] {tag} alert (Telegram disabled) → chat {chat_id}")
        return False
    url = f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                log.info(f"[{tag}] ✅ Alert sent → {chat_id}")
                return True
            body = await resp.text()
            log.error(f"[{tag}] Telegram error {resp.status}: {body[:200]}")
            return False
    except Exception as exc:
        log.error(f"[{tag}] Send error: {exc}")
        return False


def format_immediate_lean_alert(
    sol_data: dict, tok: DetectedToken, spec: ChainSpec
) -> str:
    symbol     = _esc(sol_data["symbol"])
    lp_display = _esc(_launchpad_display(sol_data.get("launchpad") or ""))
    sol_addr   = sol_data["address"]
    dst_addr   = tok.address
    dex_label  = _DEX_LABEL.get(tok.dex, tok.dex.upper())

    expires_at  = sol_data.get("expires_at", 0)
    remaining   = max(0, int((expires_at - time.time()) / 60))
    trigger_str = (
        time.strftime("%H:%M", time.localtime(sol_data["triggered_at"]))
        if sol_data.get("triggered_at") else "?"
    )

    dst_link = _token_link(spec, dst_addr)

    return (
        f"⚡ <b>CROSS-CHAIN MATCH — {symbol}</b>  <i>({spec.name})</i>\n"
        f"<i>On-chain new pair detected matching a watched SOL token!</i>\n\n"

        f"🟣 <b>SOL — {symbol}</b>  <i>via {lp_display}</i>\n"
        f"<b>Address:</b> <code>{sol_addr}</code>\n"
        f"<b>MCap:</b> ${sol_data['mcap_usd']:,.0f}  |  "
        f"<b>Fees:</b> {sol_data['fees_sol']:.3f} SOL\n"
        f"<b>Holders:</b> {sol_data['holders']}  |  "
        f"<b>Triggered:</b> {trigger_str}  |  "
        f"<b>Watch left:</b> {remaining}m\n"
        f'🔗 <a href="https://gmgn.ai/sol/token/{sol_addr}">View SOL on GMGN</a>\n\n'

        f"🔵 <b>{spec.name} — {_esc(tok.symbol)}</b>  <i>(new pair · {dex_label})</i>\n"
        f"<b>Name:</b> {_esc(tok.name)}\n"
        f"<b>Address:</b> <code>{dst_addr}</code>\n"
        f"{dst_link}"
    )
