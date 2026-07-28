"""Read X (Twitter) without an X API key.

The narrative agent needs four things about a token's X link: is the account
verified, how big is it, what does its bio say, and what does the post say.
X's own API charges for that. Two public mirrors give it away:

  fxtwitter  — one status, or a whole profile. Returns `verification`
               ({verified, type}), followers, bio and the tweet text.
               `type` separates a paid blue tick (individual) from an
               organisation or government account, which is the difference
               between a real signal and someone who spent eight dollars.
  Nitter RSS — a profile's latest tweet, when fxtwitter will not answer.
               Several instances, tried in order; they go down often.

Handles are cached briefly. One X link gets reused across a stream of copycat
tokens, and asking a free mirror the same question thirty times in a minute is
how you get blocked from it.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import aiohttp

from .scanners.slog import get_logger

log = get_logger(__name__)

FXTWITTER = "https://api.fxtwitter.com"

# Tried in order, first success wins. These are volunteer-run and drop out
# regularly, which is why fxtwitter is the primary and this is the fallback.
NITTER_INSTANCES = (
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.cz",
    "https://nitter.privacydev.net",
    "https://nitter.unixfox.eu",
    "https://nitter.fdn.fr",
)

_UA = {"User-Agent": "Mozilla/5.0 (compatible; BlockChainBot/1.0)"}
_TIMEOUT = aiohttp.ClientTimeout(total=10)

# GMGN stores the link in several shapes: a bare handle, a handle with a status
# path, or a full URL. All three end up here.
_STATUS_RE = re.compile(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/status/(\d+)", re.I)
_BARE_STATUS_RE = re.compile(r"^@?([A-Za-z0-9_]{1,15})/status/(\d+)", re.I)
_PROFILE_RE = re.compile(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)", re.I)
_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9_]{1,15})/?$")

_CACHE_TTL = 15 * 60
_cache: dict[str, tuple[float, "XProfile"]] = {}

# Nitter is unreachable from a datacenter IP: measured from our own server, all
# six instances fail (connection refused, 403, or a redirect to nowhere) while
# fxtwitter answers 200. Without a breaker every profile-link token walked the
# whole list and burned up to forty seconds of the loop to learn that again.
# One failed sweep parks it; a single instance is retried after the cooldown so
# it comes back on its own if the network changes.
_NITTER_COOLDOWN = 30 * 60
_nitter_down_until = 0.0


@dataclass
class XProfile:
    handle: str
    name: str = ""
    verified: bool = False
    verified_type: str = ""          # individual | business | government | ""
    followers: int = 0
    bio: str = ""
    website: str = ""
    found: bool = False              # we have the account's details
    # True when the mirror gave us no answer at all — a timeout, a 5xx, a
    # rate-limit. Different from an account that genuinely is not there, and
    # the difference decides whether the launch is retried or dropped.
    lookup_failed: bool = False


@dataclass
class XPost:
    text: str = ""
    age_minutes: Optional[float] = None
    author: Optional[XProfile] = None
    found: bool = False
    source: str = ""


@dataclass
class XRef:
    """What a token's social link points at."""
    handle: str = ""
    status_id: str = ""              # set only for a link to one post
    raw: str = ""
    kind: str = "none"               # tweet | profile | none

    @property
    def is_tweet(self) -> bool:
        return self.kind == "tweet"


def parse_ref(value: str) -> XRef:
    """Classify a token's X link into a tweet or a profile.

    This is the fork the whole agent hangs off: a link to one post is judged on
    what that post says, a link to a profile is judged on who the account is.
    """
    raw = (value or "").strip()
    if not raw:
        return XRef(raw=raw)
    m = _STATUS_RE.search(raw) or _BARE_STATUS_RE.match(raw)
    if m:
        return XRef(handle=m.group(1), status_id=m.group(2), raw=raw, kind="tweet")
    m = _HANDLE_RE.match(raw)
    if m:
        return XRef(handle=m.group(1), raw=raw, kind="profile")
    m = _PROFILE_RE.search(raw)
    if m and m.group(1).lower() not in ("i", "home", "search", "intent"):
        return XRef(handle=m.group(1), raw=raw, kind="profile")
    return XRef(raw=raw)


def _profile_from_user(user: dict) -> XProfile:
    v = user.get("verification") or {}
    return XProfile(
        handle=user.get("screen_name") or "",
        name=user.get("name") or "",
        verified=bool(v.get("verified")),
        verified_type=str(v.get("type") or ""),
        followers=int(user.get("followers") or 0),
        bio=user.get("description") or "",
        website=str((user.get("website") or {}).get("url") or "")
        if isinstance(user.get("website"), dict) else str(user.get("website") or ""),
        found=True,
    )


