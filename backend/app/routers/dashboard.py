"""Dashboard summary routes — top stat cards, system overview, live activity."""

from __future__ import annotations


from fastapi import APIRouter

from .. import db, registry, supervisor, watchlist
from ..util import clean_list

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def stats():
    tokens = db.get_collection("tokens")
    alerts = db.get_collection("alerts")
    total_alerts = await alerts.count_documents({})
    total_tokens = await tokens.count_documents({})

    # ETH Gas Fees: high-gas early buys caught by the swap monitors.
    gas_docs = await db.get_collection("gas_alerts").find({}).to_list(500)
    fees = [g.get("fee_eth", 0) for g in gas_docs if g.get("fee_eth")]
    avg_gas = round(sum(fees) / len(fees), 5) if fees else 0.0
    gas_hits = len(fees)

    # The SOL tickers actually being watched, expiry applied — same source the
    # /watching command answers from, so the two can never disagree.
    watching = await watchlist.count()

    return {
        "total_alerts": total_alerts,
        "total_tokens": total_tokens,
        "eth_gas_avg_eth": avg_gas,
        "eth_gas_hits": gas_hits,
        "active_watchlist": watching,
        "cards": [
            {"key": "total_alerts", "label": "Total Alerts", "value": total_alerts},
            {"key": "total_tokens", "label": "Total Tokens", "value": total_tokens},
            {"key": "eth_gas",      "label": "High-Gas Buys", "value": gas_hits},
            {"key": "watchlist",    "label": "Active Watchlist", "value": watching},
        ],
    }


@router.get("/overview")
async def overview():
    """Real component state — the toggle AND whether its worker is alive.

    Reporting a service as connected just because its switch is on hides
    exactly the failure you need to see (the forwarder toggle stays on while
    the userbot is logged out, and then no message is ever sent). State is
    resolved by supervisor.service_states, shared with /api/system/services.
    """
    svcs = await registry.list_services()
    states = supervisor.service_states({s["id"]: bool(s["enabled"]) for s in svcs})

    components = []
    for s in svcs:
        if s["category"] not in ("bot", "chain"):
            continue
        st = states.get(s["id"], {"status": "unknown", "reason": "", "depends_on": None})
        components.append({
            "name": s["label"], "id": s["id"],
            "status": st["status"], "reason": st["reason"],
            "depends_on": st["depends_on"],
        })

    # Health = of the services you asked for, how many are actually working.
    wanted = [c for c in components if c["status"] != "disabled"]
    running = sum(1 for c in wanted if c["status"] == "running")
    health = round(running / len(wanted) * 100) if wanted else 100

    return {
        "overall_health": health,
        "running": running,
        "expected": len(wanted),
        "components": components,
        "uptime_seconds": supervisor.uptime_seconds(),
        "db_backend": db.backend_name(),
    }


@router.get("/activity")
async def activity(limit: int = 8):
    docs = await db.get_collection("alerts").find({}).sort("created_at", -1).limit(limit).to_list(limit)
    return clean_list(docs)


# ── The mixed feed ───────────────────────────────────────────────────────────
#
# One section showing what the three detection panels found, so the interesting
# rows can be read without scrolling three of them. Nothing new is stored: this
# reads the same collections Detections already writes, and the retention on
# them is unchanged.
#
# Quotas per source rather than one newest-first list, and that is the whole
# design. Measured over 24 hours on the live box: 2,865 launchpad rows, 36 gas
# alerts, 0 premium calls. Merged straight by time, launchpad is 98.8% of the
# feed and the rare valuable row — a premium call, a handful a day — is pushed
# off the screen within minutes by the cheapest one. With a quota each, a call
# from 17:53 is still there at 18:30, because only a NEWER CALL can displace it.
_QUOTAS = {"calls": 8, "gas": 8, "launches": 12}

# A launchpad row only joins the mix if it carries a reason. All 2,865 a day
# would bury everything else; these four cut it to about 640, which is roughly
# 27 an hour and sits sensibly beside the gas feed. Each is a thing somebody
# actually acts on.
_LAUNCH_SIGNAL = {"$or": [
    {"matched_keywords": {"$nin": [None, ""]}},      # ~417/day
    {"watched": True},                                # ~7/day
    {"dev_buy_eth": {"$gt": 0.199}},                  # ~157/day
    {"followers": {"$gte": 500}},                     # ~222/day
]}


