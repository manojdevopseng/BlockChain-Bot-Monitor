"""The Telegram event handlers — one per source feed the userbot watches.

Each handler is effectively its own bot, gated by its own registry toggle so
the Settings switches turn them on and off live:

    _call_handler    CallAnalyser2      → Signals      (GATE_CALL)
    _buybot_handler  BuyBotTracker      → Signals      (GATE_BUYBOT)
    _dexs_handler    DexsSignal         → Dexs group   (GATE_DEXS)
    _premium_handler premium groups     → four features (see its own docstring)
    _otto_handler    OttoEthDeployments → Otto group   (GATE_OTTO)

Filter, dedup and forward rules are unchanged from the reference forwarder.
"""

from __future__ import annotations

import asyncio
import re

from telethon import events

from app import fwd_counters, heartbeat
from app.util import bare_chat_id

from .common import (DEST_DEXS, DEST_OTTO, DEST_PREMIUM_ALL, DEST_PREMIUM_ETH_CALLER,
                     DEST_SIGNALS, ETH_RE, GATE_BUYBOT, GATE_CALL, GATE_DEXS, GATE_OTTO,
                     GATE_PREMIUM, GATE_PREMIUM_ETH, GATE_PREMIUM_RBH, GATE_PREMIUM_SOL,
                     HASH_RE, SOL_RE, SOURCE_BUYBOT, SOURCE_CALL, SOURCE_DEXS,
                     SOURCE_OTTO, log)
from .sending import safe_send


