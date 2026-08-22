"""Where a token actually trades — which pool, which Uniswap version.

A router cannot be picked in advance. On Robinhood Chain the same nine tokens
sit across three versions at once: KITSU has V2, V3 and V4 pools and its
deepest is V3; FLUSH exists only on V4; INVEST's best pool is V2. Deciding
"this chain uses V2" would buy half of them in the wrong place and miss the
rest entirely — and buying in a thin pool when a deep one exists is a loss
that looks like slippage and never gets explained.

So the venue is resolved per token, per trade, by depth. DexScreener answers
that in one request it is already being asked for prices: every pair, its
version, and how much liquidity is behind it.

Depth is the only ranking. Not the newest pool, not the version we would
rather use — the one with the most money in it, because that is the one that
moves least when a buy lands on it.

V4 needs more than an address. A V2 or V3 pool is a contract you can call; a
V4 pool is an entry inside one PoolManager, addressed by a hash of five
values — currency0, currency1, fee, tickSpacing and hooks. The hash is
one-way, so knowing the pool id is not enough to trade on it. Two ways round
that, in order of trust:

  The detector has been recording Initialize events since it was written, and
  they carry four of the five fields. Only tickSpacing is missing, and with
  the pool id already known it can be recovered by trying the handful of legal
  values and keeping whichever reproduces the id — a check, not a guess.

  Failing that, the whole key is searched: the standard fee tiers, the
  spacings that go with them, and the hooks known to run pools on that chain.
  Whatever reproduces the id is right by construction.
"""

from __future__ import annotations

from typing import Optional

import aiohttp

from . import db
from .scanners.slog import get_logger

log = get_logger(__name__)

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"

# Our chain ids to DexScreener's.
_DS_CHAIN = {"rbh": "robinhood", "eth": "ethereum", "bnb": "bsc",
             "base": "base", "sol": "solana", "tron": "tron"}

# The detector writes Robinhood pools under two names, depending on which
# scanner saw them first. Both are the same chain and both are searched.
_POOL_CHAINS = {"rbh": ("robinhood", "rbhx", "rbh"), "eth": ("eth",),
                "bnb": ("bnb", "bsc"), "base": ("base",)}

# V4 in the native coin is the zero address, not the wrapped token.
NATIVE = "0x0000000000000000000000000000000000000000"

# A pool this thin cannot fill anything without moving the price to nonsense,
# so it is treated as no pool at all. And a routable pool worth less than this
# share of the real one is a worse answer than admitting the token cannot be
# reached — the fill would be technically successful and financially absurd.
MIN_LIQUIDITY = 2_000.0
MIN_SHARE = 0.10

# The legal (fee, tickSpacing) pairs. A hooked pool may use anything, which is
# why the hooked shapes are listed separately rather than assumed.
_TIERS = ((100, 1), (500, 10), (3000, 60), (10000, 200))
_HOOK_TIERS = ((0, 200), (0, 60), (0, 1), (100, 1), (500, 10), (3000, 60),
               (10000, 200))
# V4 lets a pool choose any spacing up to this, and launchpads use values no
# standard tier lists — the deepest MOOONER pool runs fee 2500, whose spacing
# is in none of the four documented pairs. Since a candidate is checked by
# rehashing it against the pool id, searching the whole legal range is a
# verification rather than a guess, and thirty thousand keccaks costs
# milliseconds. Ordered so the common values are found first.
_MAX_SPACING = 32767
_SPACINGS = tuple(dict.fromkeys(
    (1, 10, 60, 200, 2, 4, 5, 20, 25, 50, 100, 500, 1000, 2000)
    + tuple(range(1, _MAX_SPACING + 1))))


def pool_id(currency0: str, currency1: str, fee: int, tick_spacing: int,
            hooks: str) -> str:
    """V4's pool id: keccak of the PoolKey, five words, in order."""
    from eth_utils import keccak

    def addr(v: str) -> bytes:
        return bytes(12) + bytes.fromhex(v.lower().replace("0x", "").rjust(40, "0"))

    def num(v: int) -> bytes:
        return int(v).to_bytes(32, "big", signed=v < 0)

    return "0x" + keccak(addr(currency0) + addr(currency1) + num(fee)
                         + num(tick_spacing) + addr(hooks)).hex()


