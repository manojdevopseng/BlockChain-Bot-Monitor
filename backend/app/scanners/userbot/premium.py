"""Premium-caller address detection — the ETH / RBH / SOL dashboard panels.

An address seen in a premium Telegram group is verified on chain first (is it a
real contract / a real account?) and only then recorded, so the panels do not
fill with random base58 or hex noise that happened to match a regex.

Each chain has its own Settings toggle (Premium ETH / Premium RBH / Premium
SOL) and its own RPC endpoint pool, so one chain's quota running out cannot
stop the others.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Optional

from app import notifier, outcomes
from app.scanners import scfg as config
from app.scanners.wss_pool import EndpointPool
from app.util import gmgn_url, tg_message_url

from .common import GATE_PREMIUM, log
from .onchain import REVERTED, decode_symbol
from .store import col

# Messages sent per address per chain. The first sighting and the first repeat
# are the signal; after that the shill count keeps rising in the panel and the
# group stays quiet. Same 2 the old ETH-only forward used, so switching that
# chat from the userbot to the bot does not change its volume.
CALL_CAP = 2


class PremiumCaptureMixin:
    """`_record_eth_detection` / `_record_sol_detection`, called from the premium
    handler once an address has been spotted in a watched group."""

    async def _announce_detection(self, chain: str, address: str, symbol: str,
                                  group: str, calls: int, post_url: str = "") -> None:
        """Post a detection to its own chain's Telegram group.

        Sent by the BOT, not the userbot. The bot is not a member of the
        premium source groups — that is the whole reason a Telethon session
        exists — so it cannot forward the original message and composes one
        carrying the same fields instead.

        Routing is by chain, so an address that turns out to be live on two
        chains is announced in both groups and never in the wrong one. A chain
        with no chat id in .env is simply not announced; its panel rows are
        unaffected.
        """
        if calls > CALL_CAP or not self._on(GATE_PREMIUM):
            return
        chat_id = config.DEST_PREMIUM_BY_CHAIN.get(chain)
        if not chat_id:
            return
        label = chain.upper()
        group = group or "Unknown"
        # The address is the one thing that gets acted on, so it sits alone on
        # its own line in <code> — a tap copies it whole. Everything above it
        # is context. The links are inline buttons rather than text in the
        # body, where they rendered as bare URLs trailing under the message.
        text = (
            f"🎯 <b>{label} PREMIUM SIGNAL</b>\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            + (f"🪙 <b>{html.escape(symbol)}</b>\n" if symbol else "")
            + f"📢 {html.escape(group)}\n"
            f"📞 Call {calls} of {CALL_CAP}\n\n"
            f"<code>{html.escape(address)}</code>"
        )
        # "View on Telegram" opens the exact post the address was seen in.
        # Dropped when that post cannot be linked to — a plain (non-super)
        # group has no message links — rather than shipping a dead button.
        buttons = [b for b in (
            ("📊 GMGN", gmgn_url(chain, address)),
            ("💬 View on Telegram", post_url),
        ) if b[1]]
        if await notifier.send_to(chat_id, text, buttons=buttons or None):
            self.count_premium += 1
            log.info(f"[PREMIUM] [{group}] {symbol or address[:10]} -> {label} group ({calls}/{CALL_CAP})")
        else:
            # send_to never raises, so a failure would otherwise be invisible —
            # and "the group went quiet" is exactly the symptom worth naming.
            log.warning(f"[PREMIUM] {label} signal not delivered to {chat_id} for {address[:10]}")

    async def _record_eth_detection(self, addr: str, chat_id: int, group: str, text: str,
                                   username: Optional[str] = None, msg_id: Optional[int] = None,
                                   check_eth: bool = True, check_rbh: bool = True,
                                   check_bnb: bool = True, raw_chat_id=None) -> None:
        """Confirm an 0x-format address seen in a premium group and, per chain
        it turns out to be a real contract on, record it in the Detections
        panel under that chain's filter.

        The same address is checked against every enabled chain, because an
        0x address does not say which chain it belongs to — the same string can
        be a live contract on Ethereum, BSC and Robinhood at once, and each is
        a separate finding. `check_*` are the Settings → Bots → Premium ETH /
        RBH / BNB toggles; any can be switched off without touching the others
        or the separate premium-caller-forward feature."""
        if not self._http:
            return
        keyword = await self._match_keywords(text)
        entry = {"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}
        post_url = tg_message_url(raw_chat_id, msg_id, username)
        native0 = "0x" + "0" * 40
        eth_bases = {
            config.ETH_WETH.lower(),
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "0x6b175474e89094c44da98b954eedeac495271d0f",
            native0,
        }
        rbh_bases = {config.RBH_WETH.lower(), "0x5fc5360d0400a0fd4f2af552add042d716f1d168", native0}
        # WBNB plus the stables a BSC pair is usually quoted in (BSC-USD, BUSD,
        # USDC), so _resolve_token picks the token side rather than the base.
        bnb_bases = {
            (config.BNB_WBNB or "").lower(),
            "0x55d398326f99059ff775485246999027b3197955",   # BSC-USD (USDT)
            "0xe9e7cea3dedca5984780bafc599bd69add087d56",   # BUSD
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",   # USDC
            native0,
        }

        async def check_chain(label: str, chain: str, pool: EndpointPool, bases: set) -> None:
            try:
                if not pool.urls():
                    return
                code = await self._eth_call(pool, f"PREMIUM-{label}", addr, None, "eth_getCode")
                if code is None:
                    return  # every endpoint failed — already logged/alerted, nothing to conclude
                if code is REVERTED or code == "0x":
                    return  # confirmed: no contract at this address on this chain
                token_addr, pair_addr = await self._resolve_token(pool, f"PREMIUM-{label}", addr, bases)
                if token_addr is None:
                    return  # resolve calls failed on every endpoint
                token_l = token_addr.lower()
                existing = await col("premium_detections").find_one(
                    {"chain": chain, "address": {"$regex": f"^{re.escape(token_l)}$", "$options": "i"}}
                )
                if existing:
                    entries = existing.get("group_entries") or []
                    if any(e.get("chat_id") == chat_id for e in entries):
                        return
                    entries = [entry] + entries
                    await col("premium_detections").update_one(
                        {"_id": existing["_id"]},
                        {"$set": {
                            "group_entries": entries,
                            "groups": [e["name"] for e in entries],
                            "group_ids": [e["chat_id"] for e in entries],
                            "count": len(entries),
                            "ts": time.time(),
                            "keyword": existing.get("keyword") or keyword,
                        }},
                    )
                    log.info(f"[PREMIUM-{label}] {existing.get('symbol') or token_addr[:10]} shill count → {len(entries)} (from {group})")
                    await self._announce_detection(chain, token_addr, existing.get("symbol") or "",
                                                   group, len(entries), post_url)
                    return
                # A token that implements neither name() nor symbol() is
                # unusual but legal, and reverting them is not a reason to
                # throw the detection away — the address is what matters.
                name_hex, sym_hex = await asyncio.gather(
                    self._eth_call(pool, f"PREMIUM-{label}", token_addr, "0x06fdde03"),
                    self._eth_call(pool, f"PREMIUM-{label}", token_addr, "0x95d89b41"),
                )
                name_hex = "" if name_hex in (None, REVERTED) else name_hex
                sym_hex = "" if sym_hex in (None, REVERTED) else sym_hex
                record = {
                    "chain": chain,
                    "symbol": decode_symbol(sym_hex),
                    "name": decode_symbol(name_hex),
                    "address": token_addr,
                    "pair": pair_addr,
                    "group_entries": [entry],
                    "groups": [group],
                    "group_ids": [chat_id],
                    "count": 1,
                    "chat_id": chat_id,
                    "keyword": keyword,
                    "ts": time.time(),
                }
                await col("premium_detections").insert_one(record)
                # Follow it forward, tagged with the calling group, so the
                # Forwarder page can rank groups by how their calls did.
                await outcomes.track(
                    source=outcomes.SRC_PREMIUM,
                    chain={"ETH": "eth", "RBH": "robinhood", "BNB": "bnb",
                           "SOL": "sol"}.get(label, "eth"),
                    address=token_addr, symbol=record.get("symbol") or "",
                    groups=[group], keyword=keyword,
                )
                from ...ws_hub import hub
                await hub.broadcast("premium_detection", {k: v for k, v in record.items() if k != "_id"})
                log.info(f"[PREMIUM-{label}] Captured {record['symbol'] or token_addr[:10]} from {group} | "
                         + (f"{keyword} Matched" if keyword else "Not Matched"))
                await self._announce_detection(chain, token_addr, record["symbol"], group, 1, post_url)
            except Exception as exc:
                # Was log.debug — invisible even on the Logs page (below its
                # INFO floor). An RPC rejection is already handled (rotated,
                # eventually alerted) inside _eth_call/_pooled_rpc; this catch
                # is for anything else that goes wrong in between, and it
                # should not vanish without a trace either.
                log.warning(f"[PREMIUM-{label}] detection failed for {addr[:10]}: "
                           f"{type(exc).__name__}: {exc}")

        tasks = []
        if check_eth:
            tasks.append(check_chain("ETH", "eth", self._eth_http_pool, eth_bases))
        if check_rbh:
            tasks.append(check_chain("RBH", "rbh", self._rbh_http_pool, rbh_bases))
        if check_bnb:
            tasks.append(check_chain("BNB", "bnb", self._bnb_http_pool, bnb_bases))
        if tasks:
            await asyncio.gather(*tasks)

    async def _record_sol_detection(self, addr: str, chat_id: int, group: str, text: str,
                                   username: Optional[str] = None, msg_id: Optional[int] = None,
                                   raw_chat_id=None) -> None:
        """Capture a Solana address seen in a premium group into the SOL panel.
        Dormant unless a SOL HTTP endpoint is set (mirrors the ETH/RBH
        behaviour); the RPC getAccountInfo check filters out random base58
        noise. _sol_rpc already rotates and logs on failure, so a None here
        means "not a real account" and "every endpoint failed" alike — either
        way there is nothing safe to record."""
        if not self._http or not config.SOL_HTTP_ENDPOINTS:
            return
        info = await self._sol_rpc("getAccountInfo", [addr, {"encoding": "base64"}])
        if not info or info.get("value") is None:
            return

        keyword = await self._match_keywords(text)
        entry = {"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}
        post_url = tg_message_url(raw_chat_id, msg_id, username)
        detections = col("premium_detections")
        existing = await detections.find_one({"chain": "sol", "address": addr})
        if existing:
            entries = existing.get("group_entries") or []
            if any(e.get("chat_id") == chat_id for e in entries):
                return
            entries = [entry] + entries
            await detections.update_one({"_id": existing["_id"]}, {"$set": {
                "group_entries": entries, "groups": [e["name"] for e in entries],
                "group_ids": [e["chat_id"] for e in entries], "count": len(entries),
                "ts": time.time(), "keyword": existing.get("keyword") or keyword}})
            log.info(f"[PREMIUM-SOL] {existing.get('symbol') or addr[:10]} shill count → {len(entries)} (from {group})")
            await self._announce_detection("sol", addr, existing.get("symbol") or "",
                                          group, len(entries), post_url)
            return

        meta = await self._sol_token_info(addr)
        record = {
            "chain": "sol", "symbol": meta.get("symbol", ""), "name": meta.get("name", ""),
            "address": addr, "pair": None,
            "group_entries": [entry],
            "groups": [group], "group_ids": [chat_id], "count": 1, "chat_id": chat_id,
            "keyword": keyword, "ts": time.time(),
        }
        await detections.insert_one(record)
        from ...ws_hub import hub
        await hub.broadcast("premium_detection", {k: v for k, v in record.items() if k != "_id"})
        log.info(f"[PREMIUM-SOL] Captured {record['symbol'] or addr[:10]} from {group} | "
                 + (f"{keyword} Matched" if keyword else "Not Matched"))
        await self._announce_detection("sol", addr, record["symbol"], group, 1, post_url)
