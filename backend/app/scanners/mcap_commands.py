"""The /mcap_* commands — the same controls the Market Cap Alert section has.

Every one of these writes to the collections the worker reads and the page
shows, so there is one copy of the answer: add a token here and it is on the
page within fifteen seconds, change a target on the page and the next message
from here reports the new one.

Kept out of commands.py because that module is the transport — polling,
permissions, usage counters — and this is one feature's vocabulary.
"""

from __future__ import annotations

import re
import time

from app import registry
from app.scanners import scfg as config
from app.scanners.mcap_price import CHAIN_LABELS
from app.scanners.mcap_tracker import CADENCES, DEFAULT_CADENCE, fmt_usd, parse_usd
from app.util import ist_date_str

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _col(name: str):
    from app import db
    return db.get_collection(name)


def _args(text: str) -> list[str]:
    return text.split()[1:]


async def _settings() -> dict:
    return await _col("mcap_settings").find_one({"_id": "mcap"}) or {}


async def reply(cmd: str, text: str) -> str:
    """One of the /mcap_* commands, answered. Never raises — a bad argument is
    an explanation, not a stack trace in the group."""
    try:
        return await _reply(cmd, text)
    except ValueError as exc:
        return f"⚠️ {exc}"


async def _reply(cmd: str, text: str) -> str:
    args = _args(text)
    if cmd == "mcap":
        return await _status()
    if cmd == "mcap_list":
        return await _list()
    if cmd == "mcap_add":
        return await _add(args)
    if cmd == "mcap_target":
        return await _target(args)
    if cmd == "mcap_remove":
        return await _remove(args)
    if cmd == "mcap_check":
        return await _cadence(args)
    if cmd in ("mcap_on", "mcap_off"):
        return await _switch(cmd == "mcap_on", args)
    return ""


async def _status() -> str:
    doc = await _settings()
    enabled = await registry.enabled_map()
    total = await _col("mcap_tokens").count_documents({})
    hit = await _col("mcap_tokens").count_documents({"hit_at": {"$exists": True}})
    on = [label for key, label in CHAIN_LABELS.items()
          if enabled.get(f"mcap_chain_{key}", True)
          and enabled.get(f"mcap_rpc_{key}", True)]
    return (
        f"🎯 <b>Market Cap Alert</b> — "
        f"{'on' if enabled.get('mcap_tracker', True) else 'OFF'}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🪙 {total} token(s) watched · {hit} already hit\n"
        f"⏱ checked every {doc.get('cadence', DEFAULT_CADENCE)}\n"
        f"⛓ chains on: {', '.join(on) or 'none'}\n"
        f"🔔 alerts → {config.MCAP_ALERT_CHAT_ID or 'not set'}"
    )


async def _list() -> str:
    rows = await _col("mcap_tokens").find({}).sort("added_at", -1).to_list(60)
    if not rows:
        return ("No tokens watched yet — /mcap_add &lt;chain&gt; "
                "&lt;address&gt; &lt;target&gt;")
    states = {}
    async for st in _col("mcap_state").find({}):
        states[(st.get("chain"), st.get("address"))] = st
    lines = ["📋 <b>Market Cap — watched tokens</b>", "➖➖➖➖➖➖➖➖➖➖"]
    for row in rows:
        st = states.get((row.get("chain"), row.get("address")), {})
        now = st.get("mcap")
        lines.append(
            f"{(row.get('symbol') or row['address'][:8]).upper()} "
            f"[{CHAIN_LABELS.get(row.get('chain'), '?')}] · "
            f"{fmt_usd(now) if now else 'reading…'} → {fmt_usd(row.get('target'))}"
            + (" ✅" if row.get("hit_at") else ""))
    return "\n".join(lines)


def _clean_address(address: str, chain: str) -> str:
    if chain == "sol":
        if not _SOL_RE.match(address):
            raise ValueError(f"'{address}' is not a Solana mint address")
        return address
    if not _EVM_RE.match(address):
        raise ValueError(f"'{address}' is not a contract address")
    return address.lower()


