"""Premium-caller address capture — the ETH / RBH / SOL dashboard panels.

An address seen in a premium Telegram group is verified on chain first (is it a
real contract / a real account?) and only then recorded, so the panels do not
fill with random base58 or hex noise that happened to match a regex.

Each chain has its own Settings toggle (Premium ETH / Premium RBH / Premium
SOL) and its own RPC endpoint pool, so one chain's quota running out cannot
stop the others.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

from app import outcomes
from app.scanners import scfg as config
from app.scanners.wss_pool import EndpointPool

from .common import log
from .onchain import decode_symbol
from .store import col


class PremiumCaptureMixin:
    """`_capture_premium_eth` / `_capture_premium_sol`, called from the premium
    handler once an address has been spotted in a watched group."""

    async def _capture_premium_eth(self, addr: str, chat_id: int, group: str, text: str,
                                   username: Optional[str] = None, msg_id: Optional[int] = None,
                                   check_eth: bool = True, check_rbh: bool = True) -> None:
        """Confirm an ETH-format address seen in a premium group and, per
        chain it turns out to be a real contract on, record it in the SOL-panel
        style dashboard sections ("ETH/RBH Address Detected From Premium
        Caller"). `check_eth`/`check_rbh` are the Settings → Bots → Premium
        ETH / Premium RBH toggles — either can be switched off without
        touching the other or the separate premium-caller-forward feature."""
        if not self._http:
            return
        keyword = await self._match_keywords(text)
        native0 = "0x" + "0" * 40
        eth_bases = {
            config.ETH_WETH.lower(),
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "0x6b175474e89094c44da98b954eedeac495271d0f",
            native0,
        }
        rbh_bases = {config.RBH_WETH.lower(), "0x5fc5360d0400a0fd4f2af552add042d716f1d168", native0}

        async def check_chain(label: str, chain: str, pool: EndpointPool, bases: set) -> None:
            try:
                if not pool.urls():
                    return
                code = await self._eth_call(pool, f"PREMIUM-{label}", addr, None, "eth_getCode")
                if code is None:
                    return  # every endpoint failed — already logged/alerted, nothing to conclude
                if code == "0x":
                    return  # confirmed: not a contract at this address
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
                    entries = [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}] + entries
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
                    return
                name_hex, sym_hex = await asyncio.gather(
                    self._eth_call(pool, f"PREMIUM-{label}", token_addr, "0x06fdde03"),
                    self._eth_call(pool, f"PREMIUM-{label}", token_addr, "0x95d89b41"),
                )
                record = {
                    "chain": chain,
                    "symbol": decode_symbol(sym_hex or ""),
                    "name": decode_symbol(name_hex or ""),
                    "address": token_addr,
                    "pair": pair_addr,
                    "group_entries": [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}],
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
                    chain={"ETH": "eth", "RBH": "robinhood", "SOL": "sol"}.get(label, "eth"),
                    address=token_addr, symbol=record.get("symbol") or "",
                    groups=[group], keyword=keyword,
                )
                from ...ws_hub import hub
                await hub.broadcast("premium_detection", {k: v for k, v in record.items() if k != "_id"})
                log.info(f"[PREMIUM-{label}] Captured {record['symbol'] or token_addr[:10]} from {group} | "
                         + (f"{keyword} Matched" if keyword else "Not Matched"))
            except Exception as exc:
                # Was log.debug — invisible even on the Logs page (below its
                # INFO floor). An RPC rejection is already handled (rotated,
                # eventually alerted) inside _eth_call/_pooled_rpc; this catch
                # is for anything else that goes wrong in between, and it
                # should not vanish without a trace either.
                log.warning(f"[PREMIUM-{label}] capture failed for {addr[:10]}: "
                           f"{type(exc).__name__}: {exc}")

        tasks = []
        if check_eth:
            tasks.append(check_chain("ETH", "eth", self._eth_http_pool, eth_bases))
        if check_rbh:
            tasks.append(check_chain("RBH", "rbh", self._rbh_http_pool, rbh_bases))
        if tasks:
            await asyncio.gather(*tasks)

    async def _capture_premium_sol(self, addr: str, chat_id: int, group: str, text: str,
                                   username: Optional[str] = None, msg_id: Optional[int] = None) -> None:
        """Capture a Solana address seen in a premium group into the SOL panel.
        Dormant unless a SOL HTTP endpoint is set (mirrors the ETH/RBH
        behaviour); the RPC getAccountInfo check filters out random base58
        noise. _sol_rpc already rotates and logs on failure, so a None here
        means "not a real account" and "every endpoint failed" alike — either
        way there is nothing safe to capture."""
        if not self._http or not config.SOL_HTTP_ENDPOINTS:
            return
        info = await self._sol_rpc("getAccountInfo", [addr, {"encoding": "base64"}])
        if not info or info.get("value") is None:
            return

        keyword = await self._match_keywords(text)
        detections = col("premium_detections")
        existing = await detections.find_one({"chain": "sol", "address": addr})
        if existing:
            entries = existing.get("group_entries") or []
            if any(e.get("chat_id") == chat_id for e in entries):
                return
            entries = [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}] + entries
            await detections.update_one({"_id": existing["_id"]}, {"$set": {
                "group_entries": entries, "groups": [e["name"] for e in entries],
                "group_ids": [e["chat_id"] for e in entries], "count": len(entries),
                "ts": time.time(), "keyword": existing.get("keyword") or keyword}})
            log.info(f"[PREMIUM-SOL] {existing.get('symbol') or addr[:10]} shill count → {len(entries)} (from {group})")
            return

        meta = await self._sol_token_info(addr)
        record = {
            "chain": "sol", "symbol": meta.get("symbol", ""), "name": meta.get("name", ""),
            "address": addr, "pair": None,
            "group_entries": [{"chat_id": chat_id, "name": group, "username": username, "message_id": msg_id}],
            "groups": [group], "group_ids": [chat_id], "count": 1, "chat_id": chat_id,
            "keyword": keyword, "ts": time.time(),
        }
        await detections.insert_one(record)
        from ...ws_hub import hub
        await hub.broadcast("premium_detection", {k: v for k, v in record.items() if k != "_id"})
        log.info(f"[PREMIUM-SOL] Captured {record['symbol'] or addr[:10]} from {group} | "
                 + (f"{keyword} Matched" if keyword else "Not Matched"))
