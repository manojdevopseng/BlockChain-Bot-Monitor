"""Trading — the account's own paper positions, and the settings behind them.

Every route here is scoped to one account: what it bought, what it holds, what
it would spend next. Nothing is shared and nothing is executed — see
app/trading.py for why the engine records rather than trades.
"""

from __future__ import annotations

import time

import aiohttp
from fastapi import (APIRouter, Body, Depends, HTTPException, Query,
                     Request)

from .. import db, keys, mev, security, trading, wallet

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/settings")
async def get_settings(owner: dict = Depends(security.require_customer)):
    conf = await trading.settings_for(owner["username"])
    conf["chains_available"] = list(trading.CHAINS)
    # Resolved here rather than in the browser: the three-layer merge is the
    # rule for what a setting *is*, and two implementations of one rule
    # eventually disagree.
    conf["chains_conf_resolved"] = {
        c: trading.chain_conf(conf, c) for c in trading.CHAINS}
    # Said here rather than only in the UI copy, so any client that grows
    # around this API inherits the warning.
    live = bool(conf.get("live_trading"))
    conf["paper"] = not live
    conf["day"] = await trading.day_pnl(owner["username"])
    conf["trading_wallet"] = await keys.address_for(owner["username"], "evm")
    conf["paper_note"] = (
        "Live. Buy and Sell spend from your trading wallet at the amount, "
        "slippage and gas each chain's own panel is set to."
        if live else
        "Positions are recorded, not executed. Nothing here signs a "
        "transaction or moves funds.")
    return conf


@router.patch("/settings")
async def patch_settings(payload: dict = Body(...),
                         owner: dict = Depends(security.require_customer)):
    patch = dict(payload)
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
    # Crossing from recording to spending is a deliberate act, so it is
    # allowed through the ordinary settings save like any other switch — but
    # only when the account actually has a wallet to spend from. Turning it on
    # with no key would produce a buy that fails at the last step, after the
    # person believed it was armed.
    if patch.get("live_trading"):
        if not await keys.address_for(owner["username"], "evm"):
            raise HTTPException(
                400, "Create or import an EVM trading wallet before turning "
                     "live trading on — there is nothing to spend from yet.")

    for field, lo, hi in (("max_open", 1, 200),
                          ("daily_buys", 1, 500),
                          ("loss_limit_pct", 0, 100),
                          ("sell_check_min_sells", 0, 100)):
        if field in patch:
            try:
                patch[field] = max(lo, min(hi, float(patch[field])))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{field} must be a number")

    # One chain's execution settings at a time, merged rather than replaced:
    # a save from the Solana panel must not wipe what Ethereum was set to.
    if "chains_conf" in patch:
        incoming = patch.get("chains_conf") or {}
        if not isinstance(incoming, dict):
            raise HTTPException(400, "chains_conf must be an object")
        current = (await trading.settings_for(owner["username"])).get("chains_conf") or {}
        merged = {c: dict(v) for c, v in current.items()}
        for chain, block in incoming.items():
            if chain not in trading.CHAINS:
                raise HTTPException(400, f"{chain} is not a chain this trades on")
            if not isinstance(block, dict):
                raise HTTPException(400, f"{chain} settings must be an object")
            clean = dict(merged.get(chain) or {})
            for field, lo, hi in (("buy_amount", 0, 1_000_000),
                                  ("buy_slippage", 0, 100),
                                  ("sell_slippage", 0, 100),
                                  ("buy_gas_gwei", 0, 100_000),
                                  ("sell_gas_gwei", 0, 100_000),
                                  ("priority_fee", 0, 100_000_000),
                                  # 0 turns a rule off, which is why these
                                  # floor at 0. Take-profit is allowed past
                                  # 100% because a 10x is the whole point.
                                  ("take_profit_pct", 0, 100_000),
                                  ("stop_loss_pct", 0, 100),
                                  ("trailing_pct", 0, 100)):
                if field in block:
                    try:
                        clean[field] = max(lo, min(hi, float(block[field])))
                    except (TypeError, ValueError):
                        raise HTTPException(400, f"{chain}: {field} must be a number")
            if "sell_presets" in block:
                clean["sell_presets"] = [
                    max(1, min(100, int(float(x))))
                    for x in (block["sell_presets"] or []) if str(x).strip()][:6]
            if "mev_protect" in block:
                # Refused rather than stored-and-ignored. A switch that saves
                # on a chain with nothing to route through would show green
                # for ever while every order went out the ordinary way.
                want = bool(block["mev_protect"])
                if want and not mev.available(chain):
                    raise HTTPException(
                        400, f"There is no protected route for {chain.upper()} "
                             f"— nothing to send through.")
                clean["mev_protect"] = want
            merged[chain] = clean
        patch["chains_conf"] = merged
    conf = await trading.save_settings(owner["username"], patch)
    return {"ok": True, "settings": conf}


@router.get("/wallet")
async def wallet_balances(owner: dict = Depends(security.require_customer)):
    """What the trading wallets hold, right now, on every chain.

    Read on request rather than polled. A balance is looked at, not watched —
    putting it on a timer would mean five RPC calls a minute per open tab for
    a number that changes when the person themselves moves funds.
    """
    return await wallet.read(await keys.addresses(owner["username"]))