async def _add(args: list[str]) -> str:
    """/mcap_add [chain] <address> <target>.

    The chain is optional for EVM addresses because the same 0x… exists on ETH,
    BSC and Robinhood: the chains are asked which of them has a pool for it.
    A Solana mint names its own chain by its shape.
    """
    if len(args) < 2:
        raise ValueError("usage: /mcap_add &lt;chain&gt; &lt;address&gt; "
                         "&lt;target&gt;  — e.g. /mcap_add rbh 0xabc… 250k")
    rest = list(args)
    chain = rest.pop(0).lower() if rest[0].lower() in CHAIN_LABELS else ""
    if not rest:
        raise ValueError("that is a chain, not an address")
    address = rest[0]
    if not chain:
        chain = "sol" if (_SOL_RE.match(address) and not _EVM_RE.match(address)) \
                else await _detect_chain(address)
    address = _clean_address(address, chain)
    if len(rest) < 2:
        raise ValueError("give it a target too — /mcap_add "
                         f"{chain} {address} 250k")
    target = parse_usd(rest[1])
    symbol, name = await _name_symbol(chain, address)
    now = time.time()
    armed = await _armed_for(chain, address, target)
    await _col("mcap_tokens").update_one(
        {"chain": chain, "address": address},
        {"$set": {"chain": chain, "address": address, "target": target,
                  "armed": armed, "symbol": symbol, "name": name,
                  "enabled": True, "added_at": now, "day": ist_date_str(now)},
         "$unset": {"hit_at": "", "hit_mcap": ""}},
        upsert=True)
    way = "rises to" if armed == "up" else "falls to"
    return (f"✅ watching <b>{symbol or '?'}</b> <code>{address}</code> on "
            f"<b>{CHAIN_LABELS.get(chain, chain.upper())}</b>\n"
            f"🎯 alerts when its market cap {way} <b>{fmt_usd(target)}</b>")


async def _target(args: list[str]) -> str:
    if len(args) < 2:
        raise ValueError("usage: /mcap_target &lt;address&gt; &lt;target&gt;")
    address, target = args[0], parse_usd(args[1])
    row = await _col("mcap_tokens").find_one(
        {"address": {"$regex": f"^{re.escape(address)}$", "$options": "i"}})
    if not row:
        raise ValueError(f"{address} is not being watched")
    armed = await _armed_for(row.get("chain", ""), row.get("address", ""), target)
    await _col("mcap_tokens").update_one(
        {"address": row["address"]},
        {"$set": {"target": target, "armed": armed},
         "$unset": {"hit_at": "", "hit_mcap": ""}})
    way = "rises to" if armed == "up" else "falls to"
    return (f"🎯 <b>{(row.get('symbol') or '?').upper()}</b> now alerts when its "
            f"market cap {way} <b>{fmt_usd(target)}</b>")


async def _remove(args: list[str]) -> str:
    if not args:
        raise ValueError("usage: /mcap_remove &lt;address&gt;")
    row = await _col("mcap_tokens").find_one(
        {"address": {"$regex": f"^{re.escape(args[0])}$", "$options": "i"}})
    if not row:
        raise ValueError(f"{args[0]} is not being watched")
    await _col("mcap_tokens").delete_one({"address": row["address"]})
    await _col("mcap_state").delete_many({"address": row["address"]})
    return f"🗑 stopped watching <code>{row['address']}</code>"


async def _cadence(args: list[str]) -> str:
    if not args or args[0] not in CADENCES:
        raise ValueError(f"usage: /mcap_check &lt;{' | '.join(CADENCES)}&gt;")
    await _col("mcap_settings").update_one({"_id": "mcap"},
                                           {"$set": {"cadence": args[0]}}, upsert=True)
    return f"⏱ checking every {args[0]} from now"


async def _switch(on: bool, args: list[str]) -> str:
    word = "on" if on else "off"
    if not args:
        await registry.set_enabled("mcap_tracker", on)
        return f"🎯 Market Cap Alert switched {word}"
    chain = args[0].lower()
    if chain not in CHAIN_LABELS:
        raise ValueError(f"unknown chain '{chain}' — have "
                         f"{', '.join(CHAIN_LABELS)}")
    await registry.set_enabled(f"mcap_chain_{chain}", on)
    return f"⛓ {CHAIN_LABELS[chain]} switched {word} for Market Cap Alert"


async def _armed_for(chain: str, address: str, target: float) -> str:
    """Which way it has to move — see the same rule in routers/mcap.py."""
    st = await _col("mcap_state").find_one({"chain": chain,
                                            "address": address}) or {}
    current = float(st.get("mcap") or 0)
    return "down" if current and target < current else "up"


async def _name_symbol(chain: str, address: str) -> tuple[str, str]:
    import aiohttp
    from app.scanners.mcap_price import MarketCapReader
    try:
        async with aiohttp.ClientSession() as session:
            return await MarketCapReader(session).name_symbol(chain, address)
    except Exception:  # noqa: BLE001
        return "", ""


async def _detect_chain(address: str) -> str:
    """Which EVM chain this address trades on, asked rather than guessed."""
    import aiohttp
    from app.scanners.mcap_price import MarketCapReader
    async with aiohttp.ClientSession() as session:
        found = await MarketCapReader(session).find_chains(address)
    if not found:
        raise ValueError("no pool found for that address on RBH, ETH or BSC — "
                         "name the chain to watch it anyway: "
                         "/mcap_add &lt;chain&gt; &lt;address&gt; &lt;target&gt;")
    if len(found) > 1:
        raise ValueError("that address has a pool on "
                         + " and ".join(c.upper() for c in found)
                         + f" — say which: /mcap_add {found[0]} {address} 250k")
    return found[0]
