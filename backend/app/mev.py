"""Where a transaction is sent so it is not front-run.

An ordinary swap is broadcast to a public mempool, where anybody can read it
before it is mined. On a chain with real bots that is an invitation: they buy
in front of you, let your buy move the price, and sell into it. The trade
still executes — at a worse price, every time, and the loss looks like
slippage rather than theft.

The defence is not to broadcast publicly at all. Ethereum has Flashbots
Protect, BNB Chain has private relays, and Solana has Jito's block engine.
Each takes the signed transaction straight to whoever builds the block, so
there is no window in which it can be read and raced.

It is not equally meaningful everywhere, and this refuses to pretend it is.
An OP-stack style chain orders transactions through a single sequencer rather
than a public mempool, so there is nothing to watch and nothing to route
around — the honest answer there is "not applicable", not a switch that
implies a protection it is not providing.

Nothing here signs or sends. This resolves *where* a transaction would go and
proves the endpoint answers; the sending belongs to whatever eventually holds
a key, which today is nothing.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp

from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)


# Per chain: the relay's name, its endpoint, and how its health is asked.
# Endpoints are overridable from .env so a paid relay can replace a public one
# without a code change.
def routes() -> dict[str, dict]:
    return {
        "eth": {
            "name": "Flashbots Protect",
            "url": getattr(settings, "eth_mev_rpc", "") or "https://rpc.flashbots.net/fast",
            "probe": "evm",
            "note": "Sent straight to block builders, never to the public mempool.",
        },
        "bnb": {
            "name": "48Club Private RPC",
            "url": getattr(settings, "bnb_mev_rpc", "") or "https://rpc.48.club",
            "probe": "evm",
            "note": "A private relay to BNB Chain validators.",
        },
        "sol": {
            "name": "Jito Block Engine",
            "url": getattr(settings, "sol_mev_rpc", "") or "https://mainnet.block-engine.jito.wtf/api/v1/transactions",
            "probe": "none",
            "note": "Bundled to a Jito leader instead of the public gossip path.",
        },
        # Base orders through a single sequencer, so there is no public mempool
        # for anybody to watch. Left configurable in case that changes, and
        # reported as not-applicable rather than as a switch that does nothing.
        "base": {
            "name": "", "url": getattr(settings, "base_mev_rpc", "") or "",
            "probe": "evm",
            "note": "Base orders through one sequencer — there is no public "
                    "mempool to be front-run in.",
        },
        # Robinhood Chain is new and its mempool behaviour is not something to
        # assert without checking. No endpoint is assumed; set RBH_MEV_RPC and
        # it appears here with everything else.
        "rbh": {
            "name": "", "url": getattr(settings, "rbh_mev_rpc", "") or "",
            "probe": "evm",
            "note": "No protected endpoint is configured for this chain.",
        },
    }


def endpoint(chain: str) -> str:
    """Where a protected transaction for this chain would go. "" when none."""
    return (routes().get((chain or "").lower(), {}) or {}).get("url", "")


def available(chain: str) -> bool:
    return bool(endpoint(chain))


async def _probe(session: aiohttp.ClientSession, spec: dict) -> dict:
    """Does the relay answer? Asked rather than assumed.

    A toggle that says "protected" while the endpoint is unreachable is worse
    than no toggle, because the trade still goes out — through the ordinary
    path, unprotected, with the switch showing green.
    """
    url = spec.get("url") or ""
    if not url:
        return {"reachable": False, "why": "not configured"}
    try:
        if spec.get("probe") == "evm":
            async with session.post(
                    url, json={"jsonrpc": "2.0", "id": 1,
                               "method": "eth_chainId", "params": []},
                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                body = await r.json(content_type=None)
            ok = bool(isinstance(body, dict) and body.get("result"))
            return {"reachable": ok,
                    "why": "" if ok else "the relay did not answer eth_chainId",
                    "chain_id": (body or {}).get("result", "")}
        # Jito's submission endpoint has no health method worth calling and
        # rejects anything that is not a bundle. Reachability is all that can
        # honestly be checked from here.
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return {"reachable": r.status < 500,
                    "why": "" if r.status < 500 else f"relay returned {r.status}"}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "why": f"{type(exc).__name__}"}


async def status() -> list[dict]:
    """Every chain: whether protection is possible, and whether it answers."""
    specs = routes()
    async with aiohttp.ClientSession() as session:
        probes = await asyncio.gather(
            *(_probe(session, specs[c]) for c in specs))
    out = []
    for (chain, spec), probe in zip(specs.items(), probes):
        out.append({
            "chain": chain,
            "relay": spec["name"],
            "url": spec["url"],
            "note": spec["note"],
            "supported": bool(spec["url"]),
            **probe,
        })
    return out