def _take(wanted: str, limit: int) -> dict[str, int]:
    """How many rows each source may contribute.

    One source asked for by name gets the whole page. Otherwise the quotas are
    scaled to fit the limit, with a floor of one each — because a section that
    silently drops a whole source is worse than one that shows a single row from
    it, and a caller passing a small limit should not be able to defeat the
    reservation by accident.
    """
    if wanted in _QUOTAS:
        return {k: (limit if k == wanted else 0) for k in _QUOTAS}
    total = sum(_QUOTAS.values())
    if limit >= total:
        return dict(_QUOTAS)
    share = {k: max(1, round(v / total * limit)) for k, v in _QUOTAS.items()}
    # Rounding up three ways can overshoot; take the excess off the biggest.
    while sum(share.values()) > limit:
        biggest = max(share, key=lambda k: share[k])
        if share[biggest] <= 1:
            break
        share[biggest] -= 1
    return share


def _ago(*values):
    """The first timestamp that is actually set."""
    for v in values:
        if v:
            return float(v)
    return 0.0


@router.get("/feed")
async def feed(source: str = "all", limit: int = 28):
    """Premium calls, launchpad launches and high-gas buys in one list.

    `source` filters to one of them, which is what the tabs do — and asking for
    one source gives the whole quota for it rather than a third of the page.
    """
    from ..util import gmgn_url

    wanted = source if source in _QUOTAS else "all"
    take = _take(wanted, limit)
    out: list[dict] = []

    if take["calls"]:
        rows = await db.get_collection("premium_detections").find({}).sort(
            "created_at", -1).limit(take["calls"]).to_list(take["calls"])
        for r in rows:
            out.append({
                "source": "calls", "at": _ago(r.get("created_at"), r.get("ts")),
                "chain": r.get("chain") or "", "symbol": r.get("symbol") or "",
                "name": r.get("name") or "", "address": r.get("address") or "",
                # The group chips the Detections table already draws, with the
                # colours set in Forwarder → Premium Groups.
                "groups": r.get("group_entries") or [
                    {"name": g} for g in (r.get("groups") or [])],
                "calls": r.get("count"),
                "keyword": r.get("keyword") or "",
            })

    if take["launches"]:
        rows = await db.get_collection("launchpad_tokens").find(
            _LAUNCH_SIGNAL).sort("open_timestamp", -1).limit(
                take["launches"]).to_list(take["launches"])
        for r in rows:
            out.append({
                "source": "launches",
                "at": _ago(r.get("open_timestamp"), r.get("found_at")),
                "chain": "rbh", "symbol": r.get("symbol") or "",
                "name": r.get("name") or "", "address": r.get("address") or "",
                "launchpad": r.get("launchpad_label") or r.get("launchpad") or "",
                "handle": r.get("handle") or "", "link": r.get("link") or "",
                "followers": r.get("followers") or 0,
                "handle_seq": r.get("handle_seq"),
                "matched_keywords": r.get("matched_keywords") or "",
                "watched": bool(r.get("watched")),
                "dev_buy_eth": r.get("dev_buy_eth"),
            })

    if take["gas"]:
        rows = await db.get_collection("gas_alerts").find({}).sort(
            "created_at", -1).limit(take["gas"]).to_list(take["gas"])
        for r in rows:
            out.append({
                "source": "gas", "at": _ago(r.get("created_at")),
                "chain": r.get("chain") or "eth", "symbol": r.get("symbol") or "",
                "name": r.get("name") or "", "address": r.get("address") or "",
                "fee_eth": r.get("fee_eth"), "age_seconds": r.get("age_seconds"),
                "dex": (r.get("dex") or "").upper(),
            })

    for row in out:
        row["gmgn_url"] = gmgn_url(row["chain"], row["address"])
    out.sort(key=lambda r: r["at"], reverse=True)
    # No trim here. `_take` has already decided how many of each source may
    # appear, and cutting the merged list would undo it: at a limit of 12 that
    # left eleven launches, one gas alert and no calls at all — exactly the
    # crowding the quotas exist to prevent.
    return {"items": clean_list(out),
            "quotas": _QUOTAS,
            "strong_dev_buy_eth": _strong_floor(),
            # Said out loud so the section can explain itself rather than
            # looking like it is dropping rows: it is, on purpose.
            "note": "Launches are filtered to those carrying a keyword, a "
                    "watched account, 500+ followers or a strong dev buy."}


def _strong_floor() -> float:
    from ..scanners import scfg
    return float(getattr(scfg, "RBHX_DEV_BUY_STRONG_ETH", 0.199) or 0.199)
