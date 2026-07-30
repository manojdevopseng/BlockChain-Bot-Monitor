"""Ingestion: PumpPortal's launch socket into the X Links rows.

Everything the judging side later reads starts here — which is also why the
drop counters live here, so a launch that never arrives can still be explained.
"""

from __future__ import annotations

import asyncio
import json
import time

import aiohttp

from .. import pump_mcap, x_client
from ..config import settings
from ..scanners.bounded_set import BoundedSet
from ..util import ist_date_str
from .common import (DROP_MINTS_KEPT, MAX_PER_NAME_PER_DAY, OG_BURST_COUNT,
                     OG_BURST_WINDOW, X_RETRIES, X_RETRY_DELAY, _col,
                     _og_promoted, _recent_launches, _spawn, _utc_now, log)
from .tgfilter import _burst_formed, _on_mcap_cross


async def x_feed_watch() -> None:
    """Hold PumpPortal's realtime socket and record every launch that has an X link.

    This replaced a GMGN polling loop. GMGN publishes pump.fun pairs in bursts,
    so a token was 20-60 seconds old before its link could even be read, and no
    polling interval could fix that — the data was not there yet. PumpPortal
    pushes the launch itself, and the token's own metadata URI carries the
    twitter field, so the link arrives with the token: measured, the metadata
    fetch takes 0.3 to 1.4 seconds and 11 of 12 launches had one.

    No API key. The key PumpPortal issues is for its trading endpoints, and
    nothing in this project trades.
    """
    seen: BoundedSet = BoundedSet(20000)
    backoff = 1.0
    # A burst of launches must not queue up behind each other's metadata fetch,
    # and must not open fifty sockets at once either.
    gate = asyncio.Semaphore(4)

    log.info(f"[PUMP] connecting to {settings.pumpportal_ws}")
    async with aiohttp.ClientSession() as session:
        # A crossing has to be recognised as it happens, so the dollar price
        # must already be in hand when the trade arrives — not fetched after.
        pump_mcap.on_cross = _on_mcap_cross
        _spawn(_price_watch(session))
        while True:
            try:
                import websockets
                async with websockets.connect(settings.pumpportal_ws,
                                              max_size=2 ** 22,
                                              ping_interval=30,
                                              ping_timeout=60) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    backoff = 1.0
                    log.info("[PUMP] subscribed to new launches")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:  # noqa: BLE001
                            continue
                        mint = (msg.get("mint") or "").strip()
                        if not mint or mint in seen:
                            continue
                        # pump.fun only. The feed labels the pool, so this is
                        # read rather than inferred from the mint's suffix.
                        if (msg.get("pool") or "").lower() != "pump":
                            continue
                        seen.add(mint)
                        # Counting moved into the handler, which is where a link
                        # is known. Arrival is stamped here because metadata
                        # fetches finish out of order, and the burst has to be
                        # ordered by when the launch happened.
                        msg["_seen_at"] = time.time()
                        # Counted before anything can filter or lose it, so the
                        # arithmetic closes: what the socket delivered should
                        # equal what was stored plus what was dropped. A gap on
                        # this side means PumpPortal did not send it; a gap on
                        # the other means we lost it after receiving it. Those
                        # want opposite fixes and looked identical until now.
                        await _count_drop("_received", msg)
                        # The market cap clock starts here, not after the
                        # metadata fetch: that fetch costs up to 1.4s, and a
                        # minute measured from 1.4s in is not the first minute.
                        # Watching every launch is cheap — the window is 60s, so
                        # a few dozen are held at a time — and it is the only way
                        # to know what a launch did before we knew we cared.
                        pump_mcap.watch(mint, msg.get("marketCapSol") or 0)
                        _spawn(_handle_launch(session, gate, msg))
            except asyncio.CancelledError:
                log.info("[PUMP] stopped")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[PUMP] socket error: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def _price_watch(session: aiohttp.ClientSession) -> None:
    """Keep SOL's dollar price warm so a crossing is never waiting on an HTTP call."""
    while True:
        try:
            price = await pump_mcap.sol_usd(session)
            if not price:
                log.warning("[MCAP] no SOL price from any source")
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[MCAP] price refresh failed: {exc}")
        await asyncio.sleep(pump_mcap.PRICE_TTL)


