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
# Endpoints are overridable from .env so a different relay can replace this
# one without a code change.
def routes() -> dict[str, dict]:
    """Per chain: where a protected order goes, and what that is actually worth.

    Five of the six run through one provider, whose front-running protection
    is a property of the key rather than a per-call flag — so "supported"
    here means the endpoint exists and the key has that routing switched on.
    That is the most this side can honestly claim without watching a trade
    land, and the note on each chain says what the route is really buying.
    """
    relay = "dRPC front-running protection"
    return {
        "eth": {
            "name": relay, "url": settings.eth_mev_rpc, "probe": "evm",
            "note": "Ethereum has a public mempool and the busiest sandwich "
                    "bots there are — this is where the routing earns its "
                    "keep. The order goes to the relay instead of being "
                    "broadcast for anyone to read.",
        },
        "bnb": {
            "name": relay, "url": settings.bnb_mev_rpc, "probe": "evm",
            "note": "BNB Chain has a public mempool and active sandwich bots. "
                    "Routed privately rather than broadcast.",
        },
        "sol": {
            "name": relay, "url": settings.sol_mev_rpc, "probe": "sol",
            "note": "Routed through the relay rather than the ordinary "
                    "submission path.",
        },
        # Base and Robinhood both order through a single sequencer, so there
        # is no public mempool for a bot to read. The provider offers a route
        # on both and it costs nothing to use — but the honest description is
        # that the sandwich it defends against is not the attack these chains
        # have, and no relay defends against the sequencer itself, because
        # that is who receives the transaction either way.
        "base": {
            "name": relay, "url": settings.base_mev_rpc, "probe": "evm",
            "note": "Base orders through one sequencer, so there is no public "
                    "mempool to be front-run in. The route is available and "
                    "harmless — but the threat it defends against is not the "
                    "one this chain has.",
        },
        "rbh": {
            "name": relay, "url": settings.rbh_mev_rpc, "probe": "evm",
            "note": "Robinhood Chain orders first-come-first-served through a "
                    "single sequencer, with no public mempool. Paying more gas "
                    "cannot jump the queue, and no relay protects against the "
                    "sequencer itself.",
        },
        # No protected route exists here at all. Carried in the table anyway
        # so the panel can show the chain and explain itself, rather than
        # leaving a gap somebody has to guess about.
        "tron": {
            "name": "", "url": "", "probe": "none",
            "note": "No protected route is offered for Tron. Orders go the "
                    "ordinary way.",
        },
    }


# ── what a browser wallet may be handed ─────────────────────────────────────
#
# The routes above carry an API key, so they can never be given to a browser:
# every customer would receive the operator's credential and could spend the
# quota it pays for. These are the credential-free equivalents — public relays
# built to be handed to end users, which is exactly what Flashbots Protect and
# 48Club are for.
#
# Only the chains where it changes something are listed. Base and Robinhood
# order through a single sequencer with no public mempool, so pointing a
# wallet at a different endpoint there protects against nothing — offering the
# switch would be asking somebody to change their wallet settings for no
# reason. Solana and Tron have no such wallet method at all.
WALLET_NETWORKS = {
    "eth": {
        "chain_id": "0x1", "name": "Ethereum (Flashbots Protect)",
        "rpc": "https://rpc.flashbots.net/fast",
        "explorer": "https://etherscan.io",
        "symbol": "ETH", "decimals": 18,
        "relay": "Flashbots Protect",
        "why": "Your wallet sends straight to block builders instead of the "
               "public mempool, so a buy cannot be read and raced.",
    },
    "bnb": {
        "chain_id": "0x38", "name": "BNB Chain (48Club Private)",
        "rpc": "https://rpc.48.club",
        "explorer": "https://bscscan.com",
        "symbol": "BNB", "decimals": 18,
        "relay": "48Club Private RPC",
        "why": "A private relay to BNB Chain validators, instead of a mempool "
               "that sandwich bots read continuously.",
    },
}


def wallet_networks() -> dict[str, dict]:
    """The chains a browser wallet can usefully be switched to, and how."""
    return WALLET_NETWORKS


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
        if spec.get("probe") == "sol":
            async with session.post(
                    url, json={"jsonrpc": "2.0", "id": 1,
                               "method": "getHealth", "params": []},
                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                body = await r.json(content_type=None)
            ok = (body or {}).get("result") == "ok"
            return {"reachable": ok,
                    "why": "" if ok else "the relay did not report healthy"}
        return {"reachable": False, "why": "no protected route for this chain"}
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
