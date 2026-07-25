"""Demo-data seeding.

Populates chains / rpc_endpoints / commands / forwarder sources / a handful of
tokens, alerts and logs so the dashboard is fully populated during Phase 1-2
(before the real scanners come online in Phase 3). Every seeder is idempotent —
it only fills a collection that is currently empty, so real data is never
clobbered once scanners start writing.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db

_NOW = time.time


_SEED_FILE = Path(__file__).resolve().parent / "data" / "seed_data.json"


def _dt(ts: float) -> datetime:
    """BSON Date for the TTL index (seeded demo rows expire like real ones)."""
    return datetime.fromtimestamp(ts, timezone.utc)


def _seed_data() -> dict:
    try:
        return json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _empty(name: str) -> bool:
    return (await db.get_collection(name).count_documents({})) == 0


async def seed_chains() -> None:
    if not await _empty("chains"):
        return
    chains = [
        {"id": "sol",  "name": "Solana",       "symbol": "SOL",  "latency_ms": 112, "tps": 2142, "block_height": 257812341, "uptime": 99.98, "errors_24h": 2, "status": "connected"},
        {"id": "eth",  "name": "Ethereum",     "symbol": "ETH",  "latency_ms": 98,  "tps": 18.2, "block_height": 19872341,  "uptime": 99.95, "errors_24h": 3, "status": "connected"},
        {"id": "rbh",  "name": "Robinhood Chain", "symbol": "RBH", "latency_ms": 125, "tps": 128, "block_height": 4281993,   "uptime": 99.90, "errors_24h": 1, "status": "connected"},
        {"id": "base", "name": "Base",         "symbol": "BASE", "latency_ms": 145, "tps": 56.7, "block_height": 12341221,  "uptime": 99.88, "errors_24h": 5, "status": "connected"},
        {"id": "arb",  "name": "Arbitrum One", "symbol": "ARB",  "latency_ms": 168, "tps": 32.1, "block_height": 8772112,   "uptime": 99.81, "errors_24h": 6, "status": "connected"},
        {"id": "poly", "name": "Polygon",      "symbol": "MATIC","latency_ms": 156, "tps": 78.4, "block_height": 56778991,  "uptime": 99.83, "errors_24h": 4, "status": "connected"},
        {"id": "avax", "name": "Avalanche C",  "symbol": "AVAX", "latency_ms": 134, "tps": 24.8, "block_height": 4512981,   "uptime": 99.91, "errors_24h": 2, "status": "connected"},
    ]
    await db.get_collection("chains").insert_many(chains)


async def seed_rpc() -> None:
    if not await _empty("rpc_endpoints"):
        return
    eps = [
        {"name": "Solana Alchemy",   "chain": "sol",  "url": "https://solana-mainnet.g.alchemy.com/v2/****", "status": "healthy",  "latency_ms": 78,  "uptime": 99.95, "requests_1h": 52341, "error_rate": 0.15},
        {"name": "Solana Helius",    "chain": "sol",  "url": "https://rpc.helius.xyz/?api-key=****",         "status": "healthy",  "latency_ms": 65,  "uptime": 99.98, "requests_1h": 48912, "error_rate": 0.08},
        {"name": "Solana Public",    "chain": "sol",  "url": "https://api.mainnet-beta.solana.com",          "status": "degraded", "latency_ms": 286, "uptime": 97.23, "requests_1h": 23118, "error_rate": 2.34},
        {"name": "Ethereum Alchemy", "chain": "eth",  "url": "https://eth-mainnet.g.alchemy.com/v2/****",    "status": "healthy",  "latency_ms": 112, "uptime": 99.93, "requests_1h": 61223, "error_rate": 0.18},
        {"name": "Ethereum Infura",  "chain": "eth",  "url": "https://mainnet.infura.io/v3/****",            "status": "healthy",  "latency_ms": 135, "uptime": 99.90, "requests_1h": 55882, "error_rate": 0.22},
        {"name": "Robinhood RPC",    "chain": "rbh",  "url": "https://rpc.robinhoodchain.com",               "status": "healthy",  "latency_ms": 95,  "uptime": 99.96, "requests_1h": 32441, "error_rate": 0.10},
        {"name": "Robinhood Backup", "chain": "rbh",  "url": "https://backup.rpc.robinhoodchain.com",        "status": "down",     "latency_ms": 0,   "uptime": 0.0,   "requests_1h": 0,     "error_rate": 100.0},
        {"name": "Arbitrum One",     "chain": "arb",  "url": "https://arb1.arbitrum.io/rpc",                 "status": "healthy",  "latency_ms": 128, "uptime": 99.92, "requests_1h": 28776, "error_rate": 0.21},
        {"name": "Polygon Mainnet",  "chain": "poly", "url": "https://polygon-rpc.com",                      "status": "healthy",  "latency_ms": 142, "uptime": 99.91, "requests_1h": 21778, "error_rate": 0.19},
        {"name": "Base Mainnet",     "chain": "base", "url": "https://mainnet.base.org",                     "status": "healthy",  "latency_ms": 118, "uptime": 99.93, "requests_1h": 24991, "error_rate": 0.16},
        {"name": "Avalanche C-Chain","chain": "avax", "url": "https://api.avax.network/ext/bc/C/rpc",        "status": "healthy",  "latency_ms": 110, "uptime": 99.94, "requests_1h": 17226, "error_rate": 0.11},
        {"name": "BSC Mainnet",      "chain": "bsc",  "url": "https://bsc-dataseed.binance.org",             "status": "healthy",  "latency_ms": 120, "uptime": 99.90, "requests_1h": 19142, "error_rate": 0.20},
    ]
    await db.get_collection("rpc_endpoints").insert_many(eps)


async def seed_commands() -> None:
    if not await _empty("commands"):
        return
    cmds = [
        {"command": "/start",    "description": "Start the bot and show welcome message", "category": "General",  "permission": "Everyone",   "usage_24h": 2341, "success_rate": 99.91, "enabled": True},
        {"command": "/help",     "description": "Show all available commands",            "category": "General",  "permission": "Everyone",   "usage_24h": 1892, "success_rate": 100.0, "enabled": True},
        {"command": "/alerts",   "description": "View recent alerts",                     "category": "Alerts",   "permission": "Everyone",   "usage_24h": 1455, "success_rate": 99.79, "enabled": True},
        {"command": "/tokens",   "description": "View latest tokens",                     "category": "Tokens",   "permission": "Everyone",   "usage_24h": 2124, "success_rate": 99.91, "enabled": True},
        {"command": "/watchlist","description": "Manage your watchlist",                  "category": "Tokens",   "permission": "Registered", "usage_24h": 892,  "success_rate": 99.88, "enabled": True},
        {"command": "/price",    "description": "Get price of a token",                   "category": "Tokens",   "permission": "Everyone",   "usage_24h": 2987, "success_rate": 99.73, "enabled": True},
        {"command": "/chains",   "description": "View all chain status",                  "category": "System",   "permission": "Everyone",   "usage_24h": 644,  "success_rate": 100.0, "enabled": True},
        {"command": "/status",   "description": "Bot and system status",                  "category": "System",   "permission": "Everyone",   "usage_24h": 1203, "success_rate": 100.0, "enabled": True},
        {"command": "/settings", "description": "User notification settings",             "category": "Settings", "permission": "Registered", "usage_24h": 321,  "success_rate": 99.69, "enabled": True},
        {"command": "/language", "description": "Change language",                        "category": "Settings", "permission": "Registered", "usage_24h": 198,  "success_rate": 100.0, "enabled": True},
    ]
    await db.get_collection("commands").insert_many(cmds)


async def seed_forwarder() -> None:
    if await _empty("forwarder_sources"):
        await db.get_collection("forwarder_sources").insert_many([
            {"name": "CallAnalyser2",     "subtitle": "First call + ETH Addr", "type": "Channel", "status": "connected", "today": 2145, "enabled": True},
            {"name": "BuyBotTracker",     "subtitle": "New group added (eth)",  "type": "Channel", "status": "connected", "today": 1987, "enabled": True},
            {"name": "dexssignal",        "subtitle": "ETH address signals",    "type": "Channel", "status": "connected", "today": 1563, "enabled": True},
            {"name": "OttoEthDeployments","subtitle": "Method hash match",      "type": "Channel", "status": "connected", "today": 1248, "enabled": True},
            {"name": "Premium Groups",    "subtitle": "174 groups",             "type": "Multi-Group", "status": "connected", "today": 4892, "enabled": True},
            {"name": "Onigitracker_bot",  "subtitle": "X (Twitter) Calls",      "type": "Channel", "status": "connected", "today": 713,  "enabled": True},
        ])
    if await _empty("forwarder_dests"):
        await db.get_collection("forwarder_dests").insert_many([
            {"group": "DEST_SIGNALS",     "visibility": "Public",  "purpose": "Main signals (Calls + BuyBot)", "today": 3245, "status": "active"},
            {"group": "DEST_DEXS",        "visibility": "Public",  "purpose": "DEX signals (dexssignal)",       "today": 1563, "status": "active"},
            {"group": "DEST_OTTO",        "visibility": "Public",  "purpose": "Otto deployments",               "today": 1248, "status": "active"},
            {"group": "DEST_PREMIUM_ALL", "visibility": "Private", "purpose": "All premium messages (raw)",     "today": 4892, "status": "active"},
            {"group": "DEST_PREMIUM_ETH", "visibility": "Private", "purpose": "ETH only (max 2 groups)",        "today": 632,  "status": "active"},
        ])


async def seed_keywords() -> None:
    """Detection keywords — seeded from seed_data.json (defaults live in JSON,
    not code). After this, user add/remove persists in Mongo."""
    if await _empty("keywords"):
        words = _seed_data().get("detection_keywords", [])
        if words:
            await db.get_collection("keywords").insert_many([{"word": w} for w in words])


async def seed_premium_groups() -> None:
    """Built-in premium groups — seeded from JSON into the `premium_groups`
    collection. Groups the user adds via the dashboard persist here too, so
    nothing is hardcoded in the forwarder."""
    if await _empty("premium_groups"):
        ids = _seed_data().get("premium_groups", [])
        if ids:
            await db.get_collection("premium_groups").insert_many([
                {"id": int(g), "name": None, "username": None,
                 "builtin": True, "enabled": True, "added_at": _NOW()}
                for g in ids
            ])


async def seed_otto_rules() -> None:
    """Otto method/function/rugger hash sets — seeded from JSON as one config doc."""
    if await _empty("otto_rules"):
        rules = _seed_data().get("otto_rules", {})
        await db.get_collection("otto_rules").insert_one({
            "_key": "default",
            "method_ids": rules.get("method_ids", []),
            "function_texts": rules.get("function_texts", []),
            "rugger_hashes": rules.get("rugger_hashes", []),
        })


async def seed_filter_keywords() -> None:
    """CallAnalyser2 / BuyBotTracker trigger keywords — from JSON as one config doc."""
    if await _empty("filter_keywords"):
        fk = _seed_data().get("filter_keywords", {})
        await db.get_collection("filter_keywords").insert_one({
            "_key": "default",
            "call": fk.get("call", []),
            "buybot": fk.get("buybot", []),
        })


async def seed_activity() -> None:
    """A few tokens / alerts / logs so lists aren't empty in Phase 1-2."""
    if await _empty("tokens"):
        base = _NOW()
        samples = [
            ("BEAN", "robinhood", "0x37ad...7777", "BEAN/USDC", 14_990_000, 2_100_000, "new"),
            ("SHILOH", "robinhood", "0xd048...7777", "SHILOH/USDC", 5_680_000, 1_200_000, "new"),
            ("CAFFI", "robinhood", "0xdad5...017b", "CAFFI/USDC", 47_120, 1_800_000, "new"),
            ("303", "robinhood", "0xd211...7777", "303/USDC", 8_210, 890_000, "watching"),
            ("SHYRO", "solana", "8xYg...pump", "SHYRO/SOL", 3_450_000, 860_000, "migrated"),
            ("LUMEN", "ethereum", "0xabc1...9f34", "LUMEN/ETH", 1_980_000, 560_000, "new"),
        ]
        docs = []
        for i, (sym, chain, addr, pair, mcap, vol, typ) in enumerate(samples):
            docs.append({
                "symbol": sym, "chain": chain, "address": addr, "pair": pair,
                "mcap_usd": mcap, "volume_24h": vol, "type": typ,
                "fee_eth": round(random.uniform(0.0008, 0.006), 5),
                "created_at": base - i * 3600, "dt": _dt(base - i * 3600),
            })
        await db.get_collection("tokens").insert_many(docs)

    if await _empty("alerts"):
        base = _NOW()
        rows = [
            ("New Token Detected", "high",   "robinhood", "New token detected: BEAN", "new"),
            ("Forwarded",          "medium", "forwarder", "Message forwarded to Telegram", "new"),
            ("ETH Pair Found",     "medium", "ethereum",  "ETH pair found for CAFFI", "new"),
            ("Watching Added",     "low",    "solana",    "Added to watchlist: 303", "new"),
            ("RPC Disconnected",   "high",   "robinhood", "Robinhood WebSocket disconnected", "new"),
            ("Volume Spike",       "medium", "solana",    "Volume spike: NEITZSHCE $12.45K", "new"),
        ]
        docs = [{
            "type": t, "severity": s, "chain": c, "message": m, "status": st,
            "created_at": base - i * 90, "dt": _dt(base - i * 90),
        } for i, (t, s, c, m, st) in enumerate(rows)]
        await db.get_collection("alerts").insert_many(docs)

    if await _empty("logs"):
        base = _NOW()
        rows = [
            ("INFO",  "Forwarder",        "New token forwarded: BEAN -> DEST_SIGNALS"),
            ("INFO",  "Sol Scanner",      "New token detected: BEAN via Pump.fun"),
            ("DEBUG", "Sol Scanner",      "Watching pair: BEAN/USDC on Pump.fun"),
            ("WARN",  "Eth Scanner",      "No new blocks detected in last 15 seconds"),
            ("ERROR", "Rpc Monitor",      "RPC Error: 429 Too Many Requests (helius-rpc)"),
            ("INFO",  "Robinhood Scanner","New token: SHILOH | MCAP: $5,680,243"),
        ]
        docs = [{
            "level": lv, "service": svc, "message": msg, "ts": base - i * 2, "dt": _dt(base - i * 2),
        } for i, (lv, svc, msg) in enumerate(rows)]
        await db.get_collection("logs").insert_many(docs)