async def _handle_launch(session: aiohttp.ClientSession,
                         gate: asyncio.Semaphore, msg: dict) -> None:
    """One launch: read its metadata, resolve the X account, publish the row."""
    from ..ws_hub import hub

    async with gate:
        try:
            meta = await _fetch_metadata(session, msg.get("uri") or "")
            link = str(meta.get("twitter") or "")
            if not link:
                await _count_drop("no metadata" if not meta else "no twitter link", msg)
                return

            mint = msg["mint"].strip()
            ref = x_client.parse_ref(link)
            if ref.kind == "none":
                await _count_drop("link is not an account", msg)
                return

            # It has a link, so it counts towards a burst. Before the verified
            # gate on purpose: the copies in a burst are often unverified, and
            # the rule is about the name being worked, not the account.
            await _note_for_og(msg, ref)

            day = ist_date_str(time.time())
            name_key = (f"{(msg.get('name') or '').lower().strip()}|"
                        f"{(msg.get('symbol') or '').lower().strip()}")
            already = await _col("x_links").count_documents(
                {"name_key": name_key, "day": day})
            if already >= MAX_PER_NAME_PER_DAY:
                await _count_drop("name already at the daily cap", msg)
                return

            prof = await x_client.fetch_profile(session, ref.handle)
            for attempt in range(X_RETRIES):
                if not prof.lookup_failed:
                    break
                # No answer from X. Wait and ask again rather than treat silence
                # as "unverified" — that is how a real token goes missing.
                await asyncio.sleep(X_RETRY_DELAY)
                prof = await x_client.fetch_profile(session, ref.handle)
            if prof.lookup_failed:
                await _count_drop("x lookup failed", msg, ref.handle)
                log.info(f"[PUMP] {msg.get('symbol')} dropped — X gave no answer "
                         f"for @{ref.handle} after {X_RETRIES} tries")
                return

            # Verified or nothing. Any kind counts — individual, business,
            # government — but an unverified account is not worth a row: on this
            # chain anyone can point a launch at any handle, and the tick is the
            # only cheap evidence that somebody stands behind it. Not stored
            # either, which keeps the collection to the rows actually shown.
            if not prof.verified:
                await _count_drop("not verified", msg, ref.handle)
                return
            post = await x_client.fetch_post(session, ref)

            row = {
                "address": mint,
                "symbol": msg.get("symbol") or "?",
                "name": msg.get("name") or "",
                "link": link,
                "kind": ref.kind,
                "handle": ref.handle,
                "resolved": prof.found,
                "verified": prof.verified,
                "verified_type": prof.verified_type,
                "followers": prof.followers,
                "post_found": post.found,
                "post_source": post.source,
                "post_age_minutes": (round(post.age_minutes)
                                     if post.age_minutes is not None else None),
                "excerpt": (post.text or prof.bio or "")[:160],
                "description": str(meta.get("description") or "")[:400],
                "website": str(meta.get("website") or "")[:200],
                "market_cap_sol": float(msg.get("marketCapSol") or 0),
                "creator": msg.get("traderPublicKey") or "",
                # Stamped when PumpPortal pushed the launch, not when this row
                # was finally written — the metadata fetch and the X lookups in
                # between take 0.3-1.4s, and stamping it here made every age and
                # every timestamp on the page that much later than the truth.
                "open_timestamp": float(msg.get("_seen_at") or time.time()),
                "found_at": time.time(),
                "source": "pumpportal",
                "judged": False,
                # Kept on the row so the per-day cap and the History dropdown
                # both read the same field rather than recomputing a boundary.
                "day": day,
                "name_key": name_key,
            }
            await _col("x_links").update_one({"address": mint},
                                             {"$set": {**row, "dt": _utc_now()}},
                                             upsert=True)
            await hub.broadcast("x_link", row)
            # This launch may be the fifth on its link. If so the four before it
            # are re-checked here, because one of them may have crossed the bar
            # minutes ago when there was no burst yet to qualify it.
            await _burst_formed(link, day)
        except Exception as exc:  # noqa: BLE001
            # A warning, and a counted drop. At debug this was written nowhere —
            # both log handlers sit at INFO — and counted nothing, so a launch
            # lost here was indistinguishable from one that never arrived. That
            # is the difference between "the feed missed it" and "we broke on
            # it", and the two want opposite fixes.
            log.warning(f"[PUMP] handler failed on {msg.get('symbol')} "
                        f"{(msg.get('mint') or '')[:12]}: "
                        f"{type(exc).__name__}: {exc}")
            await _count_drop("handler error", msg)


