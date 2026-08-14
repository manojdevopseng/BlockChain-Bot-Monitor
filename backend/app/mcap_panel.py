"""The Market Cap Alert screen on Telegram — buttons, not commands.

    ┌ Market Cap Alert ───────────────
    │ [Watcher ✅] [Alerts ✅]
    │ [RBH ✅] [ETH ✅] [BSC ✅] [SOL ✅]
    │ check [15s•] [30s] [1m] [5m]
    │ [➕ Add token] [🪙 Tokens (3)] [🔄]
    └──────────────────────────────────

One message, edited in place, so pressing things does not fill the chat. Adding
takes two messages because Telegram has no text field on a keyboard: the chain
is a button, then the address and the target arrive as one ordinary message
("0xabc… 250k").

Callback data is `mc:<what>:<arg>` and stays under Telegram's 64 bytes — an EVM
address alone is 42 of them, which is why the per-token buttons carry a prefix
and look the full one up.
"""

from __future__ import annotations

import time
from typing import Optional

from . import db, notifier, registry
from .scanners import scfg as config
from .scanners.mcap_price import CHAIN_LABELS
from .scanners.mcap_tracker import CADENCES, DEFAULT_CADENCE, fmt_usd
from .scanners.slog import get_logger

log = get_logger(__name__)

_TOKEN_LIMIT = 20
# chat id -> (chain, when the chain was chosen). An abandoned add must not
# swallow an unrelated message an hour later.
_PENDING: dict[int, tuple[str, float]] = {}
_PENDING_SECONDS = 300.0


def _col(name: str):
    return db.get_collection(name)


async def _settings() -> dict:
    return await _col("mcap_settings").find_one({"_id": "mcap"}) or {}


def _mark(on: bool) -> str:
    return "✅" if on else "⛔"


# ── the main screen ───────────────────────────────────────────────────────────

async def main_panel() -> tuple[str, list[list[dict]]]:
    doc = await _settings()
    enabled = await registry.enabled_map()
    cadence = str(doc.get("cadence", DEFAULT_CADENCE))
    total = await _col("mcap_tokens").count_documents({})
    hit = await _col("mcap_tokens").count_documents({"hit_at": {"$exists": True}})

    text = (
        f"🎯 <b>Market Cap Alert — settings</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🪙 {total} token(s) watched · {hit} already hit\n"
        f"⏱ checked every <b>{cadence}</b> — one request per token per pass\n"
        f"🔔 → {config.MCAP_ALERT_CHAT_ID or 'no chat set'}\n\n"
        f"<i>A token stays here until you remove it, target and all. Set a "
        f"target above where it is now and it fires on the way up; below, and "
        f"it fires on the way down.</i>"
    )

    rows: list[list[dict]] = [[
        {"text": f"Watcher {_mark(enabled.get('mcap_tracker', True))}",
         "callback_data": "mc:tog:mcap_tracker"},
        {"text": f"Alerts {_mark(enabled.get('mcap_telegram', True))}",
         "callback_data": "mc:tog:mcap_telegram"},
    ]]
    rows.append([{"text": f"{label} {_mark(enabled.get(f'mcap_chain_{key}', True))}",
                  "callback_data": f"mc:chain:{key}"}
                 for key, label in CHAIN_LABELS.items()])
    rows.append([{"text": f"⏱ {c}{' •' if c == cadence else ''}",
                  "callback_data": f"mc:cad:{c}"} for c in CADENCES])
    rows.append([{"text": "➕ Add token", "callback_data": "mc:add"},
                 {"text": f"🪙 Tokens ({total})", "callback_data": "mc:tokens"},
                 {"text": "🔄 Refresh", "callback_data": "mc:home"}])
    return text, rows


# ── the token list, and one token's own screen ────────────────────────────────

