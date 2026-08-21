"""Taking USDT and USDC, and knowing when one has arrived.

The hard part of accepting crypto is not showing an address, it is deciding
which payment belongs to which order. Three ways are common and two are bad:

  one address per order   correct, and needs a wallet that can derive and sweep
      hundreds of addresses. That is a custody problem, not a payment one.
  a memo or tag   works on some chains, is ignored by users on all of them.
  a unique amount   what this does. Every pending order is quoted a figure no
      other pending order on that chain is quoted — $29.99 becomes $29.9743 —
      so the arriving number names the order by itself.

And the arrival is read as a balance, not as a log. `eth_getLogs` is where the
free tiers put their limits (ours allows a ten-block range), while "what is our
balance now" is one call per chain per pass, forever, on any provider. A rise
that matches a pending order's figure is that order paid.

What this deliberately does not do: hold funds, sweep, refund, or price
anything. It watches one address per chain, which is yours, and says what
landed in it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)

# balanceOf(address)
_SEL_BALANCE_OF = "0x70a08231"


@dataclass(frozen=True)
class Asset:
    """One stablecoin on one chain, and how to read our balance of it."""
    chain: str              # eth | bsc | sol | tron
    symbol: str             # USDT | USDC
    label: str              # what the user picks from: "USDT on BNB Chain"
    contract: str
    decimals: int
    # Roughly what it costs the payer to send. Shown so nobody pays $12 of gas
    # to save nothing — it is the honest reason to prefer one rail.
    fee_note: str


ASSETS: dict[str, Asset] = {
    "bsc_usdt": Asset("bsc", "USDT", "USDT on BNB Chain (BEP20)",
                      "0x55d398326f99059fF775485246999027B3197955", 18,
                      "a few cents"),
    "bsc_usdc": Asset("bsc", "USDC", "USDC on BNB Chain (BEP20)",
                      "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18,
                      "a few cents"),
    "sol_usdt": Asset("sol", "USDT", "USDT on Solana (SPL)",
                      "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6,
                      "a fraction of a cent"),
    "sol_usdc": Asset("sol", "USDC", "USDC on Solana (SPL)",
                      "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6,
                      "a fraction of a cent"),
    "tron_usdt": Asset("tron", "USDT", "USDT on Tron (TRC20)",
                       "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6,
                       "about a dollar"),
    "eth_usdt": Asset("eth", "USDT", "USDT on Ethereum (ERC20)",
                      "0xdAC17F958D2ee523a2206206994597C13D831ec7", 6,
                      "several dollars — pick another rail if you can"),
    "eth_usdc": Asset("eth", "USDC", "USDC on Ethereum (ERC20)",
                      "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6,
                      "several dollars — pick another rail if you can"),
}


def receiving_address(chain: str) -> str:
    """Where money for this chain is sent. Blank means the rail is closed."""
    return {
        "eth": settings.pay_eth_address,
        "bsc": settings.pay_bsc_address,
        "sol": settings.pay_sol_address,
        "tron": settings.pay_tron_address,
    }.get(chain, "") or ""


def allowed_ids() -> Optional[set[str]]:
    """The explicit allowlist, or None when there is not one."""
    raw = [a.strip().lower() for a in (settings.pay_assets or "").split(",")]
    picked = {a for a in raw if a in ASSETS}
    return picked or None


def available() -> list[Asset]:
    """The rails that can actually take money, cheapest for the payer first.

    Two conditions, both required: the chain has a receiving address, and the
    coin is one we said we would accept. Cheapest first because the same $29.99
    costs cents on Solana and several dollars on Ethereum, and the payer is the
    one who feels that.
    """
    order = ["sol_usdc", "sol_usdt", "bsc_usdt", "bsc_usdc", "tron_usdt",
             "eth_usdc", "eth_usdt"]
    picked = allowed_ids()
    return [ASSETS[k] for k in order
            if receiving_address(ASSETS[k].chain)
            and (picked is None or k in picked)]


def asset_by_id(asset_id: str) -> Optional[Asset]:
    return ASSETS.get(asset_id)


def unique_amount(price: float, taken: set[float]) -> float:
    """The figure this order will be quoted: the price, made unique.

    Four decimal places of noise below a cent — enough that two orders for the
    same plan on the same rail cannot collide, small enough that nobody feels
    overcharged. Rounded to 4dp because that is what every wallet will let the
    payer type.
    """
    for _ in range(200):
        cents = secrets.randbelow(9000) + 500      # 0.0005 … 0.0095
        amount = round(price - cents / 1_000_000, 4)
        if amount not in taken and amount > 0:
            return amount
    # Astronomically unlikely; the caller would rather have a duplicate than an
    # exception, and the watcher settles the older order first.
    return round(price, 4)


# ── reading what has arrived ─────────────────────────────────────────────────

class BalanceReader:
    """Our balance of one asset, on any of the four chains, as a decimal."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def balance(self, asset: Asset) -> Optional[float]:
        address = receiving_address(asset.chain)
        if not address:
            return None
        if asset.chain in ("eth", "bsc"):
            return await self._evm(asset, address)
        if asset.chain == "sol":
            return await self._sol(asset, address)
        if asset.chain == "tron":
            return await self._tron(asset, address)
        return None

    async def _evm(self, asset: Asset, address: str) -> Optional[float]:
        from .scanners import scfg
        http = scfg.ETH_RPC_HTTP if asset.chain == "eth" else scfg.BNB_RPC_HTTP
        if not http:
            return None
        data = _SEL_BALANCE_OF + address.lower().replace("0x", "").rjust(64, "0")
        try:
            async with self._session.post(
                http,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                      "params": [{"to": asset.contract, "data": data}, "latest"]},
                timeout=_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[PAY] {asset.chain} balance failed: {exc}")
            return None
        raw = (body or {}).get("result")
        if not raw or raw == "0x":
            return None
        return int(raw, 16) / (10 ** asset.decimals)

    async def _sol(self, asset: Asset, owner: str) -> Optional[float]:
        """Every token account this owner holds for the mint, added up.

        Asked by mint rather than by token account so the address in .env can
        be the plain wallet — nobody should have to look up an associated token
        account to take a payment.
        """
        from .scanners import scfg
        http = scfg.SOL_RPC_HTTP
        if not http:
            return None
        try:
            async with self._session.post(
                http,
                json={"jsonrpc": "2.0", "id": 1,
                      "method": "getTokenAccountsByOwner",
                      "params": [owner, {"mint": asset.contract},
                                 {"encoding": "jsonParsed"}]},
                timeout=_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[PAY] sol balance failed: {exc}")
            return None
        rows = ((body or {}).get("result") or {}).get("value") or []
        total = 0.0
        for row in rows:
            info = (((row.get("account") or {}).get("data") or {})
                    .get("parsed") or {}).get("info") or {}
            amount = (info.get("tokenAmount") or {}).get("uiAmount")
            total += float(amount or 0)
        return total

    async def _tron(self, asset: Asset, address: str) -> Optional[float]:
        """TronGrid's public account endpoint. No key needed at this volume."""
        url = (f"{settings.tron_api_url.rstrip('/')}"
               f"/v1/accounts/{address}")
        headers = {"accept": "application/json"}
        if settings.tron_api_key:
            headers["TRON-PRO-API-KEY"] = settings.tron_api_key
        try:
            async with self._session.get(url, headers=headers,
                                         timeout=_TIMEOUT) as resp:
                body = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[PAY] tron balance failed: {exc}")
            return None
        rows = (body or {}).get("data") or []
        if not rows:
            return None
        for entry in rows[0].get("trc20") or []:
            for contract, raw in entry.items():
                if contract == asset.contract:
                    return int(raw) / (10 ** asset.decimals)
        return 0.0