async def fetch_profile(session: aiohttp.ClientSession, handle: str) -> XProfile:
    """Everything we know about an account. Cached for a few minutes."""
    key = handle.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    prof = XProfile(handle=handle)
    try:
        # Redirects are not followed on purpose: a handle that does not exist
        # answers 302, and following it hides that behind whatever the redirect
        # lands on. The difference matters — a missing account is settled, a
        # sulking mirror is worth asking again.
        async with session.get(f"{FXTWITTER}/{handle}", headers=_UA,
                               timeout=_TIMEOUT, allow_redirects=False) as r:
            if r.status == 200:
                body = await r.json(content_type=None)
                user = body.get("user")
                if user:
                    prof = _profile_from_user(user)
            elif r.status in (301, 302, 303, 307, 308, 404):
                pass                      # no such account; nothing to retry
            else:
                # 401/403/429/5xx and anything else: the mirror is unhappy, not
                # the account missing.
                prof.lookup_failed = True
                log.debug(f"[X] @{handle}: mirror returned {r.status}")
    except Exception as exc:  # noqa: BLE001
        prof.lookup_failed = True
        log.debug(f"[X] profile lookup failed for @{handle}: {exc}")

    if prof.lookup_failed:
        # Never cached. Caching a failure meant one bad minute silenced that
        # handle for the next fifteen, and every launch pointing at it was
        # dropped as unverified.
        return prof

    _cache[key] = (time.time(), prof)
    if len(_cache) > 2000:
        for k, _v in sorted(_cache.items(), key=lambda kv: kv[1][0])[:500]:
            _cache.pop(k, None)
    return prof


async def fetch_post(session: aiohttp.ClientSession, ref: XRef) -> XPost:
    """The post's text and age, plus its author.

    A tweet link is read directly. A profile link falls back to the account's
    latest tweet, which is the closest thing to "what is this account saying
    right now" that a free mirror will give us.
    """
    if ref.is_tweet:
        url = f"{FXTWITTER}/{ref.handle}/status/{ref.status_id}"
        try:
            async with session.get(url, headers=_UA, timeout=_TIMEOUT) as r:
                if r.status == 200:
                    tweet = (await r.json(content_type=None)).get("tweet") or {}
                    text = (tweet.get("text") or "").strip()
                    if text:
                        author = tweet.get("author") or {}
                        return XPost(
                            text=text,
                            age_minutes=_age_minutes(
                                tweet.get("created_timestamp")
                                or tweet.get("created_at") or ""),
                            author=_profile_from_user(author) if author else None,
                            found=True, source="fxtwitter",
                        )
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[X] tweet fetch failed for {ref.handle}/{ref.status_id}: {exc}")
        return XPost()

    text, age = await _nitter_latest(session, ref.handle)
    if text:
        return XPost(text=text, age_minutes=age, found=True, source="nitter")
    return XPost()


async def _nitter_latest(session: aiohttp.ClientSession,
                         handle: str) -> tuple[str, Optional[float]]:
    global _nitter_down_until
    now = time.time()
    # Parked: try one instance to see whether it is back, not the whole list.
    instances = (NITTER_INSTANCES if now >= _nitter_down_until
                 else NITTER_INSTANCES[:1])
    for instance in instances:
        try:
            async with session.get(f"{instance}/{handle}/rss", headers=_UA,
                                   timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status != 200:
                    continue
                items = ET.fromstring((await r.text()).strip()).findall(".//item")
                if not items:
                    continue
                desc = items[0].findtext("description") or ""
                title = items[0].findtext("title") or ""
                raw = desc if len(desc) > len(title) else title
                clean = re.sub(r"<[^>]+>", " ", raw)
                clean = re.sub(r"&(amp|lt|gt|#\d+);", " ", clean)
                clean = re.sub(r"\s+", " ", clean).strip()
                if len(clean) > 10:
                    _nitter_down_until = 0.0
                    return clean, _age_minutes(items[0].findtext("pubDate") or "")
        except Exception:  # noqa: BLE001
            continue
    if len(instances) > 1:
        _nitter_down_until = now + _NITTER_COOLDOWN
        log.info("[X] every Nitter instance failed — parked for "
                 f"{_NITTER_COOLDOWN // 60} min; profile links fall back to the bio")
    return "", None


def _age_minutes(created) -> Optional[float]:
    """Minutes since a timestamp, from a Unix number, ISO 8601, or RFC 2822."""
    now = datetime.now(timezone.utc)
    try:
        ts = float(created)
        if ts > 0:
            return (now - datetime.fromtimestamp(ts, tz=timezone.utc)).total_seconds() / 60
    except (TypeError, ValueError):
        pass
    if not isinstance(created, str) or not created:
        return None
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return (now - dt).total_seconds() / 60
    except ValueError:
        pass
    try:
        return (now - parsedate_to_datetime(created)).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return None


# A Solana mint, for the profile branch: a launch account usually posts the
# address in its bio or its latest post once the token is live. Base58 excludes
# 0, O, I and l, which is most of what stops ordinary words matching — and a
# pump.fun mint ends in "pump", so that wins when a line holds more than one
# candidate. Case is preserved: a Solana address is case-sensitive.
_CA_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


def find_contract(*texts: str) -> Optional[str]:
    fallback: Optional[str] = None
    for t in texts:
        for cand in _CA_RE.findall(t or ""):
            if cand.lower().endswith("pump"):
                return cand
            if fallback is None:
                fallback = cand
    return fallback