async def token_list_panel() -> tuple[str, list[list[dict]]]:
    rows_db = await _col("mcap_tokens").find({}).sort("added_at", -1) \
                                       .to_list(_TOKEN_LIMIT)
    states = {}
    async for st in _col("mcap_state").find({}):
        states[(st.get("chain"), st.get("address"))] = st

    if not rows_db:
        return ("🪙 <b>No tokens watched yet</b>\n\nAdd one with\n"
                "<code>/mcap_add &lt;chain&gt; &lt;address&gt; &lt;target&gt;</code>\n"
                "<i>e.g. /mcap_add rbh 0xabc… 250k</i>",
                [[{"text": "➕ Add token", "callback_data": "mc:add"},
                  {"text": "⬅ Back", "callback_data": "mc:home"}]])

    lines = ["🪙 <b>Watched tokens</b>", "➖➖➖➖➖➖➖➖➖➖",
             "Tap one to change its target or drop it."]
    keyboard: list[list[dict]] = []
    for row in rows_db:
        st = states.get((row.get("chain"), row.get("address")), {})
        now = st.get("mcap")
        shown = fmt_usd(now) if now else "…"
        flag = " ✅" if row.get("hit_at") else ""
        keyboard.append([{
            "text": f"{(row.get('symbol') or row['address'][:6]).upper()} · "
                    f"{CHAIN_LABELS.get(row.get('chain'), '')} · {shown} → "
                    f"{fmt_usd(row.get('target'))}{flag}",
            "callback_data": f"mc:t:{row['address'][:12]}",
        }])
    keyboard.append([{"text": "➕ Add token", "callback_data": "mc:add"},
                     {"text": "⬅ Back", "callback_data": "mc:home"}])
    return "\n".join(lines), keyboard


async def token_panel(address: str) -> tuple[str, list[list[dict]]]:
    row = await _col("mcap_tokens").find_one({"address": address})
    if not row:
        return await token_list_panel()
    st = await _col("mcap_state").find_one({"chain": row.get("chain"),
                                            "address": address}) or {}
    now = st.get("mcap")
    target = float(row.get("target") or 0)
    text = (
        f"🪙 <b>{(row.get('symbol') or '?').upper()}</b> · "
        f"{CHAIN_LABELS.get(row.get('chain'), '')}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        + (f"📊 now <b>{fmt_usd(now)}</b>\n" if now
           else "📊 not read yet — the next pass will\n")
        + f"🎯 target <b>{fmt_usd(target)}</b>"
        + (" (on the way down)" if str(row.get("armed")) == "down" else "") + "\n"
        + (f"✅ hit {fmt_usd(row.get('hit_mcap'))}\n" if row.get("hit_at") else "")
        + (f"💵 ${st['price_usd']:.10g} per token\n" if st.get("price_usd") else "")
        + f"\n<code>{address}</code>"
    )
    prefix = address[:12]
    # A target is a number, and a keyboard cannot type one — so the steps are
    # buttons and anything else goes through "Set target", which asks for a
    # message the same way adding does.
    keyboard = [
        [{"text": "−25%", "callback_data": f"mc:sc:75:{prefix}"},
         {"text": "−10%", "callback_data": f"mc:sc:90:{prefix}"},
         {"text": "+10%", "callback_data": f"mc:sc:110:{prefix}"},
         {"text": "+25%", "callback_data": f"mc:sc:125:{prefix}"}],
        [{"text": "×2", "callback_data": f"mc:sc:200:{prefix}"},
         {"text": "×5", "callback_data": f"mc:sc:500:{prefix}"},
         {"text": "✏️ Set target", "callback_data": f"mc:set:{prefix}"}],
        [{"text": "🗑 Stop watching", "callback_data": f"mc:x:{prefix}"},
         {"text": "⬅ Back", "callback_data": "mc:tokens"}],
    ]
    return text, keyboard


async def add_panel() -> tuple[str, list[list[dict]]]:
    text = ("➕ <b>Watch a token</b>\n"
            "➖➖➖➖➖➖➖➖➖➖\n"
            "Pick the chain, then send the address and the target as one "
            "message:\n<code>0xabc…  250k</code>\n\n"
            "<i>Targets take k / m / b — 250k, 1.5m, 40000 all work.</i>")
    keys = list(CHAIN_LABELS)
    rows = [[{"text": CHAIN_LABELS[k], "callback_data": f"mc:addc:{k}"}
             for k in keys[i:i + 2]] for i in range(0, len(keys), 2)]
    rows.append([{"text": "⬅ Back", "callback_data": "mc:home"}])
    return text, rows


async def pending_address(chat_id, text: str) -> bool:
    """A plain message after a chain was chosen: "<address> <target>".

    Returns True when it was consumed, so the caller knows to stay quiet about
    an ordinary message otherwise.
    """
    from app.scanners.mcap_commands import reply as command_reply
    entry = _PENDING.get(chat_id)
    if not entry:
        return False
    chain, at = entry
    if time.time() - at > _PENDING_SECONDS:
        _PENDING.pop(chat_id, None)
        return False
    _PENDING.pop(chat_id, None)
    parts = text.strip().split()
    if chain.startswith("set:"):
        # Retargeting an existing token — the address is already known.
        said = await command_reply("mcap_target",
                                   f"/mcap_target {chain[4:]} {parts[0] if parts else ''}")
    else:
        said = await command_reply(
            "mcap_add",
            f"/mcap_add {chain} {' '.join(parts[:2])}")
    await notifier.send_to(chat_id, said or "Could not add that")
    if said.startswith(("✅", "🎯")):
        await open_panel(chat_id)
    return True