@router.get("/mev")
async def mev_status(owner: dict = Depends(security.require_customer)):
    """Per chain: is there a protected route, and does it answer.

    Probed rather than assumed. A toggle reading "protected" while the relay
    is unreachable is worse than no toggle at all — the order still goes out,
    the ordinary way, with the switch showing green.
    """
    return {"items": await mev.status(),
            # The credential-free relays a browser wallet can be pointed at.
            # Deliberately not the routes above: those carry the operator's
            # API key, and handing one to a customer hands them the quota.
            "wallet_networks": mev.wallet_networks()}


# ── the trading wallet ──────────────────────────────────────────────────────
#
# Everything below concerns a key that can move money. One rule governs all of
# it: the secret arrives once, over a connection nobody can read, and never
# comes back out. See app/keys.py for what that costs and why it is here.

def _require_secure(request: Request) -> None:
    """Refuse a secret over a connection anyone on the path can read.

    Typed into an http:// page, a key crosses the network in the clear and
    every hop between keeps a copy if it wants one. There is no "probably fine
    on this network" version of that, so the answer is no until the connection
    is encrypted.

    The proxy's header is what settles it: the app itself sits behind nginx on
    plain HTTP by design, so its own view of the scheme is always "http".
    """
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "").lower()
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if proto == "https" or host in ("localhost", "127.0.0.1"):
        return
    raise HTTPException(
        400,
        "This page is not served over HTTPS, so a private key typed here "
        "would cross the network in the clear. The trading wallet stays "
        "locked until the connection is encrypted.")


@router.get("/keys")
async def keys_list(owner: dict = Depends(security.require_customer)):
    """Which trading wallets exist, described by address alone.

    There is no route anywhere that returns a key, and this is the one
    somebody looking for such a route would try first.
    """
    return {"items": await keys.listing(owner["username"]),
            "vault_ready": keys.configured(),
            "kinds": list(keys.KINDS)}


@router.post("/keys/create")
async def keys_create(request: Request, payload: dict = Body(...),
                      owner: dict = Depends(security.require_customer)):
    """A fresh wallet, generated here and sealed immediately.

    Offered ahead of importing because it bounds the loss to whatever the
    person deliberately sends it, instead of to everything they own.
    """
    _require_secure(request)
    try:
        return await keys.create(owner["username"],
                                 str(payload.get("kind") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/keys/import")
async def keys_import(request: Request, payload: dict = Body(...),
                      owner: dict = Depends(security.require_customer)):
    """Take a key the person already holds.

    The response carries the address that key controls and nothing else, and
    the error messages deliberately never quote the input — a key pasted into
    the wrong field must not come back out in a message or a log.
    """
    _require_secure(request)
    try:
        return await keys.import_key(owner["username"],
                                     str(payload.get("kind") or ""),
                                     str(payload.get("private_key") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/keys/{kind}")
async def keys_forget(kind: str,
                      owner: dict = Depends(security.require_customer)):
    """Delete the key. Anything left in a wallet created here becomes
    unreachable the moment this runs — there is no copy anywhere, which is
    the whole point of the vault."""
    if not await keys.forget(owner["username"], kind):
        raise HTTPException(404, "No trading wallet of that kind")
    return {"ok": True, "kind": kind}


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


@router.get("/portfolio")
async def portfolio(owner: dict = Depends(security.require_customer)):
    """Everything the account owns and everything it has done.

    Two different kinds of truth on one screen, which is exactly why they are
    labelled rather than blended. Holdings are read from the chain and are
    what the wallet actually has, whoever put it there. History is this
    account's own trades and nothing else — it cannot know about a swap made
    somewhere else, and pretending otherwise would be inventing a record.
    """
    user = owner["username"]
    conf = await trading.settings_for(user)
    rows = await db.get_collection("trading_positions").find(
        {"user": user}).to_list(1000)
    rows.sort(key=lambda r: r.get("opened_at") or 0, reverse=True)
    items = [trading.view(r) for r in rows]

    open_rows = [i for i in items if i["status"] == "open"]
    closed = [i for i in items if i["status"] == "closed"]
    wins = [c for c in closed if c["pnl_usd"] > 0]

    # Realised is banked money; unrealised is what is still riding. Kept apart
    # because one of them can still go to zero.
    realised = sum(c["pnl_usd"] for c in closed) + sum(
        i.get("realised_usd") or 0 for i in open_rows)
    unrealised = sum(i["pnl_usd"] for i in open_rows)

    return {
        "wallets": await keys.listing(user),
        "balances": await wallet.read(await keys.addresses(user)),
        "holdings": open_rows,
        "history": closed,
        "live_trading": bool(conf.get("live_trading")),
        "summary": {
            "open": len(open_rows),
            "closed": len(closed),
            "wins": len(wins),
            "win_rate": (len(wins) / len(closed) * 100) if closed else 0.0,
            "realised_usd": realised,
            "unrealised_usd": unrealised,
            "total_pnl_usd": realised + unrealised,
            "volume_usd": sum(i["usd"] for i in items),
            "live_trades": len([i for i in items if i.get("live")]),
        },
        "day": await trading.day_pnl(user),
    }
