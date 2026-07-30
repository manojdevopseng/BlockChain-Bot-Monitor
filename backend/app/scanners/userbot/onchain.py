"""On-chain reads the userbot needs, over rotating RPC endpoint pools.

Everything here answers one question: "is this address a real thing on chain,
and what is it called?" — asked of an ETH-format or Solana address that turned
up in a premium Telegram group. It has nothing to do with the on-chain
*discovery* scanners; those ride their own WebSocket pools.

Mixed into TelegramForwarder rather than being a standalone client, because the
pools and the shared aiohttp session live on the forwarder instance.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp

from app.scanners.wss_pool import EndpointPool, host_of

from .common import ETH_RPCS, log


def addr_from_word(hex_result: Optional[str]) -> Optional[str]:
    if not hex_result:
        return None
    h = hex_result[2:] if hex_result.startswith("0x") else hex_result
    if len(h) < 64:
        return None
    a = "0x" + h[-40:].lower()
    return None if a == "0x" + "0" * 40 else a


def decode_symbol(hex_result: str) -> str:
    try:
        raw = hex_result[2:] if hex_result.startswith("0x") else hex_result
        if len(raw) < 64:
            return ""
        if len(raw) >= 128:
            str_length = int(raw[64:128], 16)
            if str_length == 0 or str_length > 100:
                raise ValueError("invalid length")
            str_hex = raw[128:128 + str_length * 2]
            return bytes.fromhex(str_hex).decode("utf-8", errors="replace").strip()
        return bytes.fromhex(raw[:64]).rstrip(b"\x00").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


# A contract answering "execution reverted" is a working endpoint giving a
# valid answer: the method simply is not on that contract. Only infrastructure
# problems — rate limits, auth, a dead host — are reasons to rotate.
#
# geth reports an execution error as JSON-RPC code 3. Treating it as an endpoint
# failure meant every plain token address (token0() does not exist on one, so it
# reverts) rotated the whole pool, reported "failed on every endpoint" and was
# then dropped — which is exactly what stopped a real Robinhood token being
# recorded on 2026-07-30.
REVERTED = object()

_EXECUTION_CODES = {3, -32015}
_EXECUTION_WORDS = ("execution reverted", "revert", "invalid opcode",
                    "out of gas", "always failing transaction")


def _is_execution_error(err: dict) -> bool:
    """A contract-level answer rather than an endpoint problem."""
    if not isinstance(err, dict):
        return False
    if err.get("code") in _EXECUTION_CODES:
        return True
    return any(w in str(err.get("message", "")).lower() for w in _EXECUTION_WORDS)


class OnChainMixin:
    """RPC calls over an EndpointPool, with rotation and exhaustion alerting."""

    async def _pooled_rpc(self, pool: EndpointPool, tag: str, method: str, params: list):
        """JSON-RPC call rotating across `pool`'s endpoints on a rejection.

        Three outcomes, and the caller must tell them apart:

            <result>  the call succeeded
            REVERTED  the endpoint answered, and the contract rejected the
                      call — a valid answer meaning "this method is not on
                      this contract", not a reason to rotate
            None      every endpoint failed; nothing was learned

        Before None was distinguished from a real answer, a rate-limited
        Alchemy key silently dropped every address seen in premium groups.
        Before REVERTED was distinguished from a failure, the opposite bug:
        an ordinary token reverting token0() rotated the whole pool and was
        dropped as if the infrastructure were down.

        Shared by ETH, Robinhood and SOL's premium-caller HTTP checks — same
        rotation, same "alert once when the whole pool is down" behaviour via
        _maybe_alert_rpc (which reaches ALERT_CHAT_ID), instead of each having
        its own slightly different copy.
        """
        attempts = max(1, len(pool.urls()))
        last = "no endpoint configured"
        for _ in range(attempts):
            url = pool.current()
            if not url:
                break
            host = host_of(url)
            try:
                async with self._http.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=aiohttp.ClientTimeout(total=6),
                ) as resp:
                    status = resp.status
                    body = await resp.json(content_type=None)
            except Exception as exc:
                kind, detail = pool.note_failure(url, exc)
                log.warning(f"[{tag}] {method} on {host} failed: {type(exc).__name__}: {exc}")
                last = f"{host}: {detail}"
                await self._maybe_alert_rpc(pool, tag)
                pool.rotate(f"{kind} on {host}")
                continue
            err = (body or {}).get("error")
            if err and _is_execution_error(err):
                # The endpoint worked. Do not rotate, do not mark it bad.
                pool.note_success(url)
                return REVERTED
            if status != 200 or err:
                fail = status if status != 200 else RuntimeError(str(err))
                kind, detail = pool.note_failure(url, fail)
                log.warning(f"[{tag}] {method} on {host} -> {detail}")
                last = f"{host}: {detail}"
                await self._maybe_alert_rpc(pool, tag)
                pool.rotate(f"{kind} on {host}")
                continue
            pool.note_success(url)
            return body.get("result")
        log.warning(f"[{tag}] {method} failed on every endpoint — {last}")
        return None

    async def _maybe_alert_rpc(self, pool: EndpointPool, tag: str) -> None:
        """Alert ALERT_CHAT_ID when the whole pool is refusing. Throttled by
        the pool itself, same as the WSS providers — one message, not one per
        rejected call."""
        if not pool.should_alert():
            return
        body = pool.alert_text()
        log.warning(f"[{tag}] ALL RPC ENDPOINTS EXHAUSTED — {body.splitlines()[0]}")
        try:
            from ... import notifier
            await notifier.notify_rpc_exhausted(pool.label, body)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[{tag}] could not send exhaustion alert: {exc}")

    async def _eth_call(self, pool: EndpointPool, tag: str, addr: str,
                        data: Optional[str], method: str = "eth_call"):
        params = [{"to": addr, "data": data}, "latest"] if method == "eth_call" else [addr, "latest"]
        return await self._pooled_rpc(pool, tag, method, params)

    async def _resolve_token(self, pool: EndpointPool, tag: str, addr: str, bases: set):
        """Work out which side of a pair is the token. (token, pair) on a pair;
        (addr, None) when the address is the token itself; (None, None) only
        when nothing could be learned."""
        t0, t1 = await asyncio.gather(
            self._eth_call(pool, tag, addr, "0x0dfe1681"),   # token0()
            self._eth_call(pool, tag, addr, "0xd21220a7"),   # token1()
        )
        # Reverting token0()/token1() is how a plain token answers — it has no
        # such methods. That makes the address itself the token, which is the
        # normal case for something posted in a premium group.
        if t0 is REVERTED or t1 is REVERTED:
            return addr, None
        if t0 is None or t1 is None:
            return None, None   # every endpoint failed — don't guess
        a0, a1 = addr_from_word(t0), addr_from_word(t1)
        if not a0 or not a1:
            return addr, None
        if a0 in bases and a1 not in bases:
            return a1, addr
        if a1 in bases and a0 not in bases:
            return a0, addr
        return (a1 if a0 in bases else a0), addr

    async def _sol_rpc(self, method: str, params: list):
        """JSON-RPC call over the SOL premium-check pool (Alchemy #1/#2).

        Thin wrapper over the shared _pooled_rpc — same rotation, same
        "alert ALERT_CHAT_ID once the whole pool is down" behaviour as the
        ETH/RBH premium checks, instead of its own near-identical copy.
        """
        return await self._pooled_rpc(self._sol_http_pool, "SOL-HTTP", method, params)

    async def _sol_token_info(self, address: str) -> dict:
        """Best-effort symbol/name via GMGN web quotation API (no key needed)."""
        try:
            url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{address}"
            async with self._http.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                data = await resp.json(content_type=None)
            d = (data.get("data") or {})
            tok = d.get("token") or d
            return {"symbol": tok.get("symbol") or "", "name": tok.get("name") or ""}
        except Exception:
            return {}

    async def _fetch_token_symbol(self, address: str) -> str:
        """Symbol for a premium-caller alert, off free public endpoints.

        Deliberately not on the paid pool: this is cosmetic (it decorates the
        forwarded message) and must never be the thing that burns a quota the
        detection checks need.
        """
        if not self._http:
            return ""
        payload = {"jsonrpc": "2.0", "method": "eth_call",
                   "params": [{"to": address, "data": "0x95d89b41"}, "latest"], "id": 1}
        for rpc in ETH_RPCS:
            try:
                async with self._http.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json(content_type=None)
                hex_result = data.get("result", "")
                if hex_result and hex_result != "0x":
                    return decode_symbol(hex_result)
            except Exception:
                continue
        return ""
