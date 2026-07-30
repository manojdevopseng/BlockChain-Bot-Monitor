"""TelegramCommands — bot command handler.

Ported from the reference repo's core/commands.py, with three changes:

  • answers from MongoDB instead of JSON state files
  • every command can be switched off individually from the dashboard; a
    disabled command stops replying AND disappears from Telegram's "/" menu
  • real usage is recorded (count, last used, failures), so the Commands page
    shows what actually happened rather than seeded numbers

Runs on the BOT token (TELEGRAM_BOT_TOKEN from @BotFather) — completely
separate from the Telethon userbot the forwarder uses, so the two never fight
over the same session and commands work even when the userbot is logged out.

Every command except /stop and /restart is read-only. Those two change bot
behaviour, so they check the sender against Telegram's own admin list for the
chat (`getChatAdministrators`) before doing anything — everyone else gets a
"admins only" reply, same as a wrong command. See ADMIN_ONLY_COMMANDS.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

import aiohttp

from app.scanners import scfg as config
from app.scanners.slog import get_logger
from app.util import esc
from app import heartbeat

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# The command set this handler implements. `menu` is the description Telegram
# shows in the "/" popup. Seeded into Mongo on first run; the dashboard owns
# `enabled` and the usage counters from then on.
COMMAND_SPEC = [
    ("start",    "Bot intro and what it monitors",     "General"),
    ("help",     "List all commands",                  "General"),
    ("status",   "Running state and uptime",           "System"),
    ("services", "Which bots, chains and RPCs are on", "System"),
    ("stats",    "Detection and alert counts",         "System"),
    ("watching", "Live SOL watch list",                "Tokens"),
    ("tokens",   "Recently detected tokens",           "Tokens"),
    ("alerts",   "Recent alerts",                      "Alerts"),
    ("gas",      "Recent high-gas early buys",         "Alerts"),
    ("ping",     "Quick alive check",                  "General"),
    ("stop",     "Stop the bot — turns off all bots/chains/RPCs "
                 "(group admins only)",                "System"),
    ("restart",  "Undo /stop — turns back on exactly what it stopped "
                 "(group admins only)",                "System"),
]

# Checked against Telegram's own admin list for the chat, not a hardcoded user
# id — whoever the group actually makes an admin can use these, and losing
# admin status in the group revokes it here too, automatically.
ADMIN_ONLY_COMMANDS = {"stop", "restart"}

# How long a chat's admin list is trusted before asking Telegram again. Long
# enough that this cannot become a de-facto rate limit on getChatAdministrators
# if someone spams the command; short enough that a promotion/demotion in the
# group takes effect within about two minutes rather than needing a restart.
_ADMIN_CACHE_TTL = 120.0


def _fmt_dur(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def _ago(ts) -> str:
    if not ts:
        return "—"
    d = int(time.time() - float(ts))
    if d < 60:
        return f"{d}s"
    if d < 3600:
        return f"{d // 60}m"
    if d < 86400:
        return f"{d // 3600}h"
    return f"{d // 86400}d"


def _col(name: str):
    from .. import db
    return db.get_collection(name)


class TelegramCommands:
    """Long-polling command handler. One instance, runs as a supervisor task."""

    def __init__(self) -> None:
        self._token = config.TELEGRAM_BOT_TOKEN if config.TELEGRAM_BOT_TOKEN_SET else ""
        # The only chat commands are answered in. Blank = answer anywhere.
        self._chat_id = str(config.COMMAND_CHAT_ID or "").strip()
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._boot_at = time.time()
        # chat_id -> ({admin user ids}, fetched_at). Only touched by /stop and
        # /restart, so it stays empty for a chat that never runs them.
        self._admin_cache: dict[int, tuple[set, float]] = {}

    def allowed(self, chat_id) -> bool:
        """Is this the chat we answer in?

        Chat ids are compared as strings so -1003946098130 from .env matches the
        int Telegram sends.
        """
        if not self._chat_id:
            return True
        return str(chat_id) == self._chat_id

    async def _is_group_admin(self, chat_id, user_id) -> bool:
        """True if Telegram itself lists user_id as an admin/creator of chat_id.

        Cached for _ADMIN_CACHE_TTL per chat. On a failed lookup, an existing
        cache entry is reused rather than treated as "nobody is admin" (breaks
        the command for real admins) or "everybody is admin" (the one outcome
        that must never happen for /stop). With no cache and no successful
        lookup, it fails closed — denied.
        """
        if user_id is None:
            return False
        now = time.time()
        cached = self._admin_cache.get(chat_id)
        if cached is not None and now - cached[1] < _ADMIN_CACHE_TTL:
            return user_id in cached[0]
        try:
            res = await self._api("getChatAdministrators", {"chat_id": chat_id})
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[CMD] getChatAdministrators failed: {exc}")
            return user_id in cached[0] if cached is not None else False
        if not res.get("ok"):
            log.warning(f"[CMD] getChatAdministrators rejected: {res.get('description')}")
            return user_id in cached[0] if cached is not None else False
        ids = {m["user"]["id"] for m in res.get("result", []) if m.get("user")}
        self._admin_cache[chat_id] = (ids, now)
        return user_id in ids

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN not set — get one from @BotFather and put it in .env"
            )
        self._session = aiohttp.ClientSession()
        # A leftover webhook would swallow updates and getUpdates would return
        # nothing at all, silently.
        try:
            await self._api("deleteWebhook", {"drop_pending_updates": "false"})
        except Exception as exc:  # noqa: BLE001
            # A webhook left in place swallows every update, so getUpdates would
            # return nothing at all and the bot would look simply dead.
            log.warning(f"[CMD] deleteWebhook failed: {exc}")
        await self.refresh_menu()

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("[CMD] Command handler stopped")

    async def refresh_menu(self) -> None:
        """Publish only the enabled commands to Telegram's "/" menu.

        Called at startup and again whenever a toggle changes, so switching a
        command off in the dashboard really removes it from the menu.

        When COMMAND_CHAT_ID is set the menu is published *scoped to that chat*
        and the default scope is emptied, so no other group or DM even sees a
        "/" list — matching the handler, which ignores them anyway.
        """
        try:
            enabled = await self._enabled_map()
            cmds = [
                {"command": name, "description": menu}
                for name, menu, _cat in COMMAND_SPEC
                if enabled.get(name, True)
            ]
            payload = json.dumps(cmds)

            if self._chat_id:
                scope = json.dumps({"type": "chat", "chat_id": self._chat_id})
                res = await self._api("setMyCommands",
                                      {"commands": payload, "scope": scope})
                if not res.get("ok"):
                    # Usually "chat not found" — the bot is not in that group yet.
                    log.warning(f"[CMD] menu for chat {self._chat_id} rejected: "
                                f"{res.get('description')}. Add the bot to that group.")
                # Empty everywhere else, so the "/" popup is blank in other chats.
                await self._api("setMyCommands",
                                {"commands": "[]",
                                 "scope": json.dumps({"type": "default"})})
                log.info(f"[CMD] Menu published — {len(cmds)} command(s) enabled, "
                         f"only in chat {self._chat_id}")
            else:
                await self._api("setMyCommands", {"commands": payload})
                log.info(f"[CMD] Menu published — {len(cmds)} command(s) enabled "
                         f"(no COMMAND_CHAT_ID set: answers in every chat)")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[CMD] setMyCommands failed: {exc}")

    async def run(self) -> None:
        where = f"answering only in chat {self._chat_id}" if self._chat_id \
                else "answering in every chat (COMMAND_CHAT_ID not set)"
        log.info(f"[CMD] Command handler started — long-polling getUpdates, {where}")
        while True:
            try:
                for update in await self._get_updates():
                    await self._observe(update)
                    if update.get("callback_query"):
                        await self._handle_button(update["callback_query"])
                    else:
                        await self._handle(update)
            except asyncio.CancelledError:
                return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                # Normal with long-polling: Telegram or the network drops an
                # idle connection. Nothing is lost — just poll again.
                await asyncio.sleep(1)
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[CMD] poll error: {exc}")
                await asyncio.sleep(3)

    # ── Telegram I/O ──────────────────────────────────────────────────────────

    async def _api(self, method: str, params: dict, timeout: int = 15) -> dict:
        assert self._session is not None
        async with self._session.post(
            f"{TELEGRAM_API}/bot{self._token}/{method}",
            data=params, timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            return await resp.json()

    async def _get_updates(self) -> list:
        data = await self._api(
            "getUpdates",
            # my_chat_member fires the moment the bot is added to (or removed
            # from) a group — that is what makes a brand-new private group
            # discoverable in Settings → Find Chat ID.
            {"offset": self._offset, "timeout": 25,
             "allowed_updates": json.dumps(["message", "my_chat_member",
                                            "callback_query"])},
            timeout=35,
        )
        result = data.get("result", []) if isinstance(data, dict) else []
        if result:
            self._offset = result[-1]["update_id"] + 1
        return result

    async def _send(self, chat_id, text: str) -> bool:
        try:
            r = await self._api("sendMessage", {
                "chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": "true",
            })
            return bool(r.get("ok"))
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] send failed: {exc}")
            return False

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _enabled_map(self) -> dict[str, bool]:
        try:
            docs = await _col("commands").find({}).to_list(100)
            return {d["command"].lstrip("/"): bool(d.get("enabled", True)) for d in docs}
        except Exception:
            return {}

    @staticmethod
    async def _observe(update: dict) -> None:
        """Remember every chat the bot sees, for the Settings chat-id lookup.

        Purely a note-to-self: nothing is subscribed to and nothing is
        forwarded — the chat is only written down so its numeric id can be
        copied into .env later.
        """
        member = update.get("my_chat_member") or {}
        cb_chat = ((update.get("callback_query") or {}).get("message") or {}).get("chat")
        chat = (update.get("message") or {}).get("chat") or member.get("chat") or cb_chat
        if not chat:
            return
        try:
            from .. import chatid
            await chatid.record_chat(chat, "bot added" if member else "message seen")
            if member:
                status = ((member.get("new_chat_member") or {}).get("status")) or "?"
                log.info(f"[CMD] chat noted: {chat.get('title') or chat.get('id')} "
                         f"({chat.get('id')}) — bot is now '{status}'")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] could not note chat {chat.get('id')}: {exc}")

    async def _handle_button(self, cb: dict) -> None:
        """A button press on an alert.

        Telegram keeps showing a spinner until answerCallbackQuery is sent, so
        that goes out whatever happens. Buttons are honoured from the alert
        chats too, not only the command chat — the whole point is acting on the
        alert where it arrived.
        """
        from .. import tgbuttons
        text, show_alert = "", False
        try:
            text, show_alert = await tgbuttons.handle_callback(cb)
        except Exception as exc:  # noqa: BLE001
            log.error(f"[CMD] button {cb.get('data')!r} failed: {exc}")
            text = "Could not do that"
        try:
            await self._api("answerCallbackQuery", {
                "callback_query_id": cb.get("id"),
                "text": text[:200],
                "show_alert": "true" if show_alert else "false",
            })
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] answerCallbackQuery failed: {exc}")

    async def _handle(self, update: dict) -> None:
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return

        # Commands are answered in one group only. Everywhere else the bot says
        # nothing at all — no reply, no error, and the usage counter is not
        # touched, so the dashboard shows only real use.
        if not self.allowed(chat_id):
            log.debug(f"[CMD] ignored '{text.split()[0]}' from chat {chat_id} "
                      f"(only {self._chat_id} is allowed)")
            return

        # "/status@MyBot arg" -> "status"
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()
        if cmd not in {c for c, _, _ in COMMAND_SPEC}:
            return                                    # unknown — stay silent

        enabled = await self._enabled_map()
        if not enabled.get(cmd, True):
            log.debug(f"[CMD] /{cmd} is disabled — ignored")
            return

        user_id = (msg.get("from") or {}).get("id")
        heartbeat.beat("command")
        started = time.perf_counter()
        try:
            reply = await self._reply_for(cmd, chat_id, user_id)
            ok = await self._send(chat_id, reply) if reply else False
        except Exception as exc:  # noqa: BLE001
            log.error(f"[CMD] /{cmd} failed: {exc}")
            ok = False
        await self._record(cmd, ok, time.perf_counter() - started, user_id)

    async def _record(self, cmd: str, ok: bool, seconds: float, user_id) -> None:
        """Real usage stats for the dashboard — no invented numbers."""
        try:
            await _col("commands").update_one(
                {"command": f"/{cmd}"},
                {"$inc": {"uses_total": 1, "errors_total": 0 if ok else 1},
                 "$set": {"last_used": time.time(),
                          "last_ms": round(seconds * 1000),
                          "last_user_id": user_id}},
            )
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[CMD] could not record use of /{cmd}: {exc}")

    async def _reply_for(self, cmd: str, chat_id, user_id) -> str:
        if cmd in ("start", "help"):
            return await self._msg_help()
        if cmd == "status":
            return await self._msg_status()
        if cmd == "services":
            return await self._msg_services()
        if cmd == "stats":
            return await self._msg_stats()
        if cmd == "watching":
            return await self._msg_watching()
        if cmd == "tokens":
            return await self._msg_tokens()
        if cmd == "alerts":
            return await self._msg_alerts()
        if cmd == "gas":
            return await self._msg_gas()
        if cmd == "ping":
            return "🟢 <b>pong</b> — bot is alive"
        if cmd in ADMIN_ONLY_COMMANDS and not await self._is_group_admin(chat_id, user_id):
            return "🔒 Only a group admin can use this command."
        if cmd == "stop":
            return await self._msg_stop(user_id)
        if cmd == "restart":
            return await self._msg_restart(user_id)
        return ""

    # ── Replies (all read from MongoDB) ───────────────────────────────────────

    async def _msg_help(self) -> str:
        enabled = await self._enabled_map()
        lines = [
            "🤖 <b>BlockChain-Bot Monitor</b>\n",
            "SOL→ETH and SOL→Robinhood cross-chain moves, premium Telegram "
            "calls, and high-gas early buys on Ethereum.\n",
            "<b>Commands:</b>",
        ]
        for name, menu, _cat in COMMAND_SPEC:
            if enabled.get(name, True) and name != "start":
                lines.append(f"/{name} — {menu}")
        return "\n".join(lines)

    async def _msg_status(self) -> str:
        from .. import db, supervisor
        workers = supervisor.diagnostics().get("workers", {})
        alive = [k for k, v in workers.items() if v]
        return (
            "📊 <b>Bot Status</b>\n\n"
            f"<b>State:</b> 🟢 Running\n"
            f"<b>Uptime:</b> {_fmt_dur(supervisor.uptime_seconds())}\n"
            f"<b>Workers:</b> {', '.join(alive) if alive else 'none'}\n"
            f"<b>Database:</b> {db.backend_name()}"
        )

    async def _msg_services(self) -> str:
        from .. import registry, supervisor
        svcs = await registry.list_services()
        states = supervisor.service_states({s["id"]: bool(s["enabled"]) for s in svcs})
        icon = {"running": "🟢", "stopped": "🔴", "disabled": "⚪"}
        out = {"bot": [], "chain": [], "rpc": []}
        for s in svcs:
            st = states.get(s["id"], {}).get("status", "unknown")
            out.setdefault(s["category"], []).append(
                f"{icon.get(st, '❔')} {esc(s['label'])} — {st}"
            )
        parts = ["🔀 <b>Services</b>"]
        for cat, title in (("bot", "Bots"), ("chain", "Chains"), ("rpc", "RPCs")):
            if out.get(cat):
                parts.append(f"\n<b>{title}</b>\n" + "\n".join(out[cat]))
        return "\n".join(parts)

    async def _msg_stats(self) -> str:
        day = time.time() - 86400
        tokens, alerts = _col("tokens"), _col("alerts")
        watch = await self._watchlist()
        return (
            "📈 <b>Stats</b>\n\n"
            f"<b>Watching now:</b> {len(watch)}\n"
            f"<b>Tokens found:</b> {await tokens.count_documents({})}"
            f"  (24h: {await tokens.count_documents({'created_at': {'$gte': day}})})\n"
            f"<b>Alerts:</b> {await alerts.count_documents({})}"
            f"  (24h: {await alerts.count_documents({'created_at': {'$gte': day}})})\n"
            f"<b>Cross-chain matches:</b> "
            f"{await alerts.count_documents({'type': 'Cross-Chain Match'})}\n"
            f"<b>High-gas buys:</b> {await _col('gas_alerts').count_documents({})}\n"
            f"<b>Premium detections:</b> "
            f"{await _col('premium_detections').count_documents({})}"
        )

    @staticmethod
    async def _watchlist() -> list:
        from .. import watchlist
        return await watchlist.active()

    async def _msg_watching(self) -> str:
        wl = await self._watchlist()
        if not wl:
            return "👀 <b>Watching Now</b>\n\nNothing being watched right now."
        lines = [f"👀 <b>Watching Now</b>  ({len(wl)})\n"]
        now = time.time()
        for d in wl[:25]:
            rem = max(0, int((d.get("expires_at", 0) - now) / 60))
            lines.append(
                f"• <b>{esc(d.get('symbol'))}</b>  "
                f"${float(d.get('mcap_usd') or 0):,.0f}  ·  {rem}m left"
            )
        if len(wl) > 25:
            lines.append(f"…and {len(wl) - 25} more")
        return "\n".join(lines)

    async def _msg_tokens(self) -> str:
        docs = await _col("tokens").find({}).to_list(500)
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if not docs:
            return "🪙 <b>Recent Tokens</b>\n\nNo tokens detected yet."
        lines = ["🪙 <b>Recent Tokens</b>\n"]
        for t in docs[:10]:
            lines.append(
                f"• <b>{esc(t.get('symbol'))}</b> · {esc((t.get('chain') or '').upper())}"
                f" · {_ago(t.get('created_at'))} ago"
            )
        return "\n".join(lines)

    async def _msg_alerts(self) -> str:
        docs = await _col("alerts").find({}).to_list(500)
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if not docs:
            return "🔔 <b>Recent Alerts</b>\n\nNo alerts yet."
        lines = ["🔔 <b>Recent Alerts</b>\n"]
        for a in docs[:8]:
            lines.append(
                f"• <b>{esc(a.get('token_symbol') or a.get('type'))}</b> · "
                f"{esc((a.get('chain') or '').upper())} · {_ago(a.get('created_at'))} ago"
            )
        return "\n".join(lines)

    async def _msg_gas(self) -> str:
        docs = await _col("gas_alerts").find({}).to_list(200)
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if not docs:
            return (
                "⛽ <b>High-Gas Early Buys</b>\n\nNone caught yet.\n"
                f"<i>Threshold: {config.MIN_FEE_ETH} ETH</i>"
            )
        lines = [f"⛽ <b>High-Gas Early Buys</b>  (threshold {config.MIN_FEE_ETH} ETH)\n"]
        for g in docs[:8]:
            lines.append(
                f"• <b>{esc(g.get('symbol'))}</b> · {float(g.get('fee_eth') or 0):.6f} ETH"
                f" · age {g.get('age_seconds', '?')}s · {_ago(g.get('created_at'))} ago"
            )
        return "\n".join(lines)

    # ── Bot control (admin-only, the only two commands that change state) ──────
    #
    # /stop turns off every registry toggle except bot_commands, and snapshots
    # exactly which ones it touched so /restart can undo precisely that — not
    # "everything", which would also re-enable anything the user had already
    # switched off on purpose before /stop ran. bot_commands itself is never
    # touched: turning it off would kill this very handler, and /restart could
    # then never be heard.

    async def _msg_stop(self, user_id) -> str:
        from .. import registry
        svcs = await registry.list_services()
        to_stop = [s for s in svcs if s.get("enabled") and s["id"] != "bot_commands"]
        if not to_stop:
            return "⚪ Already stopped — nothing else was on."
        await _col("bot_control").update_one(
            {"_id": "stop_snapshot"},
            {"$set": {"ids": [s["id"] for s in to_stop],
                     "stopped_at": time.time(), "stopped_by": user_id}},
            upsert=True,
        )
        for s in to_stop:
            await registry.set_enabled(s["id"], False)
        names = ", ".join(esc(s["label"]) for s in to_stop)
        return (
            "🔴 <b>Bot stopped</b>\n\n"
            f"Turned off {len(to_stop)}: {names}\n\n"
            "Dashboard and this chat stay reachable — /restart brings back "
            "exactly this, or use Settings."
        )

    async def _msg_restart(self, user_id) -> str:
        from .. import registry
        snap = await _col("bot_control").find_one({"_id": "stop_snapshot"})
        ids = (snap or {}).get("ids") or []
        if not ids:
            return "🟢 Nothing to restart — the bot was not stopped with /stop."
        by_id = {s["id"]: s for s in await registry.list_services()}
        restored = []
        for sid in ids:
            await registry.set_enabled(sid, True)
            if sid in by_id:
                restored.append(by_id[sid]["label"])
        await _col("bot_control").delete_one({"_id": "stop_snapshot"})
        names = ", ".join(esc(n) for n in restored)
        return f"🟢 <b>Bot restarted</b>\n\nTurned back on {len(restored)}: {names}"
