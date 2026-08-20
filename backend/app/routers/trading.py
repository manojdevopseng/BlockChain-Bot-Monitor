"""Trading — the account's own paper positions, and the settings behind them.

Every route here is scoped to one account: what it bought, what it holds, what
it would spend next. Nothing is shared and nothing is executed — see
app/trading.py for why the engine records rather than trades.
"""

from __future__ import annotations

import time

import aiohttp
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import db, security, trading

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/settings")
async def get_settings(owner: dict = Depends(security.require_customer)):
    conf = await trading.settings_for(owner["username"])
    # The key is never handed back, only whether one is stored. A field that
    # returns what was typed into it is a field that leaks it to anything that
    # can read one response.
    key = conf.pop("gmgn_key", "")
    conf["gmgn_key_set"] = bool(key)
    conf["chains_available"] = list(trading.CHAINS)
    # Said here rather than only in the UI copy, so any client that grows
    # around this API inherits the warning.
    conf["paper"] = True
    conf["paper_note"] = ("Positions are recorded, not executed. GMGN's trading "
                          "API signs with a private key and serves Solana only, "
                          "so nothing here can place a real order yet.")
    return conf


@router.patch("/settings")
async def patch_settings(payload: dict = Body(...),
                         owner: dict = Depends(security.require_customer)):
    patch = dict(payload)
    # An empty string means "leave it alone", not "clear it" — otherwise every
    # save from a form that does not show the key would wipe it.
    if not str(patch.get("gmgn_key") or "").strip():
        patch.pop("gmgn_key", None)
    if "chains" in patch:
        patch["chains"] = [c for c in patch["chains"] if c in trading.CHAINS]
    for field, lo, hi in (("buy_usd", 1, 100000), ("max_open", 1, 200),
                          ("daily_buys", 1, 500),
                          ("buy_slippage", 0, 100), ("sell_slippage", 0, 100)):
        if field in patch:
            try:
                patch[field] = max(lo, min(hi, float(patch[field])))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{field} must be a number")
    conf = await trading.save_settings(owner["username"], patch)
    conf.pop("gmgn_key", "")
    return {"ok": True, "settings": conf}


@router.get("/positions")
async def positions(status: str = Query("all", pattern="^(all|open|closed)$"),
                    owner: dict = Depends(security.require_customer)):
    flt: dict = {"user": owner["username"]}
    if status != "all":
        flt["status"] = status
    rows = await db.get_collection("trading_positions").find(flt).to_list(500)
    rows.sort(key=lambda r: r.get("opened_at") or 0, reverse=True)
    items = [trading.view(r) for r in rows]

    open_rows = [i for i in items if i["status"] == "open"]
    closed = [i for i in items if i["status"] == "closed"]
    wins = [c for c in closed if c["pnl_usd"] > 0]
    return {
        "items": items,
        "summary": {
            "open": len(open_rows),
            "open_cost": round(sum(i["usd"] for i in open_rows), 2),
            "open_value": round(sum(i["usd"] + i["pnl_usd"] for i in open_rows), 2),
            "unrealised": round(sum(i["pnl_usd"] for i in open_rows), 2),
            "realised": round(sum(i["pnl_usd"] for i in closed)
                              + sum(i["realised_usd"] for i in items), 2),
            "closed": len(closed),
            # Only meaningful once there are closed trades; the page shows it
            # as "—" until then rather than as a confident 0%.
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        },
    }


@router.post("/refresh")
async def refresh(owner: dict = Depends(security.require_customer)):
    async with aiohttp.ClientSession() as s:
        n = await trading.refresh(owner["username"], s)
    return {"priced": n}


@router.post("/buy")
async def buy(payload: dict = Body(...),
              owner: dict = Depends(security.require_customer)):
    """A manual buy, from the Buy button on a detection row."""
    try:
        async with aiohttp.ClientSession() as s:
            row = await trading.open_position(
                user=owner["username"],
                chain=str(payload.get("chain") or ""),
                address=str(payload.get("address") or ""),
                symbol=str(payload.get("symbol") or ""),
                name=str(payload.get("name") or ""),
                usd=float(payload.get("usd") or 0),
                source="manual", session=s)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "position": trading.view(row)}


@router.post("/sell/{pid}")
async def sell(pid: str, payload: dict = Body(default=None),
               owner: dict = Depends(security.require_customer)):
    part = float((payload or {}).get("percent") or 100)
    try:
        async with aiohttp.ClientSession() as s:
            row = await trading.close_position(owner["username"], pid,
                                               part=part, session=s)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "position": trading.view(row)}


@router.post("/demo")
async def demo(owner: dict = Depends(security.require_customer)):
    """Open one position on a token that is definitely priceable.

    For seeing the page work before a real call arrives. Marked `demo` so it
    can be told apart from — and cleared without touching — anything real.
    """
    tokens = [
        ("rbh", "0xe6e766a51495d8e14f8cd1b3469954b0d5cc238f", "MOONCOIN", "MoonCoin"),
        ("eth", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", "Wrapped Ether"),
    ]
    made, failed = [], []
    async with aiohttp.ClientSession() as s:
        for chain, addr, sym, name in tokens:
            try:
                row = await trading.open_position(
                    user=owner["username"], chain=chain, address=addr,
                    symbol=sym, name=name, usd=50.0, source="demo", session=s)
                made.append(trading.view(row))
            except ValueError as exc:
                failed.append(f"{sym}: {exc}")
    if not made:
        raise HTTPException(502, "; ".join(failed) or "no demo token could be priced")
    return {"ok": True, "opened": made, "skipped": failed}


@router.delete("/demo")
async def clear_demo(owner: dict = Depends(security.require_customer)):
    res = await db.get_collection("trading_positions").delete_many(
        {"user": owner["username"], "source": "demo"})
    return {"removed": getattr(res, "deleted_count", 0)}