async def seed_detections() -> None:
    """Sample premium-caller detections (eth/rbh/sol) so the Detections panels
    render immediately. Real rows are written live by the forwarder's
    _capture_premium_* once it runs."""
    if not await _empty("premium_detections"):
        return
    base = _NOW()

    def _entries(names):
        return [{"chat_id": 1000000 + i, "name": n, "username": None, "message_id": None}
                for i, n in enumerate(names)]

    rows = [
        # chain, symbol, name, address, groups, keyword, minutes_ago
        ("rbh", "Penny", "Cash Cat", "0xd831aa00000000000000000000000000000e0d8", ["B4ochan - Trading Journey"], "aped", 27),
        ("rbh", "VLTR", "Vaultler Protocol", "0x4cae000000000000000000000000000000007613", ["Gems of Mena", "Micha - Micha Calls"], "AI", 33),
        ("rbh", "4663", "GenesisWall4663", "0xef76000000000000000000000000000000000ced", ["Gems of Mena"], "", 34),
        ("rbh", "DEFAI", "DEFAI Creator", "0x4d36000000000000000000000000000000004548", ["Doxxed Gamble Club", "Doxxed GEM Club"], "agents, aped, AI", 52),
        ("eth", "LUMEN", "Lumen Protocol", "0xabc1000000000000000000000000000000009f34", ["Kingdom X100", "watisdes"], "AI", 12),
        ("eth", "OTTO", "Otto Finance", "0x9052000000000000000000000000000000006c00", ["OttoEthDeployments"], "Agent", 25),
        ("eth", "NODEX", "Node X", "0x1f04000000000000000000000000000000000dd1", ["gigacalz", "MENACalls", "x100apes"], "node", 40),
        ("sol", "BONKAI", "Bonk AI", "7xJhpumpBonkAi000000000000000000000000000000", ["JusticeCalls", "MoonCalls"], "AI", 18),
        ("sol", "PEPESOL", "Pepe on Sol", "8xYgpumpPepe0000000000000000000000000000000", ["npcalls"], "", 44),
    ]
    docs = []
    for chain, sym, name, addr, groups, kw, mins in rows:
        docs.append({
            "chain": chain, "symbol": sym, "name": name, "address": addr, "pair": None,
            "group_entries": _entries(groups), "groups": groups,
            "group_ids": [1000000 + i for i in range(len(groups))],
            "count": len(groups), "keyword": kw, "ts": base - mins * 60,
        })
    await db.get_collection("premium_detections").insert_many(docs)


