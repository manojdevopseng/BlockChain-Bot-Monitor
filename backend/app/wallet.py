"""What an address actually holds, read from the chain itself.

Watch-only, and that is the whole design. An address is enough to read a
balance; spending needs a private key, and this app never asks for one — the
same reason the trading engine records instead of executing. Paste an address
and you can see it. Nothing here can move anything.

The balance comes from the chain, not from an aggregator. `eth_getBalance` is
one call and it is the truth; an indexer's answer is a copy of that truth with
a delay and nobody to ask when it disagrees. GMGN was considered and does not
answer this question at all — its API serves tokens and pairs.

Addresses are grouped by keyspace, not by chain. One EVM address covers
Robinhood, Ethereum, BNB and Base, because all four derive addresses the same
way — the string is asked of each and usually holds a balance on only one.
Solana and Tron are separate keyspaces and get their own, rather than being
guessed at from an EVM address that cannot exist there.

Tron is the odd one: it answers `eth_getBalance` like an EVM chain, but its
addresses are base58 and its coin has six decimals rather than eighteen. Read
as wei, a wallet holding a thousand TRX comes back as zero — and no error is
raised anywhere, which is what makes it worth naming here.

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
from .config import settings
from .scanners import scfg
from .scanners.slog import get_logger

log = get_logger(__name__)

# Wei/lamports per unit, and what to call the coin once it is divided.
_EVM_DECIMALS = 10 ** 18
_SOL_DECIMALS = 10 ** 9

# Wrapped natives. Solana's and Tron's native balances have no contract of
# their own, so the dollar price is taken from the wrapped token, which is the
# same asset.
_WSOL = "So11111111111111111111111111111111111111112"
_WTRX = "TNUC9Qb1rRpS5CbWLmNMxXBjyFoydXjWFR"

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Base58 has no 0, O, I or l — a typo that turns one of those up is caught
# here rather than by an RPC returning a confusing error.
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# Tron addresses are base58 too, but always 34 characters and always starting
# with T — a Solana mint pasted into the Tron box is caught by the shape.
_TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


def _tron_to_hex(addr: str) -> str:
    """Tron base58 -> the 0x form its JSON-RPC expects.

    A Tron address is a 0x41 prefix, twenty bytes of body, and a four-byte
    checksum, all base58-encoded. The EVM-compatible RPC wants the body alone.
    """
    from .wallets import _b58decode
    raw = _b58decode(addr)
    if len(raw) < 25:
        raise ValueError("not a Tron address")
    return "0x" + raw[1:-4].hex()


def chains() -> list[dict]:
    """Every chain, in the order the strip shows them.

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
        # Tron answers eth_getBalance like an EVM chain — but in sun, which is
        # six decimals, not eighteen. Divided as wei, a wallet holding a
        # thousand TRX reads as zero and nothing anywhere raises an error.
        {"id": "tron", "label": "Tron",      "symbol": "TRX",
         "http": settings.tron_rpc_http, "wrapped": _WTRX,
         "decimals": 10 ** 6, "base58": True},
    ]


# The three address shapes this deals in, and how to describe a bad one. A
# table rather than a chain of ifs, because every new chain belongs to one of
# these and the message should name the shape it expected.
KINDS = ("evm", "sol", "tron")
_SHAPES = {
    "evm":  (_EVM_RE,  "an EVM address — 0x followed by 40 hex characters"),
    "sol":  (_SOL_RE,  "a Solana address"),
    "tron": (_TRON_RE, "a Tron address — base58 beginning with T"),
}


def valid_one(kind: str, address: str) -> str:
    """One address, cleaned. Raises ValueError saying what was expected.

    Blank is allowed and means "not set" — somebody with only a Solana wallet
    should not have to invent an EVM address to get past the form.
    """
    kind = (kind or "").strip().lower()
    address = (address or "").strip()
    if kind not in _SHAPES:
        raise ValueError(f"{kind or 'that'} is not an address kind this knows")
    if not address:
        return ""
    shape, described = _SHAPES[kind]
    if not shape.match(address):
        raise ValueError(f"That does not look like {described}.")
    # Shape is not enough between the two base58 chains: a Tron address is 34
    # characters of base58 and so are plenty of Solana ones. What separates
    # them is what they decode to — a Solana address is exactly a 32-byte
    # public key, a Tron address is 25 bytes of prefix, body and checksum.
    if kind == "sol":
        from .wallets import _b58decode
        try:
            if len(_b58decode(address)) != 32:
                raise ValueError
        except ValueError:
            raise ValueError("That is base58 but not a Solana address — a "
                             "Solana address decodes to a 32-byte key.")
    return address


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
    if spec.get("base58"):
        addr = _tron_to_hex(addr)
    res = await _rpc(session, spec["http"], "eth_getBalance", [addr, "latest"])
    return int(str(res or "0x0"), 16) / spec.get("decimals", _EVM_DECIMALS)


def _kind_of(chain: str) -> str:
    """Which keyspace a chain's addresses come from."""
    return chain if chain in ("sol", "tron") else "evm"


# What to call that keyspace when telling somebody they have not linked one.
# The chain's own name would be wrong: one EVM wallet serves four of them.
_KIND_LABEL = {"evm": "EVM", "sol": "Solana", "tron": "Tron"}


async def _one(session: aiohttp.ClientSession, spec: dict,
               by_kind: dict) -> dict:
    """One chain's answer across every linked wallet, or its own reason.

    Summed rather than listed: this is the strip, and the strip answers "how
    much do I have on Base". Which wallet holds it is the Portfolio's
    question, not this one's.
    """
    out = {"chain": spec["id"], "label": spec["label"], "symbol": spec["symbol"],
           "balance": None, "usd": None, "price": None, "why": "",
           "wallets": 0}
    kind = _kind_of(spec["id"])
    addrs = by_kind.get(kind, [])
    if not addrs:
        out["why"] = f"no {_KIND_LABEL[kind]} wallet linked"
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


async def read(by_kind: dict) -> dict:
    """Every chain at once. Never raises — each chip carries its own bad news.

    Takes {kind: [addresses]} rather than one argument per keyspace, so adding
    a chain is a row in the table above and nothing else.
    """
    specs = chains()
    by_kind = {k: list(v or []) for k, v in (by_kind or {}).items()}
    async with aiohttp.ClientSession() as session:
        rows = await asyncio.gather(
            *(_one(session, s, by_kind) for s in specs))
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
            "addresses": by_kind,
            "linked": sum(len(v) for v in by_kind.values()),
            # Said in the payload, not just in the UI copy, so anything that
            # grows around this API inherits it.
            "watch_only": True}
