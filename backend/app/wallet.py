"""What an address actually holds, read from the chain itself.

Watch-only, and that is the whole design. An address is enough to read a
balance; spending needs a private key, and this app never asks for one — the
same reason the trading engine records instead of executing. Paste an address
and you can see it. Nothing here can move anything.

The balance comes from the chain, not from an aggregator. `eth_getBalance` is
one call and it is the truth; an indexer's answer is a copy of that truth with
a delay and nobody to ask when it disagrees. GMGN was considered and does not
answer this question at all — its API serves tokens and pairs.

One EVM address covers four chains. Robinhood, Ethereum, BNB and Base all
derive addresses the same way, so the same string is asked of each of them and
usually holds a balance on only one. Solana is a different keyspace and gets
its own field rather than being guessed at.

Every chain is asked independently and answers for itself. One RPC being
rate-limited or unset turns one chip grey; it does not cost the others their
number, because "we could not reach Base" and "you have nothing on Base" are
different sentences and must never be shown as the same one.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp

from . import trading
from .scanners import scfg
from .scanners.slog import get_logger

log = get_logger(__name__)

# Wei/lamports per unit, and what to call the coin once it is divided.
_EVM_DECIMALS = 10 ** 18
_SOL_DECIMALS = 10 ** 9

# Wrapped SOL. Solana's native balance has no contract of its own, so the
# dollar price is taken from the wrapped mint, which is the same asset.
_WSOL = "So11111111111111111111111111111111111111112"

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Base58 has no 0, O, I or l — a typo that turns one of those up is caught
# here rather than by an RPC returning a confusing error.
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def chains() -> list[dict]:
    """The five, in the order the strip shows them.

    Endpoints are read at call time rather than at import: an operator who
    fills in a missing RPC and restarts nothing should still see it work.
    """
    return [
        {"id": "rbh",  "label": "Robinhood", "symbol": "ETH",
         "http": scfg.RBH_RPC_HTTP,  "wrapped": scfg.RBH_WETH},
        {"id": "eth",  "label": "Ethereum",  "symbol": "ETH",
         "http": scfg.ETH_RPC_HTTP,  "wrapped": scfg.ETH_WETH},
        {"id": "bnb",  "label": "BNB",       "symbol": "BNB",
         "http": scfg.BNB_RPC_HTTP,  "wrapped": scfg.BNB_WBNB},
        {"id": "base", "label": "Base",      "symbol": "ETH",
         "http": scfg.BASE_RPC_HTTP, "wrapped": scfg.BASE_WETH},
        {"id": "sol",  "label": "Solana",    "symbol": "SOL",
         "http": scfg.SOL_RPC_HTTP,  "wrapped": _WSOL},
    ]


def valid(evm: str, sol: str) -> tuple[str, str]:
    """The two addresses, cleaned. Raises ValueError naming the bad one.

    Blank is allowed and means "not set" — somebody with only a Solana wallet
    should not have to invent an EVM address to save the form.
    """
    evm, sol = (evm or "").strip(), (sol or "").strip()
    if evm and not _EVM_RE.match(evm):
        raise ValueError("That does not look like an EVM address — 0x and 40 "
                         "hex characters.")
    if sol and not _SOL_RE.match(sol):
        raise ValueError("That does not look like a Solana address.")
    return evm, sol


async def _rpc(session: aiohttp.ClientSession, url: str, method: str,
               params: list) -> Any:
    async with session.post(
            url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params},
            timeout=aiohttp.ClientTimeout(total=12)) as resp:
        body = await resp.json(content_type=None)
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(str((body["error"] or {}).get("message") or "refused"))
    return (body or {}).get("result")


async def _balance(session: aiohttp.ClientSession, spec: dict,
                   addr: str) -> float:
    """One address on one chain, in whole coins. Raises so the caller can say
    why rather than reporting a zero."""
    if spec["id"] == "sol":
        res = await _rpc(session, spec["http"], "getBalance", [addr])
        lamports = (res or {}).get("value") if isinstance(res, dict) else res
        return int(lamports or 0) / _SOL_DECIMALS
    res = await _rpc(session, spec["http"], "eth_getBalance", [addr, "latest"])
    return int(str(res or "0x0"), 16) / _EVM_DECIMALS


async def _one(session: aiohttp.ClientSession, spec: dict,
               evms: list, sols: list) -> dict:
    """One chain's answer across every linked wallet, or its own reason.

    Summed rather than listed: this is the strip, and the strip answers "how
    much do I have on Base". Which wallet holds it is the Portfolio's
    question, not this one's.
    """
    out = {"chain": spec["id"], "label": spec["label"], "symbol": spec["symbol"],
           "balance": None, "usd": None, "price": None, "why": "",
           "wallets": 0}
    addrs = sols if spec["id"] == "sol" else evms
    if not addrs:
        out["why"] = ("no Solana wallet linked" if spec["id"] == "sol"
                      else "no EVM wallet linked")
        return out
    if not spec["http"]:
        out["why"] = "no RPC endpoint configured for this chain"
        return out
    total, failed = 0.0, 0
    for addr in addrs:
        try:
            total += await _balance(session, spec, addr)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.debug(f"[WALLET] {spec['id']} {addr[:10]} failed: {exc}")
    if failed == len(addrs):
        # Named rather than shown as a zero. A wallet reported empty when the
        # endpoint was merely rate-limited is a lie the user cannot detect.
        out["why"] = "could not read this chain right now"
        return out
    if failed:
        # Some answered and some did not, so the total is real but short. Say
        # so — a number that is quietly missing a wallet is worse than no
        # number, because it looks complete.
        out["why"] = f"{failed} of {len(addrs)} wallets could not be read"
    out["balance"] = total
    out["wallets"] = len(addrs) - failed
    return out


async def read(evms: list, sols: list) -> dict:
    """Every chain at once. Never raises — each chip carries its own bad news."""
    specs = chains()
    evms, sols = list(evms or []), list(sols or [])
    async with aiohttp.ClientSession() as session:
        rows = await asyncio.gather(
            *(_one(session, s, evms, sols) for s in specs))
        rows = list(rows)

        # One DexScreener request for the wrapped natives, and only for the
        # chains that came back with something to value.
        wants = [(s["id"], s["wrapped"]) for s, r in zip(specs, rows)
                 if s["wrapped"] and r["balance"]]
        quotes = await trading.prices(session, wants) if wants else {}

    for spec, row in zip(specs, rows):
        px = quotes.get((spec["id"], trading._key(spec["id"], spec["wrapped"])))
        if px and row["balance"]:
            row["price"] = px
            row["usd"] = row["balance"] * px

    total = sum(r["usd"] or 0 for r in rows)
    return {"chains": rows, "total_usd": total,
            "evm": evms, "sol": sols, "linked": len(evms) + len(sols),
            # Said in the payload, not just in the UI copy, so anything that
            # grows around this API inherits it.
            "watch_only": True}