async def _dexscreener(session: aiohttp.ClientSession, chain: str,
                       token: str) -> list[dict]:
    want = _DS_CHAIN.get(chain, chain)
    try:
        async with session.get(DEX_URL + token,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            body = await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[VENUE] dexscreener failed for {token[:10]}: {exc}")
        return []
    out = []
    for p in (body or {}).get("pairs") or []:
        if p.get("chainId") != want:
            continue
        labels = [str(x).lower() for x in (p.get("labels") or [])]
        out.append({
            "version": next((v for v in ("v4", "v3", "v2") if v in labels), "v2"),
            "dex": p.get("dexId") or "",
            "pair": p.get("pairAddress") or "",
            "liquidity": float((p.get("liquidity") or {}).get("usd") or 0),
            "base": ((p.get("baseToken") or {}).get("address") or "").lower(),
            "quote": ((p.get("quoteToken") or {}).get("address") or "").lower(),
            "quote_symbol": (p.get("quoteToken") or {}).get("symbol") or "",
            "price_usd": float(p.get("priceUsd") or 0) or None,
        })
    return out


async def _v4_key(chain: str, token: str, pid: str) -> Optional[dict]:
    """The full PoolKey behind a V4 pool id, or None.

    Recovered rather than trusted: whatever is assembled is hashed and checked
    against the id it claims to be. A PoolKey that does not reproduce its own
    id would be a swap against a pool that does not exist.
    """
    pid = (pid or "").lower()
    if not pid:
        return None
    chains = _POOL_CHAINS.get(chain, (chain,))
    row = await db.get_collection("v4_pools").find_one(
        {"chain": {"$in": list(chains)}, "pool_id": pid})

    if row:
        # Four of the five fields are on the record; only the spacing is not.
        # With the id known, trying each legal value and keeping the one that
        # reproduces it turns the guess into a verification.
        for spacing in _SPACINGS:
            if pool_id(row["currency0"], row["currency1"], int(row["fee"]),
                       spacing, row["hooks"]).lower() == pid:
                return {"currency0": row["currency0"], "currency1": row["currency1"],
                        "fee": int(row["fee"]), "tick_spacing": spacing,
                        "hooks": row["hooks"], "source": "initialize log"}
        log.debug(f"[VENUE] {pid[:12]} on record but no spacing reproduces it")

    # No record: search the whole key. The token pairs with the native coin in
    # nearly every launch, so that is the only counter-currency tried.
    from .scanners import scfg
    hooks_raw = {"rbh": getattr(scfg, "RBH_V4_HOOKS", ""),
                 "eth": getattr(scfg, "ETH_V4_HOOKS", ""),
                 "bnb": getattr(scfg, "BNB_V4_HOOKS", "")}.get(chain, "")
    hooks = [NATIVE] + [h.strip().lower()
                        for h in str(hooks_raw or "").split(",") if h.strip()]
    t = token.lower()
    for c0, c1 in ((NATIVE, t), (t, NATIVE)):
        for hook in dict.fromkeys(hooks):
            tiers = _TIERS if hook == NATIVE else _HOOK_TIERS
            for fee, spacing in tiers:
                if pool_id(c0, c1, fee, spacing, hook).lower() == pid:
                    return {"currency0": c0, "currency1": c1, "fee": fee,
                            "tick_spacing": spacing, "hooks": hook,
                            "source": "computed"}
    return None


# How stale a stored resolution may be. A pool does not move, but liquidity
# does — and a token whose pool was resolved a week ago may have been
# abandoned since. Long enough to cover a caller's token for its whole life,
# short enough that a dead pool is re-checked.
REF_TTL = 6 * 3600


async def _from_record(chain: str, token: str) -> Optional[dict]:
    """The pool the detector already resolved, shaped like a venue answer."""
    import time
    row = await db.get_collection("pool_refs").find_one(
        {"chain": chain, "token": token.lower()})
    if not row or not row.get("kind"):
        return None
    if time.time() - float(row.get("found_at") or 0) > REF_TTL:
        return None

    out = {"ok": True, "chain": chain, "token": token.lower(),
           "version": row["kind"], "dex": "uniswap",
           "pair": row.get("address") or row.get("pool_id") or "",
           # Liquidity is not on the record and is not needed here: the pool
           # was chosen as the deepest one when it was resolved, and the
           # slippage floor is what actually protects the fill.
           "liquidity": None, "price_usd": None,
           "quote_symbol": "", "alternatives": 1,
           "decimals": int(row.get("decimals") or 18),
           "source": "detector", "why": ""}
    if row["kind"] == "v3":
        out["fee"] = int(row.get("fee") or 3000)
    elif row["kind"] == "v4":
        key = await _v4_key(chain, token, row.get("pool_id", ""))
        if not key:
            # Without the key there is nothing to route through, and the
            # network path may still find a different pool that works.
            return None
        out["v4"] = key
    return out


async def _v3_fee(chain: str, pool: str) -> Optional[int]:
    """The fee tier of a V3 pool, read from the pool itself."""
    import asyncio
    try:
        from web3 import Web3
        from .scanners import scfg
        url = str(getattr(scfg, f"{chain.upper()}_RPC_HTTP", "") or "")
        if not url or not pool:
            return None
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
        abi = [{"name": "fee", "inputs": [], "outputs": [{"type": "uint24"}],
                "stateMutability": "view", "type": "function"}]
        c = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=abi)
        return int(await asyncio.to_thread(c.functions.fee().call))
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[VENUE] v3 fee unreadable for {pool[:10]}: {exc}")
        return None


