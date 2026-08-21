"""Trading — the account's own paper positions, and the settings behind them.

Every route here is scoped to one account: what it bought, what it holds, what
it would spend next. Nothing is shared and nothing is executed — see
app/trading.py for why the engine records rather than trades.
"""

from __future__ import annotations

import time

import aiohttp
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import db, security, trading, wallet, wallets

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
    conf["day"] = await trading.day_pnl(owner["username"])
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
    # Checked before it is stored rather than when it is next read: a typo
    # saved silently becomes a chain that reports "could not read" for ever,
    # and the person who typed it has long since moved on.
    if "wallet_evm" in patch or "wallet_sol" in patch:
        conf_now = await trading.settings_for(owner["username"])
        try:
            evm, sol = wallet.valid(
                patch.get("wallet_evm", conf_now.get("wallet_evm", "")),
                patch.get("wallet_sol", conf_now.get("wallet_sol", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if "wallet_evm" in patch:
            patch["wallet_evm"] = evm
        if "wallet_sol" in patch:
            patch["wallet_sol"] = sol

    if "chains" in patch:
        patch["chains"] = [c for c in patch["chains"] if c in trading.CHAINS]
    if "callers" in patch:
        ids = []
        for c in patch["callers"] or []:
            try:
                ids.append(int(c))
            except (TypeError, ValueError):
                continue
        patch["callers"] = ids
    for field, lo, hi in (("buy_usd", 1, 100000), ("max_open", 1, 200),
                          ("daily_buys", 1, 500),
                          ("buy_slippage", 0, 100), ("sell_slippage", 0, 100),
                          # 0 turns a rule off, which is why these floor at 0
                          # rather than at 1. Take-profit is allowed past 100%
                          # because a 10x is the whole point of the exercise.
                          ("take_profit_pct", 0, 100000),
                          ("stop_loss_pct", 0, 100),
                          ("trailing_pct", 0, 100),
                          ("loss_limit_pct", 0, 100),
                          ("sell_check_min_sells", 0, 100)):
        if field in patch:
            try:
                patch[field] = max(lo, min(hi, float(patch[field])))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{field} must be a number")
    conf = await trading.save_settings(owner["username"], patch)
    conf.pop("gmgn_key", "")
    return {"ok": True, "settings": conf}


@router.get("/wallet")
async def wallet_balances(owner: dict = Depends(security.require_customer)):
    """What the saved addresses hold, right now, on all five chains.

    Read on request rather than polled. A balance is looked at, not watched —
    putting it on a timer would mean five RPC calls a minute per open tab for
    a number that changes when the person themselves moves funds.
    """
    evms, sols = await wallets.addresses(owner["username"])
    return await wallet.read(evms, sols)


@router.get("/wallets")
async def wallet_list(owner: dict = Depends(security.require_customer)):
    return {"items": await wallets.listing(owner["username"])}


@router.post("/wallets/nonce")
async def wallet_nonce(payload: dict = Body(default={}),
                       owner: dict = Depends(security.require_customer)):
    """The sentence to sign, and the one-time string inside it.

    Handed out by the server rather than built in the browser: a nonce the
    client chooses is a nonce an attacker chooses.
    """
    address = str(payload.get("address") or "").strip()
    nonce = await wallets.new_nonce(owner["username"])
    return {"nonce": nonce,
            "message": wallets.message_for(owner["username"], address, nonce)}


@router.post("/wallets")
async def wallet_link(payload: dict = Body(...),
                      owner: dict = Depends(security.require_customer)):
    try:
        return await wallets.link(
            username=owner["username"],
            kind=str(payload.get("kind") or ""),
            address=str(payload.get("address") or ""),
            signature=str(payload.get("signature") or ""),
            nonce=str(payload.get("nonce") or ""),
            source=str(payload.get("source") or "manual"),
            label=str(payload.get("label") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/wallets/manual")
async def wallet_manual(payload: dict = Body(...),
                        owner: dict = Depends(security.require_customer)):
    """Watch an address without proving it. Marked unverified, and stays so."""
    try:
        return await wallets.add_manual(
            username=owner["username"],
            kind=str(payload.get("kind") or ""),
            address=str(payload.get("address") or ""),
            label=str(payload.get("label") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/wallets/{address}")
async def wallet_unlink(address: str,
                        owner: dict = Depends(security.require_customer)):
    """Unlink. There is nothing to revoke on the chain — we were only reading,
    and the wallet never granted this app anything to give back."""
    gone = await wallets.unlink(owner["username"], address)
    if not gone:
        raise HTTPException(404, "That wallet is not linked to this account")
    return {"ok": True, "address": address}


@router.delete("/gmgn-key")
async def gmgn_disconnect(owner: dict = Depends(security.require_customer)):
    """Forget the stored GMGN key.

    Its own route rather than a blank string through the settings PATCH,
    because that path deliberately treats blank as "leave it alone" — which is
    right for a form that cannot show what it holds, and would otherwise make
    removing the key impossible.
    """
    await db.get_collection("trading_settings").update_one(
        {"user": owner["username"]}, {"$unset": {"gmgn_key": ""}})
    return {"ok": True}


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
                                               part=part, session=s,
                                               reason="manual")
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


@router.post("/stop")
async def stop(payload: dict = Body(default=None),
               owner: dict = Depends(security.require_customer)):
    """The kill switch. One press and nothing buys on its own any more.

    Open positions are left exactly as they are, and so are the auto-sell
    rules: this stops the account taking on anything new, it does not abandon
    what it is already holding. Turning auto-buy back on clears the reason.
    """
    why = str((payload or {}).get("reason") or "").strip() or "kill switch"
    conf = await trading.stop_auto_buy(owner["username"], why)
    conf.pop("gmgn_key", "")
    return {"ok": True, "settings": conf}


@router.get("/callers")
async def callers(owner: dict = Depends(security.require_customer)):
    """Which callers made money and which ate it.

    `available` is the starred groups, for the picker that decides whose calls
    auto-buy follows — the same list the sound alert uses, so a group starred
    once is starred everywhere.
    """
    return {"items": await trading.caller_stats(owner["username"]),
            "available": await trading.starred_callers()}


@router.post("/rules")
async def rules(owner: dict = Depends(security.require_customer)):
    """Run the auto-sell and loss-limit pass now, rather than on the minute.

    The same code the background worker runs — so a rule that would fire in
    forty seconds can be made to fire immediately, and what it did comes back
    in the response instead of only reaching a log.
    """
    async with aiohttp.ClientSession() as s:
        return await trading.run_rules(owner["username"], s)
