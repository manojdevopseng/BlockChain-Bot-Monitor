"""Service registry — the single source of truth for every on/off toggle.

Four categories, each a section of the Settings page:

  Bots   — the individual features: signal forwarding per source channel,
           premium-caller detection per chain, cross-chain flows, gas fees,
           outcome tracking, Telegram commands
  AI     — the X-links feed and the narrative agent that judges it
  Chains — a whole chain on or off
  RPCs   — a chain's RPC endpoints on or off

Each toggle is a document in the `services` collection:
    { id, category, label, enabled, status, chain }

Turning one off pauses the backing worker (via the supervisor) and greys the
service everywhere in the UI. Defaults are seeded on first startup; user changes
persist in the DB and win over the defaults — except `category` and `label`,
which belong to the code (see `seed`).

Naming rule for anything added here: the `id` and the `label` must name the
same thing the rest of the codebase calls it. That was not true for a while —
one channel was the registry's `bbcanalyser2` / label "BBCAnalyser2" / dead
`key` "callanalyser2", while .env, the userbot's logs and the Forwarder page
all called it CallAnalyser2. Four spellings, one channel.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from . import db

# category ids
BOT = "bot"
AI = "ai"
CHAIN = "chain"
RPC = "rpc"
# Its own category so the Settings page can give the feature a section of its
# own rather than scattering six switches through Bots.
RBHX = "rbhx"

# Registry ids that were renamed, old -> new. `seed` carries the user's on/off
# state across so a rename never silently re-enables something they had turned
# off (`bbcanalyser2` was off on the live box when it was renamed — seeding a
# fresh row would have started forwarding to Telegram again).
RENAMED_IDS: dict[str, str] = {
    "bbcanalyser2": "callanalyser2",
    # The panel these feed is called "detections" everywhere the user can see
    # it — the Mongo collection, /api/forwarder/detections, the WS event, the
    # nav item — so the switches now say detection too. `premium_sol_capture`
    # was off, which is exactly why this needs a migration entry and not just
    # a rename.
    "premium_eth_capture": "premium_eth_detection",
    "premium_rbh_capture": "premium_rbh_detection",
    "premium_sol_capture": "premium_sol_detection",
}

# ── Default services (seed) ────────────────────────────────────────────────────
DEFAULT_SERVICES: list[dict] = [
    # ── Bots: the four source channels the userbot forwards from ──
    # Ids and labels match the channel names in .env (SOURCE_*) so the Settings
    # switch, the Forwarder page's source row and the log lines all read alike.
    {"id": "callanalyser2",         "category": BOT, "label": "CallAnalyser2",
     "chain": "eth", "enabled": True},
    {"id": "buybottracker",         "category": BOT, "label": "BuyBotTracker",
     "chain": "eth", "enabled": True},
    {"id": "dexsignalcall",         "category": BOT, "label": "DexSignalCall",
     "chain": "eth", "enabled": True},
    {"id": "eth_otto_group",        "category": BOT, "label": "ETH Otto Group",
     "chain": "eth", "enabled": True},

    # ── Bots: premium groups ──
    # Forwarding + the cross-group ETH caller count. Distinct from the three
    # per-chain detection switches below, which only verify an address on chain
    # and record it in the matching Detections panel.
    {"id": "premium_callers_signal","category": BOT, "label": "Premium Callers Signal",
     "chain": "eth", "enabled": True},
    # ── Robinhood monitors ─────────────────────────────────────────────────
    # Eleven switches for two panels that share one worker. `group` is what
    # keeps them readable: the Settings page draws a block per group in this
    # order, so which switch belongs to which panel is visible instead of
    # remembered. The first switch in each block is that panel's own on/off.
    # Named in full rather than "Section on / off": both blocks have a master
    # switch, and two rows reading the same thing is how they got mistaken for
    # each other.
    {"id": "rbhx_monitor",   "category": RBHX, "group": "X Monitor",
     "label": "Robinhood — X — Token Monitor", "chain": "rbh", "enabled": True},
    {"id": "rbhx_telegram",  "category": RBHX, "group": "X Monitor",
     "label": "Telegram Alerts", "chain": "rbh", "enabled": True},
    {"id": "rbhx_skip",      "category": RBHX, "group": "X Monitor",
     "label": "Skip List (ignored usernames)", "chain": "rbh", "enabled": True},
    {"id": "rbhx_watch",     "category": RBHX, "group": "X Monitor",
     "label": "Watch List (followed usernames)", "chain": "rbh", "enabled": True},
    # Off today: every account is recorded and Min Followers does the filtering.
    # Here so the rule can be tightened later without a code change.
    {"id": "rbhx_verified_only", "category": RBHX, "group": "X Monitor",
     "label": "Verified accounts only", "chain": "rbh", "enabled": False},
    {"id": "rbhx_v2v3",      "category": RBHX, "group": "X Monitor",
     "label": "Include V2 / V3 pairs", "chain": "rbh", "enabled": False},

    # The launchpad-centric panel. Its own switch: it is much higher volume
    # than the X one and you may want one without the other.
    {"id": "launchpad_monitor", "category": RBHX, "group": "Launchpad Monitor",
     "label": "Robinhood Launchpad Monitor", "chain": "rbh", "enabled": True},
    # Separate from the X Monitor's: both post to the same chat and this one is
    # much the louder of the two, so it is the one you turn off when the group
    # gets busy.
    {"id": "launchpad_telegram", "category": RBHX, "group": "Launchpad Monitor",
     "label": "Telegram Alerts", "chain": "rbh", "enabled": True},
    # One switch per launchpad. The id is "launchpad_" + the adapter's own id,
    # so adding Pools.trade is this one line and nothing else. Off means its
    # launches are not read at all — the worker returns before any eth_call, so
    # a launchpad you do not care about costs nothing.
    {"id": "launchpad_pons",   "category": RBHX, "group": "Launchpad Monitor",
     "label": "Pons launches", "chain": "rbh", "enabled": True},
    {"id": "launchpad_pons_v2", "category": RBHX, "group": "Launchpad Monitor",
     "label": "Pons V2 launches", "chain": "rbh", "enabled": True},
    {"id": "launchpad_flap",   "category": RBHX, "group": "Launchpad Monitor",
     "label": "Flap launches", "chain": "rbh", "enabled": True},

    # One socket serves both panels, so this switch is in neither block: it
    # stops both, and it is the one thing here that is not about what to
    # record but about whether anything can be read at all.
    {"id": "rbhx_rpc",       "category": RBHX, "group": "Both sections",
     "label": "RPC Endpoints", "chain": "rbh", "enabled": True},

    # The starred-caller mirror. Its own switch so the filtered feed can be
    # stopped without touching the full one, and the other way round.
    {"id": "important_caller",      "category": BOT, "label": "Important Caller",
     "chain": "eth", "enabled": True},
    {"id": "premium_eth_detection",   "category": BOT, "label": "Premium ETH",
     "chain": "eth", "enabled": True},
    {"id": "premium_rbh_detection",   "category": BOT, "label": "Premium RBH",
     "chain": "rbh", "enabled": True},
    {"id": "premium_sol_detection",   "category": BOT, "label": "Premium SOL",
     "chain": "sol", "enabled": True},
    {"id": "premium_bnb_detection",   "category": BOT, "label": "Premium BNB",
     "chain": "bnb", "enabled": True},

    # ── Bots: cross-chain and gas ──
    {"id": "sol_to_eth",            "category": BOT, "label": "SOL to ETH",
     "chain": "eth", "enabled": True},
    {"id": "sol_to_rbh",            "category": BOT, "label": "SOL to RBH",
     "chain": "rbh", "enabled": True},
    {"id": "eth_gas_fees",          "category": BOT, "label": "ETH Gas Fees",
     "chain": "eth", "enabled": True},

    # ── Bots: infrastructure ──
    # The userbot itself. Every source channel and premium feature above also
    # needs this on — it is the Telethon session they all ride. Off, the whole
    # userbot stops; the individual switches choose what it does while it runs.
    {"id": "forwarder",             "category": BOT, "label": "Forwarder (userbot)",
     "chain": None, "enabled": True},
    # Telegram slash commands (/status, /alerts, …). Runs on the bot token, so
    # it is independent of the forwarder's userbot session.
    {"id": "bot_commands",          "category": BOT, "label": "Bot Commands",
     "chain": None, "enabled": True},
    # Follows every fired alert forward and records what the price did at 15m,
    # 1h, 6h and 24h. Off, the background task is not running at all: no price
    # lookups, no writes — and Analytics, the digest and the group ranking stop
    # gaining new data.
    {"id": "outcome_tracker",       "category": BOT, "label": "Outcome Tracker",
     "chain": None, "enabled": True},
    # Posts each alert's 1h and 24h result as a reply to that alert. Narrower
    # than the tracker: off, measurement continues and only Telegram goes quiet.
    {"id": "outcome_replies",       "category": BOT, "label": "Outcome Replies",
     "chain": None, "enabled": True},

    # ── AI ──
    # PumpPortal's realtime socket — every pump.fun launch with a verified X
    # account, which is what the AI Narrative page's live section lists. Runs
    # without the model, and the model has nothing to judge without it.
    {"id": "x_feed",                "category": AI, "label": "X Links Feed",
     "chain": "sol", "enabled": True},
    # Reads each new pump.fun token's X link, checks the account is verified and
    # asks Grok whether it matches a watched narrative. Needs XAI_API_KEY; idle
    # without one.
    {"id": "ai_agent",              "category": AI, "label": "AI Narrative Agent",
     "chain": "sol", "enabled": False},
    # With no model reachable, record what the gates let through as `pending` —
    # the list the model would be given. Useful for checking the filters, and
    # noisy once they are trusted, so it is a switch of its own.
    {"id": "ai_gate_preview",       "category": AI, "label": "Gate Preview (pending)",
     "chain": "sol", "enabled": True},

    # ── Chains ──
    {"id": "chain_eth", "category": CHAIN, "label": "ETH", "chain": "eth", "enabled": True},
    {"id": "chain_rbh", "category": CHAIN, "label": "RBH", "chain": "rbh", "enabled": True},
    {"id": "chain_sol", "category": CHAIN, "label": "SOL", "chain": "sol", "enabled": True},

    # ── RPCs ──
    {"id": "rpc_eth", "category": RPC, "label": "ETH", "chain": "eth", "enabled": True},
    {"id": "rpc_rbh", "category": RPC, "label": "RBH", "chain": "rbh", "enabled": True},
    {"id": "rpc_sol", "category": RPC, "label": "SOL", "chain": "sol", "enabled": True},
]

# Supervisor registers a callback here so a toggle takes effect live.
_on_change: Optional[Callable[[str, bool], Any]] = None


def on_change(cb: Callable[[str, bool], Any]) -> None:
    global _on_change
    _on_change = cb


async def _migrate_renamed_ids(col) -> None:
    """Carry a renamed service's on/off state onto its new id.

    Runs before seeding, so the new id already exists with the user's own state
    and `seed` leaves it alone. Without this, renaming an id looks harmless but
    means "insert a fresh row at the coded default" — for `bbcanalyser2`, which
    was deliberately off, that default is True, so the rename alone would have
    put a Telegram forwarder back on.
    """
    for old_id, new_id in RENAMED_IDS.items():
        old = await col.find_one({"id": old_id})
        if old is None:
            continue
        if await col.find_one({"id": new_id}) is None:
            await col.update_one({"id": old_id}, {"$set": {"id": new_id}})
            state = "on" if old.get("enabled") else "off"
            print(f"[registry] renamed service {old_id} -> {new_id} (kept {state})")
        else:
            # Both present: the new one already carries the live state, so the
            # stale row is just noise in the Settings list.
            await col.delete_one({"id": old_id})
            print(f"[registry] dropped stale service row {old_id}")


async def seed() -> None:
    """Insert any missing default services (idempotent — never overwrites user state)."""
    col = db.get_collection("services")
    await _migrate_renamed_ids(col)
    for svc in DEFAULT_SERVICES:
        existing = await col.find_one({"id": svc["id"]})
        if existing is None:
            doc = dict(svc)
            doc.setdefault("status", "running" if doc["enabled"] else "stopped")
            doc["updated_at"] = time.time()
            await col.insert_one(doc)
            continue
        # Where a toggle lives and what it is called belong to the code, not to
        # the user — only its on/off state is theirs. Without this, a service
        # moved to a new section or relabelled would keep the old text on every
        # box that had already seeded.
        changes: dict = {}
        if existing.get("category") != svc["category"]:
            changes["category"] = svc["category"]
        if existing.get("label") != svc["label"]:
            changes["label"] = svc["label"]
        # Same for which block inside the section it sits in.
        if existing.get("group") != svc.get("group"):
            changes["group"] = svc.get("group")
        update: dict = {}
        if changes:
            update["$set"] = changes
        # `key` was a leftover mapping to the reference repo's flow names
        # ("flow2", "flow4", "callanalyser2") that nothing has ever read. It
        # only survived to be a third spelling of things named elsewhere.
        if "key" in existing:
            update["$unset"] = {"key": ""}
        if update:
            await col.update_one({"id": svc["id"]}, update)


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
    """All services grouped by category — shape the Settings page consumes.

    Every category is pre-seeded, including AI: relying on setdefault meant the
    key was simply absent if no AI service happened to exist, and the page reads
    `data?.ai ?? []` rather than checking, so a whole section would vanish
    instead of rendering empty.
    """
    out: dict[str, list[dict]] = {BOT: [], AI: [], CHAIN: [], RPC: [], RBHX: []}
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