# ── presses ───────────────────────────────────────────────────────────────────

async def handle(data: str, cb: dict) -> tuple[str, bool]:
    message = cb.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    parts = data.split(":")
    what = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    toast, screen = "", "home"

    if what == "noop":
        return ("", False)

    if what == "tog":
        current = (await registry.enabled_map()).get(arg, True)
        await registry.set_enabled(arg, not current)
        toast = "On" if not current else "Off"
        log.info(f"[MCAP] {arg} switched {'on' if not current else 'off'} "
                 f"from Telegram")

    elif what == "chain":
        key = f"mcap_chain_{arg}"
        current = (await registry.enabled_map()).get(key, True)
        await registry.set_enabled(key, not current)
        toast = f"{arg.upper()} {'on' if not current else 'off'}"

    elif what == "cad":
        if arg not in CADENCES:
            return ("Unknown cadence", True)
        await _col("mcap_settings").update_one({"_id": "mcap"},
                                               {"$set": {"cadence": arg}}, upsert=True)
        toast = f"Checking every {arg}"

    elif what == "add":
        screen = "add"

    elif what == "addc":
        _PENDING[chat_id] = (arg, time.time())
        screen = "add"
        toast = f"Send the {CHAIN_LABELS.get(arg, arg.upper())} address and target"
        await notifier.send_to(
            chat_id,
            f"➕ Send the <b>{CHAIN_LABELS.get(arg, arg.upper())}</b> address and "
            f"the target market cap as one message:\n"
            f"<code>0xabc…  250k</code>\n"
            f"<i>Expires in {int(_PENDING_SECONDS // 60)} minutes.</i>")

    elif what == "tokens":
        screen = "tokens"

    elif what == "t":
        screen, arg = "token", await _full_address(arg)

    elif what == "sc":
        # mc:sc:<percent>:<address prefix> — the target, moved by a percentage
        # of itself. The percentage comes first because the address is what has
        # to be truncated to fit 64 bytes.
        prefix = parts[3] if len(parts) > 3 else ""
        address = await _full_address(prefix)
        row = await _col("mcap_tokens").find_one({"address": address})
        if not row:
            return ("That token is not on the list", True)
        target = float(row.get("target") or 0) * (int(arg) / 100.0)
        await _set_target(address, target)
        toast, screen, arg = f"Target {fmt_usd(target)}", "token", address

    elif what == "set":
        address = await _full_address(arg)
        _PENDING[chat_id] = (f"set:{address}", time.time())
        screen, arg = "token", address
        toast = "Send me the new target"
        await notifier.send_to(
            chat_id, "✏️ Send the new target as your next message — "
                     "<code>250k</code>, <code>1.5m</code> or <code>40000</code>.")

    elif what == "x":
        address = await _full_address(arg)
        await _col("mcap_tokens").delete_one({"address": address})
        await _col("mcap_state").delete_many({"address": address})
        toast, screen = "Stopped watching", "tokens"

    elif what == "home":
        screen = "home"

    if screen == "add":
        text, keyboard = await add_panel()
    elif screen == "tokens":
        text, keyboard = await token_list_panel()
    elif screen == "token":
        text, keyboard = await token_panel(arg)
    else:
        text, keyboard = await main_panel()
    await notifier.edit_panel(chat_id, message_id, text, keyboard)
    return (toast, False)


async def _set_target(address: str, target: float) -> None:
    """One place that writes a target, so re-arming is never forgotten."""
    st = await _col("mcap_state").find_one({"address": address}) or {}
    current = float(st.get("mcap") or 0)
    armed = "down" if current and target < current else "up"
    await _col("mcap_tokens").update_one(
        {"address": address},
        {"$set": {"target": target, "armed": armed},
         "$unset": {"hit_at": "", "hit_mcap": ""}})


async def _full_address(prefix: str) -> str:
    """The address a truncated callback refers to. Solana mints are base58 and
    case-sensitive, so the lookup is anchored but not lowercased."""
    if len(prefix) >= 32 and not prefix.startswith("0x"):
        return prefix
    if len(prefix) >= 42:
        return prefix.lower()
    row = await _col("mcap_tokens").find_one(
        {"address": {"$regex": f"^{prefix}", "$options": "i"}})
    return (row or {}).get("address", "")


async def open_panel(chat_id) -> Optional[int]:
    text, keyboard = await main_panel()
    return await notifier.send_panel(chat_id, text, keyboard)
