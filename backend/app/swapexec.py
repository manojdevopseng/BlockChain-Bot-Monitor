"""Building the swap, proving it, then sending it.

This is the only code in the app that spends. Everything it does is arranged
around one idea: a transaction that has been sent cannot be taken back, so
every doubt has to be resolved before the send, not after.

The order is always the same — resolve, quote, build, **simulate**, sign,
send. The simulation is the part that matters. The calldata for a V4 swap is
five nested encodings deep, and a mistake anywhere in it does not produce a
Python error; it produces a transaction that reverts after paying gas, or
worse, one that succeeds against a pool nobody meant to trade in. So the
built transaction is run against the real chain with `eth_call` first, with
the sender's balance faked up so the call is not rejected for being poor. If
that comes back reverted, nothing is signed.

Each version is sent through the router built for it rather than through one
clever abstraction:

  V2 and V3 have routers that take native currency directly and need only an
  ERC-20 allowance to sell. Two well-worn interfaces, nothing exotic.

  V4 has no per-pool contract at all, so it goes through the Universal
  Router: a command string, an action string inside it, and the PoolKey the
  venue resolver recovered. Selling there also needs Permit2, which is a
  second allowance on top of the first.

The fee-on-transfer variants are used wherever they exist. Most tokens worth
buying from a caller take a cut on transfer, and the plain V2 swap function
reverts on those — after the gas is spent, with a message nobody can read.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from . import keys, mev, venue
from .config import settings
from .scanners import scfg
from .scanners.slog import get_logger

log = get_logger(__name__)

EVM_CHAINS = ("eth", "rbh", "bnb", "base")

# How long a swap may sit unmined before it is no longer wanted. Two minutes:
# a fill that lands twenty minutes late is a fill at a price nobody agreed to.
DEADLINE = 120

# Gas ceilings, per shape of trade. An underestimate reverts having paid for
# the attempt, so these are deliberately generous — unused gas is refunded.
GAS = {"v2": 400_000, "v3": 400_000, "v4": 600_000,
       "approve": 80_000, "permit2": 100_000}

# Sell everything down to the last unit rather than a rounded amount: dust
# left behind is a position that shows as open for ever.
MAX_UINT = (1 << 256) - 1
MAX_UINT160 = (1 << 160) - 1
NATIVE = venue.NATIVE

PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"


# ── the addresses each chain routes through ─────────────────────────────────

def _addr(chain: str, name: str) -> str:
    return str(getattr(settings, f"{chain}_{name}", "") or "").strip()


def routers(chain: str) -> dict:
    """Every contract this chain's swaps might touch."""
    return {
        "v2": _addr(chain, "v2_router"),
        "v3": _addr(chain, "v3_router"),
        "v4": _addr(chain, "universal_router"),
        "permit2": PERMIT2,
        "wnative": (scfg.BNB_WBNB if chain == "bnb"
                    else str(getattr(scfg, f"{chain.upper()}_WETH", "") or "")),
    }


def _rpc(chain: str, protected: bool) -> str:
    if protected:
        url = mev.endpoint(chain)
        if url:
            return url
    return str(getattr(scfg, f"{chain.upper()}_RPC_HTTP", "") or "")


def _w3(chain: str, protected: bool = False):
    from web3 import Web3
    url = _rpc(chain, protected)
    if not url:
        raise ValueError(f"No RPC endpoint configured for {chain.upper()}")
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 25}))


# ── the small amount of ABI this needs ──────────────────────────────────────

