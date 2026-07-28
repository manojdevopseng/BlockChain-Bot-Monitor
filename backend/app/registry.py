"""Service registry — the single source of truth for every on/off toggle.

Categories (exactly as specified by the product owner):

  Bots:   ETH Gas Fees, Premium Callers Signal, DexSignalCall, BBCAnalyser2,
          ETH otto Group, SOL to ETH, SOL to RBH, Forwarder
  Chains: ETH, RBH, SOL
  RPCs:   ETH, RBH, SOL

Each toggle is a document in the `services` collection:
    { id, category, label, enabled, status, chain, meta }

Turning one off pauses the backing worker (via the supervisor) and greys the
service everywhere in the UI. Defaults are seeded on first startup; user changes
persist in the DB and win over the defaults.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from . import db

# category ids
BOT = "bot"
CHAIN = "chain"
RPC = "rpc"

# ── Default services (seed) ────────────────────────────────────────────────────
# `key` maps a toggle to the underlying flow/flag the scanner layer reads.
DEFAULT_SERVICES: list[dict] = [
    # ── Bots ──
    {"id": "eth_gas_fees",          "category": BOT, "label": "ETH Gas Fees",
     "chain": "eth", "key": "gas_fees",        "enabled": True},
    {"id": "premium_callers_signal","category": BOT, "label": "Premium Callers Signal",
     "chain": "eth", "key": "premium_callers",  "enabled": True},
    {"id": "dexsignalcall",         "category": BOT, "label": "DexSignalCall",
     "chain": "eth", "key": "dexssignal",       "enabled": True},
    {"id": "bbcanalyser2",          "category": BOT, "label": "BBCAnalyser2",
     "chain": "eth", "key": "callanalyser2",    "enabled": True},
    {"id": "eth_otto_group",        "category": BOT, "label": "ETH otto Group",
     "chain": "eth", "key": "otto",             "enabled": True},
    {"id": "sol_to_eth",            "category": BOT, "label": "SOL to ETH",
     "chain": "eth", "key": "flow2",            "enabled": True},
    {"id": "sol_to_rbh",            "category": BOT, "label": "SOL to RBH",
     "chain": "rbh", "key": "flow_robinhood",   "enabled": True},
    {"id": "forwarder",             "category": BOT, "label": "Forwarder",
     "chain": None,  "key": "flow4",            "enabled": True},
    # Telegram slash commands (/status, /alerts, …). Runs on the bot token, so
    # it is independent of the forwarder's userbot session.
    {"id": "bot_commands",          "category": BOT, "label": "Bot Commands",
     "chain": None,  "key": "commands",         "enabled": True},
    # Follows every fired alert forward and records what the price did at 15m,
    # 1h, 6h and 24h. Off, the background task is not running at all: no price
    # lookups, no writes — and Analytics, the digest and the group ranking stop
    # gaining new data.
    {"id": "outcome_tracker",       "category": BOT, "label": "Outcome Tracker",
     "chain": None,  "key": "outcome_tracker",  "enabled": True},
    # Posts each alert's 1h and 24h result as a reply to that alert. Narrower
    # than the tracker: off, measurement continues and only Telegram goes quiet.
    {"id": "outcome_replies",       "category": BOT, "label": "Outcome Replies",
     "chain": None,  "key": "outcome_replies",  "enabled": True},

    # ── Chains ──
    {"id": "chain_eth", "category": CHAIN, "label": "ETH", "chain": "eth",
     "key": "chain_eth", "enabled": True},
    {"id": "chain_rbh", "category": CHAIN, "label": "RBH", "chain": "rbh",
     "key": "chain_rbh", "enabled": True},
    {"id": "chain_sol", "category": CHAIN, "label": "SOL", "chain": "sol",
     "key": "chain_sol", "enabled": True},

    # ── RPCs ──
    {"id": "rpc_eth", "category": RPC, "label": "ETH", "chain": "eth",
     "key": "rpc_eth", "enabled": True},
    {"id": "rpc_rbh", "category": RPC, "label": "RBH", "chain": "rbh",
     "key": "rpc_rbh", "enabled": True},
    {"id": "rpc_sol", "category": RPC, "label": "SOL", "chain": "sol",
     "key": "rpc_sol", "enabled": True},
]

# Supervisor registers a callback here so a toggle takes effect live.
_on_change: Optional[Callable[[str, bool], Any]] = None


def on_change(cb: Callable[[str, bool], Any]) -> None:
    global _on_change
    _on_change = cb


async def seed() -> None:
    """Insert any missing default services (idempotent — never overwrites user state)."""
    col = db.get_collection("services")
    for svc in DEFAULT_SERVICES:
        existing = await col.find_one({"id": svc["id"]})
        if existing is None:
            doc = dict(svc)
            doc.setdefault("status", "running" if doc["enabled"] else "stopped")
            doc["updated_at"] = time.time()
            await col.insert_one(doc)


def _clean(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


async def list_services(category: Optional[str] = None) -> list[dict]:
    col = db.get_collection("services")
    flt = {"category": category} if category else {}
    docs = await col.find(flt).to_list(200)
    order = {s["id"]: i for i, s in enumerate(DEFAULT_SERVICES)}
    docs.sort(key=lambda d: order.get(d.get("id"), 999))
    return [_clean(d) for d in docs]


async def grouped() -> dict[str, list[dict]]:
    """All services grouped by category — shape the Settings page consumes."""
    out: dict[str, list[dict]] = {BOT: [], CHAIN: [], RPC: []}
    for svc in await list_services():
        out.setdefault(svc["category"], []).append(svc)
    return out


async def get_service(service_id: str) -> Optional[dict]:
    doc = await db.get_collection("services").find_one({"id": service_id})
    return _clean(doc) if doc else None


async def is_enabled(service_id: str) -> bool:
    svc = await get_service(service_id)
    return bool(svc and svc.get("enabled"))


async def set_enabled(service_id: str, enabled: bool) -> Optional[dict]:
    col = db.get_collection("services")
    svc = await col.find_one({"id": service_id})
    if not svc:
        return None
    await col.update_one(
        {"id": service_id},
        {"$set": {
            "enabled": enabled,
            "status": "running" if enabled else "stopped",
            "updated_at": time.time(),
        }},
    )
    # Notify the supervisor so the change is live (start/stop the worker).
    if _on_change is not None:
        try:
            res = _on_change(service_id, enabled)
            if hasattr(res, "__await__"):
                await res
        except Exception as exc:  # noqa: BLE001
            print(f"[registry] on_change failed for {service_id}: {exc}")
    return await get_service(service_id)


async def enabled_map() -> dict[str, bool]:
    """{service_id: enabled} — cheap lookup for other layers."""
    return {s["id"]: bool(s["enabled"]) for s in await list_services()}