async def best(chain: str, token: str,
               session: aiohttp.ClientSession | None = None) -> dict:
    """The deepest pool this token has on this chain, ready to trade against.

    Returns `ok: False` with a reason rather than raising — a token with no
    pool is an ordinary answer, not a fault, and the caller wants to say so
    on the row rather than see a stack trace.
    """
    # What the detector already worked out, if it has seen this token. It
    # resolves the pool from the chain itself in a few tens of milliseconds
    # and knows the version, the fee and the pool — everything a trade needs.
    # Asking DexScreener for the same answer costs two seconds on a token
    # nobody has indexed yet, which is precisely the token a caller just
    # named. So the record is tried first and the network second.
    known = await _from_record(chain, token)
    if known:
        return known

    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        pairs = await _dexscreener(session, chain, token)
    finally:
        if own:
            await session.close()

    if not pairs:
        return {"ok": False, "why": "no pool found for this token"}

    # Only pools quoted in the coin being spent. INVEST's deepest V2 pool is
    # against SPY, and a buy routed [WETH, INVEST] simply reverts — there is
    # no such pair. Trading it would mean routing WETH -> SPY -> INVEST, two
    # fees and two lots of slippage through a pool nobody checked, which is a
    # worse answer than saying the token cannot be reached.
    from .scanners import scfg
    wn = str(getattr(scfg, "BNB_WBNB" if chain == "bnb"
                     else f"{chain.upper()}_WETH", "") or "").lower()
    quotable = {NATIVE, wn} - {""}
    native_pairs = [p for p in pairs if p["quote"] in quotable]
    if not native_pairs:
        deep = max(pairs, key=lambda p: p["liquidity"])
        return {"ok": False,
                "why": (f"every pool for this token is quoted in "
                        f"{deep['quote_symbol'] or 'another token'}, not "
                        f"{'BNB' if chain == 'bnb' else 'ETH'} — reaching it "
                        f"would need a second hop this does not route")}

    native_pairs.sort(key=lambda p: -p["liquidity"])
    pairs = native_pairs
    top = pairs[0]
    out = {"ok": True, "chain": chain, "token": token.lower(),
           "version": top["version"], "dex": top["dex"], "pair": top["pair"],
           "liquidity": top["liquidity"], "price_usd": top["price_usd"],
           "quote_symbol": top["quote_symbol"],
           "alternatives": len(pairs), "why": ""}

    # A pool on somebody else's DEX cannot be traded through Uniswap's router.
    # Falling back to a routable one is only right when the fallback is real:
    # FEATHERS has $19,353 on `up` and $66 on Uniswap V4, and a buy into the
    # $66 pool would move the price so far that the fill would be worthless.
    # A trade that cannot be done well is refused rather than done badly.
    if top["dex"] not in ("uniswap", "pancakeswap"):
        alt = next((p for p in pairs
                    if p["dex"] in ("uniswap", "pancakeswap")), None)
        if alt is None:
            out.update({"ok": False,
                        "why": f"deepest pool is on {top['dex']}, which this "
                               f"cannot route through"})
            return out
        if alt["liquidity"] < MIN_LIQUIDITY or alt["liquidity"] < top["liquidity"] * MIN_SHARE:
            out.update({"ok": False,
                        "why": (f"deepest pool is on {top['dex']} "
                                f"(${top['liquidity']:,.0f}), which this cannot "
                                f"route through — and the best routable pool "
                                f"holds only ${alt['liquidity']:,.0f}")})
            return out
        out.update({"version": alt["version"], "dex": alt["dex"],
                    "pair": alt["pair"], "liquidity": alt["liquidity"],
                    "why": f"deepest pool is on {top['dex']} "
                           f"(${top['liquidity']:,.0f}); using the "
                           f"{alt['dex']} {alt['version']} pool instead"})

    # V3 needs the pool's fee tier to route, and it is not in the DexScreener
    # answer. The pair address for V3 is the pool contract itself, so it can
    # simply be asked — and a wrong tier would route into a different pool of
    # the same two tokens, which is a real pool with a real (worse) price.
    if top["liquidity"] < MIN_LIQUIDITY:
        return {"ok": False,
                "why": (f"the deepest pool quoted in the coin holds only "
                        f"${top['liquidity']:,.0f} — too thin to fill without "
                        f"moving the price to nonsense")}

    if out["version"] == "v3":
        fee = await _v3_fee(chain, out["pair"])
        if fee is None:
            return {**out, "ok": False,
                    "why": "V3 pool found but it would not report its fee tier"}
        out["fee"] = fee

    if out["version"] == "v4":
        key = await _v4_key(chain, token, out["pair"])
        if not key:
            return {**out, "ok": False,
                    "why": "V4 pool found but its key could not be recovered — "
                           "nothing can be routed through a pool whose shape "
                           "is unknown"}
        out["v4"] = key
    return out
