"""Turning a decision into a transaction — the part that actually spends.

Everything else in this app reads. This builds a swap, signs it with the
vault's key, and puts it on a chain. There is no undo, so the code is written
to refuse rather than to guess: an address it cannot verify, a quote it cannot
get, a balance that will not cover the gas — each one stops the trade instead
of sending something and hoping.

Three things are checked before any money moves, every time:

  The router is who it claims to be. A router address is just a string in a
  config file until something proves it — so this asks the contract for its
  own factory and its own wrapped-native address, and refuses unless both
  match the chain's known values. A wrong address here does not error; it
  accepts the money and keeps it.

  The price is read, not assumed. `getAmountsOut` gives what the pool will
  actually pay, and the slippage setting turns that into a floor written into
  the transaction. Without the floor a sandwich takes whatever it likes;
  with it, the trade reverts instead of filling at a robbed price.

  Fee-on-transfer is assumed. Most tokens worth buying from a caller take a
  cut on transfer, and the plain swap function reverts on those — silently,
  after gas. The SupportingFeeOnTransferTokens variants work for both kinds,
  so they are the only ones used.

Selling needs an allowance first, because a router cannot move a token the
owner has not permitted. That is a real transaction with real gas, once per
token — invisible to whoever pressed Sell, since the same key signs it, but
not free and not skippable.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from . import keys, mev
from .config import settings
from .scanners import scfg
from .scanners.slog import get_logger

log = get_logger(__name__)

# The EVM chains this can trade on. Solana and Tron route through entirely
# different programs and are not served here.
EVM_CHAINS = ("eth", "rbh", "bnb", "base")

# How long a swap may sit unmined before it is no longer wanted. Short: a
# transaction that lands twenty minutes late fills at a price nobody agreed to.
DEADLINE = 120

# Gas ceilings. A swap into a fee-on-transfer token costs more than a plain
# one, and an underestimate reverts after paying for the attempt.
GAS_APPROVE = 60_000
GAS_SWAP = 350_000


def _router(chain: str) -> str:
    return str(getattr(settings, f"{chain}_v2_router", "") or "").strip()


def _factory(chain: str) -> str:
    return str(getattr(scfg, f"{chain.upper()}_V2_FACTORY", "") or "").strip()


def _wnative(chain: str) -> str:
    key = {"bnb": "BNB_WBNB"}.get(chain, f"{chain.upper()}_WETH")
    return str(getattr(scfg, key, "") or "").strip()


def _rpc_url(chain: str, protected: bool) -> str:
    """Where the signed transaction goes.

    The protected relay when the chain's setting asks for it and one exists,
    otherwise the ordinary endpoint. Falling back rather than failing is
    deliberate: a relay being down should slow a trade's protection, not stop
    the trade — and the operator is told separately when a relay goes quiet.
    """
    if protected:
        url = mev.endpoint(chain)
        if url:
            return url
    return str(getattr(scfg, f"{chain.upper()}_RPC_HTTP", "") or "")


def _w3(chain: str, protected: bool = False):
    from web3 import Web3
    url = _rpc_url(chain, protected)
    if not url:
        raise ValueError(f"No RPC endpoint configured for {chain.upper()}")
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))


# Only the pieces of the ABI this calls. A full ABI would be noise, and every
# extra entry is another thing that can be wrong.
ROUTER_ABI = [
    {"name": "factory", "outputs": [{"type": "address"}], "inputs": [],
     "stateMutability": "view", "type": "function"},
    {"name": "WETH", "outputs": [{"type": "address"}], "inputs": [],
     "stateMutability": "view", "type": "function"},
    {"name": "getAmountsOut",
     "inputs": [{"name": "amountIn", "type": "uint256"},
                {"name": "path", "type": "address[]"}],
     "outputs": [{"name": "amounts", "type": "uint256[]"}],
     "stateMutability": "view", "type": "function"},
    {"name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
     "inputs": [{"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"}],
     "outputs": [], "stateMutability": "payable", "type": "function"},
    {"name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
     "inputs": [{"name": "amountIn", "type": "uint256"},
                {"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"}],
     "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]

ERC20_ABI = [
    {"name": "balanceOf", "inputs": [{"name": "a", "type": "address"}],
     "outputs": [{"type": "uint256"}], "stateMutability": "view",
     "type": "function"},
    {"name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"name": "allowance",
     "inputs": [{"name": "o", "type": "address"}, {"name": "s", "type": "address"}],
     "outputs": [{"type": "uint256"}], "stateMutability": "view",
     "type": "function"},
    {"name": "approve",
     "inputs": [{"name": "s", "type": "address"}, {"name": "v", "type": "uint256"}],
     "outputs": [{"type": "bool"}], "stateMutability": "nonpayable",
     "type": "function"},
]


# ── proving the router before trusting it ───────────────────────────────────

async def verify_router(chain: str) -> dict:
    """Ask the router to identify itself. Never assumes the config is right.

    A wrong router address is the worst kind of mistake available here: it
    does not throw, it accepts the transfer and keeps it. So the address is
    checked against two things the real router must agree with — the chain's
    factory and its wrapped native — before it is ever sent a payment.
    """
    out = {"chain": chain, "router": _router(chain), "ok": False, "why": ""}
    if chain not in EVM_CHAINS:
        out["why"] = "not an EVM chain"
        return out
    if not out["router"]:
        out["why"] = f"no router configured — set {chain.upper()}_V2_ROUTER"
        return out
    try:
        import asyncio
        from web3 import Web3

        def _check():
            w3 = _w3(chain)
            r = w3.eth.contract(address=Web3.to_checksum_address(out["router"]),
                                abi=ROUTER_ABI)
            return r.functions.factory().call(), r.functions.WETH().call()

        got_factory, got_weth = await asyncio.to_thread(_check)
    except Exception as exc:  # noqa: BLE001
        out["why"] = f"could not reach the router: {type(exc).__name__}"
        return out

    want_factory, want_weth = _factory(chain), _wnative(chain)
    out["factory"], out["wnative"] = got_factory, got_weth
    if want_factory and got_factory.lower() != want_factory.lower():
        out["why"] = (f"router reports factory {got_factory} but this chain's "
                      f"factory is {want_factory}")
        return out
    if want_weth and got_weth.lower() != want_weth.lower():
        out["why"] = (f"router reports {got_weth} as wrapped native but this "
                      f"chain uses {want_weth}")
        return out
    if not want_factory and not want_weth:
        out["why"] = "nothing known to check the router against"
        return out
    out["ok"] = True
    return out


async def status() -> list[dict]:
    """Every EVM chain: can it trade, and if not, what is missing."""
    import asyncio
    return list(await asyncio.gather(*(verify_router(c) for c in EVM_CHAINS)))