async def seed_crosschain() -> None:
    """Sample SOL→ETH / SOL→RBH matches so those panels render before the
    scanners fire real ones (they write the identical shape)."""
    col = db.get_collection("alerts")
    if await col.count_documents({"type": "Cross-Chain Match"}):
        return
    base = _NOW()
    rows = [
        # chain, symbol, token_addr, sol_addr, dex, mcap, fee_eth, mins_ago
        ("eth", "LUMEN", "0xabc1000000000000000000000000000000009f34", "9xLuMenpump000000000000000000000000000000000", "v3", 78000, 0.00412, 14),
        ("eth", "NODEX", "0x1f04000000000000000000000000000000000dd1", "5xNodeXpump00000000000000000000000000000000", "v2", 52000, 0.00287, 61),
        ("robinhood", "VLTR", "0x4cae000000000000000000000000000000007613", "3xVltrpump000000000000000000000000000000000", "noxa", 91000, None, 33),
        ("robinhood", "DEFAI", "0x4d36000000000000000000000000000000004548", "6xDefaipump00000000000000000000000000000000", "noxa", 64000, None, 52),
    ]
    docs = []
    for chain, sym, addr, sol_addr, dex, mcap, fee, mins in rows:
        docs.append({
            "type": "Cross-Chain Match", "severity": "high", "chain": chain,
            "message": f"{sym} → {sym} matched on {chain} ({dex})",
            "status": "new", "created_at": base - mins * 60, "dt": _dt(base - mins * 60),
            "token_symbol": sym, "token_address": addr,
            "tx_hash": f"0x{'ab' * 32}", "fee_eth": fee,
            "sol_address": sol_addr, "sol_mcap_usd": mcap, "dex": dex,
        })
    await col.insert_many(docs)


async def seed_all() -> None:
    await seed_chains()
    await seed_rpc()
    await seed_commands()
    await seed_forwarder()
    await seed_keywords()
    await seed_premium_groups()
    await seed_otto_rules()
    await seed_filter_keywords()
    await seed_detections()
    await seed_crosschain()
    await seed_activity()