V2_ABI = [
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

# SwapRouter02: one call for the swap, wrapped in multicall so the leftover
# native can be refunded in the same transaction.
V3_ABI = [
    {"name": "exactInputSingle",
     "inputs": [{"components": [
         {"name": "tokenIn", "type": "address"},
         {"name": "tokenOut", "type": "address"},
         {"name": "fee", "type": "uint24"},
         {"name": "recipient", "type": "address"},
         {"name": "amountIn", "type": "uint256"},
         {"name": "amountOutMinimum", "type": "uint256"},
         {"name": "sqrtPriceLimitX96", "type": "uint160"}],
         "name": "params", "type": "tuple"}],
     "outputs": [{"name": "amountOut", "type": "uint256"}],
     "stateMutability": "payable", "type": "function"},
    {"name": "multicall",
     "inputs": [{"name": "data", "type": "bytes[]"}],
     "outputs": [{"name": "results", "type": "bytes[]"}],
     "stateMutability": "payable", "type": "function"},
    {"name": "refundETH", "inputs": [], "outputs": [],
     "stateMutability": "payable", "type": "function"},
    {"name": "unwrapWETH9",
     "inputs": [{"name": "amountMinimum", "type": "uint256"},
                {"name": "recipient", "type": "address"}],
     "outputs": [], "stateMutability": "payable", "type": "function"},
]

UNIVERSAL_ABI = [
    {"name": "execute",
     "inputs": [{"name": "commands", "type": "bytes"},
                {"name": "inputs", "type": "bytes[]"},
                {"name": "deadline", "type": "uint256"}],
     "outputs": [], "stateMutability": "payable", "type": "function"},
]

ERC20_ABI = [
    {"name": "balanceOf", "inputs": [{"name": "a", "type": "address"}],
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"name": "allowance",
     "inputs": [{"name": "o", "type": "address"}, {"name": "s", "type": "address"}],
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "approve",
     "inputs": [{"name": "s", "type": "address"}, {"name": "v", "type": "uint256"}],
     "outputs": [{"type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
]

PERMIT2_ABI = [
    {"name": "approve",
     "inputs": [{"name": "token", "type": "address"},
                {"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint160"},
                {"name": "expiration", "type": "uint48"}],
     "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"name": "allowance",
     "inputs": [{"name": "user", "type": "address"},
                {"name": "token", "type": "address"},
                {"name": "spender", "type": "address"}],
     "outputs": [{"name": "amount", "type": "uint160"},
                 {"name": "expiration", "type": "uint48"},
                 {"name": "nonce", "type": "uint48"}],
     "stateMutability": "view", "type": "function"},
]


# ── Universal Router's command and action bytes ─────────────────────────────
#
# The router reads `commands` one byte at a time and takes the matching entry
# from `inputs`. Inside a V4_SWAP the same pattern repeats one level down:
# a string of actions, and a list of parameters to go with them.

CMD_V4_SWAP = 0x10
ACT_SWAP_EXACT_IN_SINGLE = 0x06
ACT_SETTLE_ALL = 0x0c
ACT_TAKE_ALL = 0x0f


def _v4_calldata(w3, key: dict, token: str, amount_in: int, min_out: int,
                 buying: bool) -> tuple[bytes, list[bytes]]:
    """The commands and inputs for one V4 swap.

    `zeroForOne` says which way round the pool is being traded, and it is
    derived from the PoolKey rather than assumed: currency0 and currency1 are
    ordered by address, so which one is the token differs from pool to pool.
    """
    from eth_abi import encode
    from web3 import Web3

    c0 = Web3.to_checksum_address(key["currency0"])
    c1 = Web3.to_checksum_address(key["currency1"])
    token_is_0 = c0.lower() == token.lower()
    # Buying spends the other currency to get the token; selling is the
    # reverse. zeroForOne is true when the input side is currency0.
    zero_for_one = (not token_is_0) if buying else token_is_0

    pool_key = (c0, c1, int(key["fee"]), int(key["tick_spacing"]),
                Web3.to_checksum_address(key["hooks"]))

    swap_params = encode(
        ["((address,address,uint24,int24,address),bool,uint128,uint128,bytes)"],
        [(pool_key, zero_for_one, int(amount_in), int(min_out), b"")])

    currency_in = c0 if zero_for_one else c1
    currency_out = c1 if zero_for_one else c0
    settle = encode(["address", "uint256"], [currency_in, int(amount_in)])
    take = encode(["address", "uint256"], [currency_out, int(min_out)])

    actions = bytes([ACT_SWAP_EXACT_IN_SINGLE, ACT_SETTLE_ALL, ACT_TAKE_ALL])
    inner = encode(["bytes", "bytes[]"], [actions, [swap_params, settle, take]])
    return bytes([CMD_V4_SWAP]), [inner]


def _v3_path(token_in: str, fee: int, token_out: str) -> bytes:
    """V3 packs a path tightly: address, three-byte fee, address."""
    return (bytes.fromhex(token_in[2:]) + int(fee).to_bytes(3, "big")
            + bytes.fromhex(token_out[2:]))


# ── what the pool will actually pay ─────────────────────────────────────────

async def quote(chain: str, token: str, amount_in: int, *, buying: bool,
                v: dict) -> dict:
    """Expected output for this trade, read from the pool rather than guessed.

    V2 and V3 have quoters that answer exactly. V4's quoter is a contract call
    that reverts to return its answer, which is awkward enough that the
    fallback is used instead: the price DexScreener already reported for the
    pool the venue resolver chose. That is good enough to set a slippage
    floor, which is the only thing the number is used for — the pool itself
    decides the fill, and the floor decides whether it is accepted.
    """
    from web3 import Web3
    r = routers(chain)
    w3 = _w3(chain)
    wn = Web3.to_checksum_address(r["wnative"])
    tok = Web3.to_checksum_address(token)

    def _v2() -> Optional[int]:
        c = w3.eth.contract(address=Web3.to_checksum_address(r["v2"]), abi=V2_ABI)
        path = [wn, tok] if buying else [tok, wn]
        return c.functions.getAmountsOut(int(amount_in), path).call()[-1]

    if v["version"] == "v2" and r["v2"]:
        try:
            out = await asyncio.to_thread(_v2)
            return {"ok": True, "out": int(out), "source": "getAmountsOut"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "why": f"the pool would not quote: {type(exc).__name__}"}

    # V3 and V4: derive from the pool's own reported price. The fill still
    # comes from the pool; this only sets the floor beneath it.
    price = v.get("price_usd")
    native_usd = v.get("native_usd")
    if not price or not native_usd:
        return {"ok": False, "why": "no price to size the slippage floor from"}
    decimals = v.get("decimals", 18)
    if buying:
        spend_usd = amount_in / 1e18 * native_usd
        out = int(spend_usd / price * (10 ** decimals))
    else:
        got_usd = amount_in / (10 ** decimals) * price
        out = int(got_usd / native_usd * 1e18)
    return {"ok": True, "out": out, "source": "pool price"}


# ── proving it before signing it ────────────────────────────────────────────

async def simulate(chain: str, tx: dict) -> dict:
    """Run the built transaction against the real chain without sending it.

    This is the safety net the whole module is arranged around. A V4 swap's
    calldata is five encodings deep, and a mistake in it does not raise in
    Python — it reverts on chain, after the gas is gone. So the call is made
    for real, with the sender's balance overridden upward so it is not
    refused for being poor, and only a clean return is allowed through.
    """
    from web3 import Web3
    w3 = _w3(chain)
    call = {"from": tx["from"], "to": tx["to"], "data": tx["data"],
            "value": hex(int(tx.get("value", 0)))}
    # Enough to cover the trade and any gas the node wants to see.
    override = {tx["from"]: {"balance": hex(int(tx.get("value", 0)) + 10 ** 18)}}
    try:
        res = await asyncio.to_thread(
            w3.provider.make_request, "eth_call", [call, "latest", override])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"simulation could not run: {type(exc).__name__}"}
    if isinstance(res, dict) and res.get("error"):
        msg = str((res["error"] or {}).get("message") or "reverted")
        # The two that will come up most often, in words rather than in the
        # router's own vocabulary. Everything else is passed through as-is —
        # inventing a friendly summary for an unknown revert would hide it.
        if "TRANSFER_FROM_FAILED" in msg:
            return {"ok": False,
                    "why": "the wallet does not hold enough of this token, or "
                           "has not approved it"}
        if "INSUFFICIENT_OUTPUT_AMOUNT" in msg or "Too little received" in msg:
            return {"ok": False,
                    "why": "the pool would pay less than the slippage floor — "
                           "the price moved, or the pool is too thin for this size"}
        return {"ok": False, "why": f"the swap would revert: {msg[:160]}"}
    return {"ok": True, "result": (res or {}).get("result", "")}


# ── allowances, for selling ─────────────────────────────────────────────────

async def _allowance(chain: str, token: str, owner: str, spender: str) -> int:
    from web3 import Web3
    w3 = _w3(chain)
    c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
    fn = c.functions.allowance(Web3.to_checksum_address(owner),
                               Web3.to_checksum_address(spender))
    return int(await asyncio.to_thread(fn.call))


async def _needs_approval(chain: str, token: str, owner: str, spender: str,
                          amount: int) -> bool:
    try:
        return await _allowance(chain, token, owner, spender) < amount
    except Exception:  # noqa: BLE001
        # Unreadable allowance is treated as absent: an unnecessary approval
        # costs gas, a missing one costs the whole trade.
        return True


# ── signing and sending ─────────────────────────────────────────────────────

async def _send(chain: str, user: str, tx: dict, *, protected: bool,
                dry_run: bool) -> dict:
    """Simulate, then sign with the vault key, then broadcast.

    The simulation is not optional and not a debug aid — it is the step that
    makes the rest survivable. A V4 swap's calldata is five encodings deep and
    a mistake in it raises nothing in Python; it reverts on chain with the gas
    already spent. So the built transaction is run for real first, and only a
    clean return is allowed to proceed.

    `dry_run` stops there. The first trade on any chain should be provable
    before it is payable, and checking a route should never cost anything.
    """
    sim = await simulate(chain, tx)
    if not sim["ok"]:
        return {"ok": False, "stage": "simulate", "why": sim["why"]}
    if dry_run:
        return {"ok": True, "dry_run": True,
                "tx": {k: (v if isinstance(v, (int, str)) else str(v))
                       for k, v in tx.items()}}

    raw = await keys.signer(user, "evm")
    if raw is None:
        return {"ok": False, "stage": "key",
                "why": "no EVM trading wallet, or its key could not be opened"}
    try:
        from eth_account import Account
        w3 = _w3(chain, protected)
        acct = Account.from_key(raw)

        full = dict(tx)
        full.pop("from", None)
        # Pending, not latest: two buys fired within one block would otherwise
        # be handed the same nonce and the second would be thrown away.
        full["nonce"] = await asyncio.to_thread(
            w3.eth.get_transaction_count, acct.address, "pending")
        full["chainId"] = await asyncio.to_thread(lambda: w3.eth.chain_id)

        signed = acct.sign_transaction(full)
        blob = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        h = await asyncio.to_thread(w3.eth.send_raw_transaction, blob)
        h = h.hex() if hasattr(h, "hex") else str(h)
        if not h.startswith("0x"):
            h = "0x" + h
        log.info(f"[SWAP] {user} {chain} sent {h}")
        return {"ok": True, "hash": h, "protected": protected}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "send", "why": f"{type(exc).__name__}: {exc}"}
    finally:
        # The plaintext existed for one call. Dropping the reference is not a
        # guarantee in a garbage-collected language, but keeping one would be
        # a choice, and this declines to make it.
        raw = None


async def _gas_price(chain: str, gwei: float) -> int:
    """The account's setting when it has one, the chain's own suggestion when
    it does not. A hardcoded number would be wrong on all four chains."""
    if gwei and gwei > 0:
        return int(gwei * 1e9)
    try:
        w3 = _w3(chain)
        return int(await asyncio.to_thread(lambda: w3.eth.gas_price))
    except Exception:  # noqa: BLE001
        return int(1e9)


async def _approve(chain: str, user: str, token: str, spender: str, owner: str,
                   *, gas_price: int, dry_run: bool) -> dict:
    """Let a router move this token.

    A real transaction with real gas, once per token per spender. Whoever
    pressed Sell never sees it — the same key signs it — but it is neither
    free nor skippable: a router cannot take a token it has no allowance for,
    and the swap would simply revert.
    """
    from web3 import Web3
    w3 = _w3(chain)
    c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
    data = c.encode_abi("approve",
                        args=[Web3.to_checksum_address(spender), MAX_UINT])
    tx = {"from": Web3.to_checksum_address(owner),
          "to": Web3.to_checksum_address(token),
          "data": data, "value": 0, "gas": GAS["approve"],
          "gasPrice": gas_price}
    return await _send(chain, user, tx, protected=False, dry_run=dry_run)


# ── the one thing this module exists to do ──────────────────────────────────

async def balance_of(chain: str, token: str, owner: str) -> Optional[int]:
    """How much of a token an address holds, in the token's own units."""
    from web3 import Web3
    try:
        w3 = _w3(chain)
        c = w3.eth.contract(address=Web3.to_checksum_address(token),
                            abi=ERC20_ABI)
        fn = c.functions.balanceOf(Web3.to_checksum_address(owner))
        return int(await asyncio.to_thread(fn.call))
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[SWAP] balanceOf failed for {token[:10]}: {exc}")
        return None


async def _received(chain: str, token: str, owner: str, before: Optional[int],
                    tx_hash: str, timeout: float = 90.0) -> Optional[int]:
    """What the wallet actually gained, measured rather than computed.

    The quantity a buy is worth is not `spend / price`. The pool fills at its
    own price, and most tokens worth buying from a caller take a cut on
    transfer — so the arithmetic answer is always a little high. Recording it
    makes the position claim more tokens than exist, and the error only
    surfaces later, when a sell for everything is refused for being too big.

    So the balance is read before and after, and the difference is the truth.
    """
    if before is None:
        return None
    try:
        w3 = _w3(chain)
        await asyncio.to_thread(w3.eth.wait_for_transaction_receipt,
                                tx_hash, timeout)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[SWAP] no receipt for {tx_hash[:14]}: {type(exc).__name__}")
        return None
    after = await balance_of(chain, token, owner)
    if after is None:
        return None
    return max(0, after - before)


async def native_balance(chain: str, owner: str) -> Optional[int]:
    """The coin this chain spends, in wei. None when it cannot be read."""
    from web3 import Web3
    try:
        w3 = _w3(chain)
        return int(await asyncio.to_thread(
            w3.eth.get_balance, Web3.to_checksum_address(owner)))
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[SWAP] native balance failed on {chain}: {exc}")
        return None


async def _decimals(chain: str, token: str) -> int:
    """A token's decimals, read from it. Eighteen is the common case and the
    wrong answer often enough to matter — a six-decimal token sized as if it
    had eighteen is a trade a trillion times too small."""
    from web3 import Web3
    try:
        w3 = _w3(chain)
        c = w3.eth.contract(address=Web3.to_checksum_address(token),
                            abi=ERC20_ABI)
        return int(await asyncio.to_thread(c.functions.decimals().call))
    except Exception:  # noqa: BLE001
        return 18


async def trade(*, user: str, chain: str, token: str, amount: int,
                buying: bool, slippage: float, gwei: float = 0,
                protected: bool = True, dry_run: bool = False,
                v: dict | None = None) -> dict:
    """One swap, from a token address to a transaction hash.

    Resolve, quote, build, simulate, sign, send — stopping at the first step
    that cannot answer, with a reason somebody can act on rather than a stack
    trace. `amount` is in the smallest unit: wei when buying, the token's own
    units when selling.
    """
    from web3 import Web3

    chain = (chain or "").lower()
    if chain not in EVM_CHAINS:
        return {"ok": False, "stage": "chain",
                "why": f"{chain.upper()} is not a chain this can trade on yet"}

    v = v or await venue.best(chain, token)
    if not v.get("ok"):
        return {"ok": False, "stage": "venue", "why": v.get("why", "no pool")}

    r = routers(chain)
    version = v["version"]
    if not r.get(version):
        return {"ok": False, "stage": "router",
                "why": f"no {version.upper()} router configured for {chain.upper()}"}

    owner = await keys.address_for(user, "evm")
    if not owner:
        return {"ok": False, "stage": "key", "why": "no EVM trading wallet"}

    # Two reads that depend on nothing above them, started now so they
    # overlap everything that follows. Measured: the balance costs 10-23ms on
    # every chain (75ms on Tron) while resolving the venue costs 643ms on
    # Robinhood — so run concurrently, this check is free.
    gas_task = asyncio.ensure_future(_gas_price(chain, gwei))
    bal_task = (asyncio.ensure_future(native_balance(chain, owner))
                if buying else None)

    # The V3 and V4 quote is derived from the pool's price, so it needs the
    # coin's dollar value and the token's own decimals. Both are looked up
    # once here rather than inside quote(), which should not be opening its
    # own sessions and contracts.
    if v["version"] != "v2":
        v = dict(v)
        v.setdefault("decimals", await _decimals(chain, token))
        if not v.get("native_usd"):
            import aiohttp as _a
            from . import trading as _t
            async with _a.ClientSession() as _s:
                v["native_usd"] = await _t.native_price(_s, chain)

    q = await quote(chain, token, amount, buying=buying, v=v)
    gas_price = await gas_task

    # Refused here rather than at the send. The simulation cannot catch this:
    # it deliberately overrides the sender's balance so a route can be proved
    # without funding a wallet, which makes it blind to the one thing being
    # asked about. So the wallet is asked directly — and the whole build,
    # including the eth_call, is skipped when the answer is no.
    if bal_task is not None:
        have = await bal_task
        need = amount + GAS[v["version"]] * gas_price
        if have is not None and have < need:
            return {"ok": False, "stage": "balance",
                    "why": (f"this wallet holds {have / 1e18:.6f} and the trade "
                            f"needs about {need / 1e18:.6f} including gas")}

    if not q["ok"]:
        return {"ok": False, "stage": "quote", "why": q["why"]}
    min_out = int(q["out"] * (1 - min(max(slippage, 0.0), 99.0) / 100))
    if min_out <= 0:
        return {"ok": False, "stage": "quote",
                "why": "the slippage floor worked out to zero — refusing to "
                       "trade with no protection at all"}

    deadline = int(time.time()) + DEADLINE
    tok = Web3.to_checksum_address(token)
    wn = Web3.to_checksum_address(r["wnative"])
    me = Web3.to_checksum_address(owner)
    w3 = _w3(chain)
    steps: list[dict] = []

    # Asked before anything is built. Selling more than the wallet holds
    # reverts with TransferHelper: TRANSFER_FROM_FAILED, which is true and
    # unreadable — and by then an approval may already have been paid for.
    if not buying:
        try:
            erc = w3.eth.contract(address=tok, abi=ERC20_ABI)
            held = int(await asyncio.to_thread(erc.functions.balanceOf(me).call))
        except Exception:  # noqa: BLE001
            held = None
        if held is not None and held < amount:
            dec = v.get("decimals") or await _decimals(chain, token)
            return {"ok": False, "stage": "balance",
                    "why": (f"this wallet holds {held / 10 ** dec:,.6f} of the "
                            f"token and the sell is for "
                            f"{amount / 10 ** dec:,.6f}")}

    # Selling first needs the router allowed to move the token. On V4 that is
    # two allowances, because the Universal Router spends through Permit2
    # rather than holding one of its own.
    if not buying:
        spender = PERMIT2 if version == "v4" else r[version]
        if await _needs_approval(chain, token, owner, spender, amount):
            res = await _approve(chain, user, token, spender, owner,
                                 gas_price=gas_price, dry_run=dry_run)
            if not res.get("ok"):
                return {"ok": False, "stage": "approve",
                        "why": res.get("why", "approval failed")}
            steps.append({"approve": res.get("hash", "simulated")})
        if version == "v4":
            p2 = w3.eth.contract(address=Web3.to_checksum_address(PERMIT2),
                                 abi=PERMIT2_ABI)
            data = p2.encode_abi("approve", args=[
                tok, Web3.to_checksum_address(r["v4"]),
                MAX_UINT160, (1 << 48) - 1])
            res = await _send(chain, user,
                              {"from": me,
                               "to": Web3.to_checksum_address(PERMIT2),
                               "data": data, "value": 0,
                               "gas": GAS["permit2"],
                               "gasPrice": gas_price},
                              protected=False, dry_run=dry_run)
            if not res.get("ok"):
                return {"ok": False, "stage": "permit2",
                        "why": res.get("why", "Permit2 approval failed")}
            steps.append({"permit2": res.get("hash", "simulated")})

    # ── the swap itself, through the router that version belongs to ────────
    if version == "v2":
        c = w3.eth.contract(address=Web3.to_checksum_address(r["v2"]), abi=V2_ABI)
        if buying:
            data = c.encode_abi(
                "swapExactETHForTokensSupportingFeeOnTransferTokens",
                args=[min_out, [wn, tok], me, deadline])
            value = amount
        else:
            data = c.encode_abi(
                "swapExactTokensForETHSupportingFeeOnTransferTokens",
                args=[amount, min_out, [tok, wn], me, deadline])
            value = 0
        to = r["v2"]

    elif version == "v3":
        c = w3.eth.contract(address=Web3.to_checksum_address(r["v3"]), abi=V3_ABI)
        fee = int(v.get("fee") or 3000)
        params = ((wn, tok, fee, me, amount, min_out, 0) if buying
                  else (tok, wn, fee, me, amount, min_out, 0))
        inner = c.encode_abi("exactInputSingle", args=[params])
        if buying:
            # Wrapped in multicall so refundETH runs in the same transaction:
            # whatever the pool did not take would otherwise stay in the
            # router, belonging to nobody.
            data = c.encode_abi("multicall", args=[[
                bytes.fromhex(inner[2:]),
                bytes.fromhex(c.encode_abi("refundETH", args=[])[2:])]])
            value = amount
        else:
            data = inner
            value = 0
        to = r["v3"]

    else:  # v4
        key = v.get("v4")
        if not key:
            return {"ok": False, "stage": "venue",
                    "why": "V4 pool found but its key was never recovered"}
        commands, inputs = _v4_calldata(w3, key, token, amount, min_out, buying)
        c = w3.eth.contract(address=Web3.to_checksum_address(r["v4"]),
                            abi=UNIVERSAL_ABI)
        data = c.encode_abi("execute", args=[commands, inputs, deadline])
        # V4 holds the native coin as a currency in its own right, so a buy
        # sends it with the call instead of wrapping first.
        value = amount if buying else 0
        to = r["v4"]

    tx = {"from": me, "to": Web3.to_checksum_address(to), "data": data,
          "value": int(value), "gas": GAS[version],
          "gasPrice": gas_price}

    # Read the holding before sending, so the fill can be measured against it.
    before = None if (dry_run or not buying) else await balance_of(chain, token, owner)

    res = await _send(chain, user, tx, protected=protected, dry_run=dry_run)

    if res.get("ok") and buying and not dry_run:
        got = await _received(chain, token, owner, before, res["hash"])
        if got:
            res["received"] = got
            log.info(f"[SWAP] filled {got} units of {token[:10]} "
                     f"(expected {q['out']}, floor {min_out})")

    return {**res, "version": version, "dex": v.get("dex"),
            "liquidity": v.get("liquidity"), "expected_out": q["out"],
            "min_out": min_out, "quote_source": q["source"], "steps": steps}
