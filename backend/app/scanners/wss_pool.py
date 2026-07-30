"""Endpoint rotation for the chain WebSockets, shared by every socket we open.

Three endpoints per chain, tried in order and wrapping back to the first, so a
provider whose quota runs out costs one reconnect instead of the chain's whole
detection. Rotation already existed in WSProvider; what did not exist was
knowing *why* a connect failed, and noticing when there is nowhere left to go.

Two behaviours come out of that distinction:

  • A quota/rate-limit rejection rotates on the FIRST failure. Retrying an
    endpoint that just answered 429 is guaranteed to fail again — the old rule
    of "two strikes then rotate" spent a doubling backoff on a socket that had
    already told us to go away. A network error still gets its second chance,
    because there the endpoint is probably fine and the network is not.

  • When every endpoint has been rejected with no success in between, the pool
    is exhausted and that is worth waking someone for: detection for the chain
    is down and no amount of reconnecting fixes it. Alerted once per cooldown,
    with the per-endpoint status, and again when it recovers.

Both WSProvider (ETH/Robinhood) and the SOL discovery loop use this, so the two
cannot drift apart — they had different rotation rules before this, and only one
of them could ever raise an alert.

The URL list is read through a callable rather than copied in, so an endpoint
added in Settings is picked up on the next reconnect without a restart.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from .slog import get_logger

log = get_logger(__name__)

# How long a chain must be fully exhausted before the alert repeats. Long
# enough that a rotation storm sends one message, short enough that a quota
# still dead an hour later says so again.
ALERT_COOLDOWN = 1800.0

# Statuses that mean "this endpoint is spent", not "this endpoint is broken".
# 429 rate limit, 402 payment required, 413/509 usage caps some providers use.
_LIMIT_STATUS = {402, 429, 413, 509}
# Wrong or revoked key. Deliberately NOT treated as a limit: telling the user
# their quota is exhausted when the key is invalid sends them to the wrong page.
_AUTH_STATUS = {401, 403}

_LIMIT_WORDS = (
    "too many requests", "rate limit", "ratelimit", "quota", "exceeded",
    "credits", "compute unit", "usage limit", "payment required",
)
_AUTH_WORDS = ("unauthorized", "forbidden", "invalid api key", "invalid key",
               "api key", "authentication")


def _status_of(exc: BaseException) -> Optional[int]:
    """HTTP status from a rejected WebSocket handshake, if it was one.

    websockets 15 raises InvalidStatus carrying a response object; older
    releases raised InvalidStatusCode with the code on the exception. Both are
    read by attribute so neither version has to be imported here.
    """
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def classify(exc: BaseException) -> tuple[str, str]:
    """(kind, detail) for a failed connect. kind: limit | auth | network.

    Reads the HTTP status first because it is unambiguous, and falls back to the
    text for providers that answer 200 and then close with a reason string.
    """
    status = _status_of(exc)
    if status in _LIMIT_STATUS:
        return "limit", f"HTTP {status}"
    if status in _AUTH_STATUS:
        return "auth", f"HTTP {status}"

    text = str(exc).lower()
    if any(w in text for w in _LIMIT_WORDS):
        return "limit", str(exc)[:120]
    if any(w in text for w in _AUTH_WORDS):
        return "auth", str(exc)[:120]
    if status is not None:
        return "network", f"HTTP {status}"
    return "network", f"{type(exc).__name__}: {exc}"[:120]


def classify_status(status: int) -> tuple[str, str]:
    """(kind, detail) from a plain HTTP status code — for a JSON-RPC-over-HTTP
    caller that gets a response rather than a raised exception (the SOL premium
    check's getAccountInfo call, unlike the WebSocket connects above)."""
    if status in _LIMIT_STATUS:
        return "limit", f"HTTP {status}"
    if status in _AUTH_STATUS:
        return "auth", f"HTTP {status}"
    return "network", f"HTTP {status}"


def host_of(url: str) -> str:
    """Just the host, for logs and alerts — the rest of the URL is the API key."""
    return url.split("//")[-1].split("/")[0].split("?")[0] or "?"


class EndpointPool:
    """Ordered endpoints for one chain, with rotation and exhaustion tracking."""

    def __init__(self, name: str, source: Callable[[], list[str]],
                 chain_label: str = "") -> None:
        self.name = name
        self.label = chain_label or name
        self._source = source
        self._idx = 0
        # url -> (kind, detail, when) for the last failure seen on it. Cleared
        # per url on success, so a recovered endpoint stops counting as spent.
        self._bad: dict[str, tuple[str, str, float]] = {}
        self._alerted_at = 0.0
        self._exhausted = False

    # ── the list ────────────────────────────────────────────────────────────
    def urls(self) -> list[str]:
        """Configured endpoints, blanks dropped, duplicates dropped.

        Duplicates matter: the same URL pasted into two slots looks like
        failover but shares one quota, and would make an exhausted pool look
        like it still had somewhere to rotate to.
        """
        seen: list[str] = []
        for url in self._source() or []:
            url = (url or "").strip()
            if url and url not in seen:
                seen.append(url)
        return seen

    def url_at(self, attempt: int) -> str:
        """Endpoint for the Nth attempt, for callers that run several sockets.

        SOL discovery opens one socket per launchpad against the same provider.
        They share this pool so one exhausted quota is one alert rather than
        four, but each keeps its own attempt counter — a shared index would let
        four tasks failing together skip four endpoints for one outage.
        """
        urls = self.urls()
        return urls[attempt % len(urls)] if urls else ""

    def current(self) -> str:
        urls = self.urls()
        if not urls:
            return ""
        # The list can shrink under us when an endpoint is cleared in Settings.
        self._idx %= len(urls)
        return urls[self._idx]

    def position(self) -> str:
        return f"{self._idx + 1}/{max(1, len(self.urls()))}"

    # ── outcomes ────────────────────────────────────────────────────────────
    def note_success(self, url: str) -> bool:
        """Record a working connect. Returns True if this ended an outage."""
        self._bad.pop(url, None)
        recovered = self._exhausted
        self._exhausted = False
        if recovered:
            self._alerted_at = 0.0
        return recovered

    def note_failure(self, url: str, exc_or_status: BaseException | int) -> tuple[str, str]:
        """Record a failed attempt and return its (kind, detail).

        Takes either the exception a WebSocket connect raised or a plain HTTP
        status code an ordinary request came back with — the SOL premium check
        goes through aiohttp and gets a status on the response, not a raised
        InvalidStatus, so there is nothing to classify() from an exception.
        """
        if isinstance(exc_or_status, int):
            kind, detail = classify_status(exc_or_status)
        else:
            kind, detail = classify(exc_or_status)
        self._bad[url] = (kind, detail, time.time())
        return kind, detail

    def rotate(self, reason: str = "") -> bool:
        """Advance to the next endpoint, wrapping. False if there is only one."""
        urls = self.urls()
        if len(urls) < 2:
            return False
        self._idx = (self._idx + 1) % len(urls)
        because = f" ({reason})" if reason else ""
        log.warning(f"[{self.name}] switching to RPC endpoint "
                    f"{self._idx + 1}/{len(urls)} — {host_of(urls[self._idx])}{because}")
        return True

    # ── exhaustion ──────────────────────────────────────────────────────────
    def blocked_count(self) -> int:
        """Endpoints currently rejected for limit or auth reasons."""
        return sum(1 for u in self.urls()
                   if self._bad.get(u, ("", "", 0))[0] in ("limit", "auth"))

    def is_exhausted(self) -> bool:
        """Every configured endpoint has refused us, none has worked since."""
        urls = self.urls()
        return bool(urls) and self.blocked_count() >= len(urls)

    def report(self) -> str:
        """Per-endpoint status lines for the alert."""
        now = time.time()
        out = []
        for i, url in enumerate(self.urls(), 1):
            kind, detail, when = self._bad.get(url, ("", "", 0.0))
            if not kind:
                out.append(f"  {i}. {host_of(url)} — no failure recorded")
                continue
            age = now - when
            ago = "just now" if age < 45 else \
                  f"{int(age // 60)}m ago" if age < 3600 else f"{int(age // 3600)}h ago"
            out.append(f"  {i}. {host_of(url)} — {detail} ({ago})")
        return "\n".join(out)

    def should_alert(self) -> bool:
        """True at most once per cooldown, while every endpoint is refusing.

        Called after each failure. The cooldown is what stops a chain that
        reconnects every few seconds from sending a message every few seconds.
        """
        if not self.is_exhausted():
            return False
        self._exhausted = True
        now = time.time()
        if now - self._alerted_at < ALERT_COOLDOWN:
            return False
        self._alerted_at = now
        return True

    def alert_text(self) -> str:
        urls = self.urls()
        n = len(urls)
        auth = sum(1 for u in urls if self._bad.get(u, ("",))[0] == "auth")
        if auth == n:
            headline = (f"All {n} WebSocket endpoints rejected our API key "
                        f"(not a quota problem — check the keys).")
            fix = "Fix: paste a valid endpoint in Settings → RPC Endpoints."
        elif auth:
            headline = (f"All {n} WebSocket endpoints are refusing: "
                        f"{n - auth} out of quota, {auth} rejecting the key.")
            fix = ("Fix: raise the quota on the limited ones and re-check the "
                   "key on the others, in Settings → RPC Endpoints.")
        else:
            headline = f"All {n} WebSocket endpoints are rate-limited / out of quota."
            fix = ("Fix: raise the quota, or add an endpoint from a different "
                   "provider in Settings → RPC Endpoints. A second URL on the "
                   "same account shares the same quota.")
        return (f"{headline}\n\n{self.report()}\n\n"
                f"Detection for {self.label} is DOWN until one of them answers. "
                f"Rotation keeps running, so it recovers on its own if a quota "
                f"resets.\n{fix}")

    def recovery_text(self, url: str) -> str:
        # Position is derived from the url, not from self._idx: with several
        # sockets on one pool the caller that recovered is not necessarily the
        # one the index points at, and naming the wrong endpoint in a recovery
        # message sends you to the wrong provider's dashboard.
        urls = self.urls()
        where = f"{urls.index(url) + 1}/{len(urls)}" if url in urls else "?"
        return (f"{self.label} is back — connected via {host_of(url)} "
                f"(endpoint {where}).")