async def _note_for_og(msg: dict, ref) -> None:
    """Count a linked launch towards the OG rule, promoting the original on the fifth.

    Only launches with an X link count. One without a link says nothing about
    who is behind it, and counting those let a name relaunched anonymously look
    like somebody working at it.
    """
    now = float(msg.get("_seen_at") or time.time())
    name_key = (f"{(msg.get('name') or '').lower().strip()}|"
                f"{(msg.get('symbol') or '').lower().strip()}")
    if name_key == "|":
        return

    launches = [l for l in _recent_launches.get(name_key, [])
                if now - l["ts"] <= OG_BURST_WINDOW]
    launches.append({"ts": now, "mint": (msg.get("mint") or "").strip(),
                     "symbol": msg.get("symbol") or "?",
                     "name": msg.get("name") or "",
                     "link": ref.raw, "handle": ref.handle, "kind": ref.kind})
    launches.sort(key=lambda l: l["ts"])
    _recent_launches[name_key] = launches

    # Names that went quiet are dropped, so this holds a minute of traffic
    # rather than a day of it.
    if len(_recent_launches) > 4000:
        for key, seen in list(_recent_launches.items()):
            if not seen or now - seen[-1]["ts"] > OG_BURST_WINDOW:
                _recent_launches.pop(key, None)
        for key, when in list(_og_promoted.items()):
            if now - when > 3600:
                _og_promoted.pop(key, None)

    if len(launches) < OG_BURST_COUNT:
        return
    if now - _og_promoted.get(name_key, 0) < OG_BURST_WINDOW:
        return                            # this burst already has its original
    _og_promoted[name_key] = now
    await _promote_og(name_key, launches[0])


async def _promote_og(name_key: str, first: dict) -> None:
    """Flag the first launch of a burst, storing it if nothing stored it before."""
    from ..ws_hub import hub
    try:
        mint = (first.get("mint") or "").strip()
        if not mint:
            return
        existing = await _col("x_links").find_one({"address": mint})
        if existing and existing.get("og"):
            return
        if existing:
            await _col("x_links").update_one(
                {"address": mint}, {"$set": {"og": True, "og_at": time.time()}})
            row = {**{k: v for k, v in existing.items() if k not in ("_id", "dt")},
                   "og": True}
        else:
            # Nothing stored it, so the original's account was unverified. It
            # still belongs here — the burst is what makes it worth seeing — and
            # its link is kept even though the account did not pass the gate.
            row = {
                "address": mint, "symbol": first.get("symbol") or "?",
                "name": first.get("name") or "",
                "link": first.get("link") or "", "kind": first.get("kind") or "none",
                "handle": first.get("handle") or "", "resolved": False,
                "verified": False,
                "verified_type": "", "followers": 0, "post_found": False,
                "post_source": "", "post_age_minutes": None, "excerpt": "",
                "open_timestamp": first.get("ts") or time.time(),
                "found_at": first.get("ts") or time.time(),
                "source": "pumpportal", "judged": True,
                "day": ist_date_str(first.get("ts") or time.time()),
                "name_key": name_key, "og": True, "og_at": time.time(),
            }
            await _col("x_links").update_one({"address": mint},
                                             {"$set": {**row, "dt": _utc_now()}},
                                             upsert=True)
        log.info(f"[PUMP] OG: {row.get('symbol')} — {OG_BURST_COUNT} linked "
                 f"launches under this name inside {OG_BURST_WINDOW}s")
        await hub.broadcast("x_link", row)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[PUMP] could not promote OG for {name_key}: {exc}")


async def _count_drop(reason: str, msg: dict, handle: str = "") -> None:
    """Record that a launch did not become a row, and why.

    Counted by the hour, and the mint is kept alongside the count. It used to be
    kept only for "x lookup failed", which meant "why did this token never
    appear" was unanswerable for every other reason — the counts said 489
    launches had no X link but not which ones, so a token that vanished could
    not be told apart from one correctly filtered. Bounded per bucket, and the
    collection expires with the logs, so the cost is fixed.
    """
    try:
        hour = time.strftime("%d-%m-%Y %H:00", time.localtime())
        await _col("x_drops").update_one(
            {"_id": f"{hour}|{reason}"},
            {"$inc": {"count": 1},
             "$set": {"reason": reason, "hour": hour, "dt": _utc_now()},
             "$push": {"mints": {"$each": [{"mint": msg.get("mint"),
                                            "symbol": msg.get("symbol"),
                                            "handle": handle,
                                            "at": time.time()}],
                                 "$slice": -DROP_MINTS_KEPT}}},
            upsert=True)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[PUMP] could not count drop: {exc}")
async def _fetch_metadata(session: aiohttp.ClientSession, uri: str) -> dict:
    """The launch's own metadata JSON — where the socials live."""
    if not uri or not uri.startswith("http"):
        return {}
    try:
        async with session.get(uri, timeout=aiohttp.ClientTimeout(total=8),
                               headers={"User-Agent": "Mozilla/5.0 "
                                                      "(compatible; BlockChainBot/1.0)"}) as r:
            if r.status != 200:
                return {}
            return await r.json(content_type=None) or {}
    except Exception:  # noqa: BLE001
        return {}


# Link types that count as an account behind the token. A launch whose metadata
# names something else — an X community, a bare contract address — is not worth
# a row; one with no twitter field at all never becomes a row; and neither does
# one whose account is unverified.