class HandlersMixin:
    def _register_handlers(self) -> None:
        self._client.add_event_handler(self._call_handler, events.NewMessage(chats=SOURCE_CALL))
        self._client.add_event_handler(self._buybot_handler, events.NewMessage(chats=SOURCE_BUYBOT))
        self._client.add_event_handler(self._dexs_handler, events.NewMessage(chats=SOURCE_DEXS))
        self._client.add_event_handler(self._premium_handler, events.NewMessage())
        self._client.add_event_handler(self._otto_handler, events.MessageEdited(chats=SOURCE_OTTO))

    async def _call_handler(self, event) -> None:
        fwd_counters.bump(fwd_counters.SOURCE, SOURCE_CALL)
        if not self._on(GATE_CALL):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if any(k.lower() in message for k in self._call_keywords) and ETH_RE.search(message):
            self._processed.add(unique_id)
            try:
                await safe_send(DEST_SIGNALS, lambda: event.forward_to(DEST_SIGNALS),
                                self._limiter, "CALL")
                self.count_signals += 1
                log.info("[CALL] Forwarded -> Signals")
            except Exception as exc:
                log.error(f"[CALL] Forward error: {exc}")

    async def _buybot_handler(self, event) -> None:
        fwd_counters.bump(fwd_counters.SOURCE, SOURCE_BUYBOT)
        # Its own gate now. It used to check "forwarder" — the master userbot
        # switch — which meant BuyBotTracker was the one source channel with no
        # switch of its own: you could only stop it by stopping everything.
        if not self._on(GATE_BUYBOT):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if any(k.lower() in message for k in self._buybot_keywords) and re.search(r"chain:\s*eth", message):
            self._processed.add(unique_id)
            try:
                await safe_send(DEST_SIGNALS, lambda: event.forward_to(DEST_SIGNALS),
                                self._limiter, "BUYBOT")
                self.count_signals += 1
                log.info("[BUYBOT] Forwarded -> Signals")
            except Exception as exc:
                log.error(f"[BUYBOT] Forward error: {exc}")

    async def _dexs_handler(self, event) -> None:
        fwd_counters.bump(fwd_counters.SOURCE, SOURCE_DEXS)
        if not self._on(GATE_DEXS):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if ETH_RE.search(message) and re.search(r"chain:.*(ethereum|base)", message):
            self._processed.add(unique_id)
            try:
                await safe_send(DEST_DEXS, lambda: event.forward_to(DEST_DEXS),
                                self._limiter, "DEXS")
                self.count_dexs += 1
                log.info("[DEXS] Forwarded -> Dexs Group")
            except Exception as exc:
                log.error(f"[DEXS] Forward error: {exc}")
                await safe_send(
                    DEST_DEXS,
                    lambda: self._client.send_message(DEST_DEXS, "ETH/BASE DEXS SIGNAL\n\n" + event.raw_text),
                    self._limiter, "DEXS",
                )

    async def _premium_handler(self, event) -> None:
        # Four independent switches share this handler:
        #   GATE_PREMIUM      — the premium-all forward + the caller-signal
        #                       feature (cross-group counting, forward to
        #                       DEST_PREMIUM_ETH_CALLER)
        #   GATE_PREMIUM_SOL  — SOL panel capture (getAccountInfo check)
        #   GATE_PREMIUM_ETH  — ETH panel capture (eth_getCode check)
        #   GATE_PREMIUM_RBH  — RBH panel capture (eth_getCode check)
        # None of the panel-capture ones require GATE_PREMIUM any more: a
        # premium address is an on-chain question ("is this a real contract on
        # X"), independent of whether the caller-signal forward is switched
        # on. Turning one off must not silently stop another.
        premium_on = self._on(GATE_PREMIUM)
        sol_on = self._on(GATE_PREMIUM_SOL)
        eth_on = self._on(GATE_PREMIUM_ETH)
        rbh_on = self._on(GATE_PREMIUM_RBH)
        if not any((premium_on, sol_on, eth_on, rbh_on)):
            return
        bare = bare_chat_id(event.chat_id)
        if bare not in self._premium_ids:
            return
        fwd_counters.bump(fwd_counters.SOURCE, bare)
        heartbeat.beat("premium_msg")
        await self._learn_group_name(event, bare)
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return

        if premium_on and DEST_PREMIUM_ALL:
            # Highest-volume path: every premium message is mirrored here, so it
            # is the most likely to hit Telegram's per-chat flood limit.
            try:
                await safe_send(DEST_PREMIUM_ALL, lambda: event.forward_to(DEST_PREMIUM_ALL),
                                self._limiter, "PREMIUM-ALL")
            except Exception as exc:
                err = str(exc)
                if "noforwards" in err or "can't do that operation" in err or "invalid" in err.lower():
                    text = event.raw_text or ""
                    if text:
                        try:
                            chat = await event.get_chat()
                            source = getattr(chat, "title", "Unknown")
                            await safe_send(
                                DEST_PREMIUM_ALL,
                                lambda: self._client.send_message(DEST_PREMIUM_ALL, f"📢 {source}\n\n{text}"),
                                self._limiter, "PREMIUM-ALL",
                            )
                        except Exception as exc2:  # noqa: BLE001
                            # Last resort for a no-forwards group failed too, so
                            # this message is not mirrored anywhere. Identical
                            # repeats are collapsed by the log dedup filter.
                            log.warning(f"[PREMIUM-ALL] copy fallback failed for "
                                        f"chat {event.chat_id}: {exc2}")
                else:
                    log.error(f"[PREMIUM-ALL] Forward error: {exc}")

        raw = event.raw_text or ""
        message = raw.lower()

        # Resolve the source once — needed by both SOL and ETH capture.
        chat = await event.get_chat()
        source_name = getattr(chat, "title", "Unknown")
        source_uname = getattr(chat, "username", None)
        bare = bare_chat_id(event.chat_id)

        # ── SOL address capture (dashboard-only panel; independent of ETH) ──
        if sol_on:
            for sol_addr in set(SOL_RE.findall(raw)):
                sol_key = f"{bare}:{sol_addr}"
                if sol_key not in self._capture_seen:
                    self._capture_seen.add(sol_key)
                    asyncio.create_task(self._capture_premium_sol(
                        sol_addr, bare, source_name, raw,
                        username=source_uname, msg_id=event.id,
                    ))

        eth_match = ETH_RE.search(message)
        eth_address = eth_match.group(0).lower() if eth_match else None

        # ── ETH/RBH panel capture — independent of GATE_PREMIUM ──────────────
        if eth_address and (eth_on or rbh_on):
            cap_key = f"{bare}:{eth_address}"
            if cap_key not in self._capture_seen:
                self._capture_seen.add(cap_key)
                asyncio.create_task(self._capture_premium_eth(
                    eth_address, bare, source_name, raw,
                    username=source_uname, msg_id=event.id,
                    check_eth=eth_on, check_rbh=rbh_on,
                ))

        # ── Premium caller signal (forward + cross-group counting) ───────────
        # Everything below is the separate feature GATE_PREMIUM actually
        # names — turning off Premium ETH/RBH above does not touch this.
        if not premium_on or not eth_address:
            return

        group_key = (event.chat_id, eth_address)
        if group_key in self._group_eth_tracker:
            return
        if self._eth_global_counter.get(eth_address, 0) >= 2:
            return
        self._group_eth_tracker.add(group_key)
        self._eth_global_counter[eth_address] = self._eth_global_counter.get(eth_address, 0) + 1
        self._processed.add(unique_id)

        symbol = await self._fetch_token_symbol(eth_address)
        token_line = f"Token: <b><code>{symbol}</code></b>\n" if symbol else ""
        try:
            forwarded_msg = await safe_send(
                DEST_PREMIUM_ETH_CALLER,
                lambda: event.forward_to(DEST_PREMIUM_ETH_CALLER),
                self._limiter, "PREMIUM",
            )
            if forwarded_msg is None:
                return
            await safe_send(
                DEST_PREMIUM_ETH_CALLER,
                lambda: self._client.send_message(
                    DEST_PREMIUM_ETH_CALLER,
                    (f"SOURCE: {source_name}\n{token_line}"
                     f"ETH: <code>{eth_address}</code>\n"
                     f"TOTAL CALLS: {self._eth_global_counter[eth_address]}/2"),
                    reply_to=forwarded_msg.id, parse_mode="html",
                ),
                self._limiter, "PREMIUM",
            )
            self.count_premium += 1
            log.info(f"[PREMIUM] [{source_name}] {symbol or '?'} -> PremiumETH "
                     f"({self._eth_global_counter[eth_address]}/2)")
        except Exception as exc:
            log.error(f"[PREMIUM] Forward error: {exc}")

    async def _otto_handler(self, event) -> None:
        if not self._on(GATE_OTTO):
            return
        unique_id = f"{event.chat_id}_{event.id}"
        if unique_id in self._processed:
            return
        message = event.raw_text.lower()
        if "method ids hash" not in message or "functions text" not in message:
            return
        hashes = {"#" + h for h in HASH_RE.findall(message)}
        if any(h in self._method_ids for h in hashes) or any(h in self._function_texts for h in hashes):
            self._processed.add(unique_id)
            is_rugger = any(h in self._rugger_hashes for h in hashes)
            rugger_prefix = "🛑 🛑RUGGER 🛑🛑\n\n" if is_rugger else ""
            try:
                if is_rugger:
                    await safe_send(
                        DEST_OTTO,
                        lambda: self._client.send_message(DEST_OTTO, rugger_prefix + event.raw_text),
                        self._limiter, "OTTO",
                    )
                else:
                    await safe_send(DEST_OTTO, lambda: event.forward_to(DEST_OTTO),
                                    self._limiter, "OTTO")
                self.count_otto += 1
                log.info(f"[OTTO] {'🛑 RUGGER ' if is_rugger else ''}Forwarded -> Otto Group")
            except Exception as exc:
                log.error(f"[OTTO] Forward error: {exc}")
                await safe_send(
                    DEST_OTTO,
                    lambda: self._client.send_message(
                        DEST_OTTO, rugger_prefix + "MATCHED TOKEN (COPY MODE)\n\n" + event.raw_text),
                    self._limiter, "OTTO",
                )
