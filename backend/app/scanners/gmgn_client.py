"""GMGN API client — ported from the reference repo (api/client.py).

Two surfaces, two sessions (openapi.gmgn.ai via curl_cffi Chrome fingerprint;
gmgn.ai web via plain aiohttp). Only the imports changed. The dual-URL auth,
timestamp-after-rate-limiter behaviour, and Cloudflare handling are unchanged.
"""

import uuid
import time
from typing import Any, Optional

import aiohttp
from curl_cffi.requests import AsyncSession

from app.scanners import scfg as config
from app.scanners.rate_limiter import RateLimiter, with_retry
from app.scanners.slog import get_logger

log = get_logger(__name__)

_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://gmgn.ai/",
    "Origin":          "https://gmgn.ai",
}

_WEB_TIMEOUT = aiohttp.ClientTimeout(total=10)


class APIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def _auth_params() -> dict:
    return {
        "timestamp": int(time.time()),
        "client_id": str(uuid.uuid4()),
    }


class GMGNClient:
    def __init__(self, api_key: str = None, rate_limit: float = None) -> None:
        self._api_key         = api_key or config.GMGN_API_KEY
        self._session:     Optional[AsyncSession]          = None
        self._web_session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter(rate=rate_limit or config.API_RATE_LIMIT)

    async def start(self) -> None:
        imp = config.GMGN_IMPERSONATE
        self._session = AsyncSession(
            impersonate=imp,
            headers={
                "X-APIKEY":      self._api_key,
                "Content-Type":  "application/json",
                "Accept":        "application/json",
                "Referer":       "https://gmgn.ai/",
            },
            timeout=15,
        )
        if config.CF_CLEARANCE:
            self._session.cookies.update({"cf_clearance": config.CF_CLEARANCE})
            log.info("GMGN API client: cf_clearance cookie injected")

        # The web host (gmgn.ai) fingerprints the TLS handshake, not just the
        # headers. Plain aiohttp — and curl_cffi's older Chrome profiles — get a
        # flat 403 from it; a current Chrome profile passes. So this session
        # uses curl_cffi too, with the profile taken from GMGN_IMPERSONATE so a
        # future Cloudflare change is a .env edit, not a code change.
        self._web_session = AsyncSession(
            impersonate=imp,
            headers=_WEB_HEADERS,
            timeout=15,
        )
        if config.CF_CLEARANCE:
            self._web_session.cookies.update({"cf_clearance": config.CF_CLEARANCE})
        log.info(f"GMGN API client started (openapi + web, impersonating {imp})")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            log.info("GMGN API client stopped")
        if self._web_session:
            await self._web_session.close()

    # ── openapi.gmgn.ai (curl_cffi) ──────────────────────────────
    async def _get(self, base: str, path: str, params: dict) -> Any:
        return await with_retry(self._get_once, base, path, params)

    async def _get_once(self, base: str, path: str, params: dict) -> Any:
        await self._rate_limiter.acquire()
        assert self._session is not None
        final_params = {**params}
        if base == config.GMGN_BASE_URL:
            final_params.update(_auth_params())
        resp = await self._session.get(base + path, params=final_params)
        return self._parse_cffi(resp)

    def _parse_cffi(self, resp) -> Any:
        if resp.status_code in (429, 403):
            raise APIError(resp.status_code, "Rate limited / Cloudflare block")
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text[:200])
        return resp.json()

    # ── gmgn.ai web (curl_cffi — see start() for why) ────────────
    async def _web_get(self, path: str, params: dict = None) -> Any:
        return await with_retry(self._web_get_once, path, params or {})

    async def _web_get_once(self, path: str, params: dict) -> Any:
        await self._rate_limiter.acquire()
        assert self._web_session is not None
        resp = await self._web_session.get(config.GMGN_WEB_URL + path, params=params)
        return self._parse_cffi(resp)

    async def _parse_aiohttp(self, resp: aiohttp.ClientResponse) -> Any:
        if resp.status in (429, 403):
            raise APIError(resp.status, "Rate limited / Cloudflare block")
        if resp.status >= 400:
            text = await resp.text()
            raise APIError(resp.status, text[:200])
        return await resp.json(content_type=None)

    # ── endpoints used by the scanners ───────────────────────────
    async def get_chain_new_pairs(self, chain: str, limit: int = 100) -> list[dict]:
        params = {
            "limit":     limit,
            "orderby":   "open_timestamp",
            "direction": "desc",
            "period":    "24h",
        }
        result = await self._web_get(
            f"/defi/quotation/v1/pairs/{chain}/new_pairs/24h", params,
        )
        return _pick_list(result, ["data", "pairs"])

    async def get_sol_new_pairs(self, limit: int = 500, offset: int = 0) -> list[dict]:
        params = {
            "limit":     limit,
            "offset":    offset,
            "orderby":   "open_timestamp",
            "direction": "desc",
            "period":    "24h",
        }
        result = await self._web_get(
            "/defi/quotation/v1/pairs/sol/new_pairs/24h", params,
        )
        return _pick_list(result, ["data", "pairs"])

    async def get_eth_trending_pairs(self, limit: int = 50) -> list[dict]:
        return await self.get_chain_new_pairs("eth", limit)

    async def get_web_token_info(self, address: str, chain: str = None) -> dict:
        c = chain or config.CHAIN
        try:
            result = await self._web_get(f"/defi/quotation/v1/tokens/{c}/{address}")
        except Exception as exc:
            log.debug(f"[WEB_TOKEN_INFO] {address[:8]} skipped: {exc}")
            return {}
        if not isinstance(result, dict):
            return {}
        data = result.get("data") or {}
        return data.get("token") or data or {}


def _pick(data: Any, keys: list[str]) -> Any:
    try:
        for k in keys:
            data = data[k]
        return data
    except (KeyError, TypeError):
        return None


def _pick_list(data: Any, keys: list[str]) -> list:
    val = _pick(data, keys)
    return val if isinstance(val, list) else []
