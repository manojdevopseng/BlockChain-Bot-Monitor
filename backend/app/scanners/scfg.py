"""Scanner config shim.

The ported scanners (sol/eth/robinhood/gmgn client) reference their settings as
`config.SOMETHING`. To keep the port byte-for-byte faithful to the reference
repo, each scanner does `from app.scanners import scfg as config` — so this
module exposes the exact same names the original `config.py` did, sourced from
our env-backed `app.config.settings` and overlaid with live registry toggles.

`refresh_from_registry()` is called by the supervisor before (re)starting
scanners so the Robinhood source toggles + master flags reflect the dashboard.
"""

from __future__ import annotations

from ..config import settings

# ── API ─────────────────────────────────────────────────────────
GMGN_API_KEY   = settings.gmgn_api_key
GMGN_BASE_URL  = "https://openapi.gmgn.ai"
GMGN_WEB_URL   = "https://gmgn.ai"

# ── Rate limiting / retries (from .env) ─────────────────────────
# API_RATE_LIMIT is the fallback pace for a client created without an explicit
# rate; GMGN_SCAN_RATE is what the shared scanning client actually runs at.
API_RATE_LIMIT     = settings.api_rate_limit
GMGN_SCAN_RATE     = settings.gmgn_scan_rate
RETRY_MAX          = settings.retry_max
RETRY_BACKOFF_BASE = settings.retry_backoff_base

# ── Telegram ────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = settings.telegram_bot_token or "placeholder"
TELEGRAM_CHAT_ID   = settings.telegram_chat_id or "placeholder"
TELEGRAM_ENABLED   = bool(settings.telegram_bot_token and settings.telegram_chat_id)
# The "placeholder" fallback above keeps the ported scanners byte-faithful to
# the reference repo, but it is truthy — so anything that must not run without
# a real token (the /command handler) checks this instead.
TELEGRAM_BOT_TOKEN_SET = bool(settings.telegram_bot_token)

# The one chat the /command handler answers in. Defaults to the alert group so
# commands, errors and start/stop notices all live in the same place.
COMMAND_CHAT_ID = (settings.command_chat_id or settings.alert_chat_id or "").strip()

# NOTE: the reference repo also carried MIN_LIQUIDITY_USD, but nothing ever
# read it (its own .env said "read by config (kept)"). The SOL scanner filters
# on SOL_MIN_MCAP + SOL_MIN_FEES instead, so it is not carried over here.

CHAIN = "eth"

# ── Solana (from .env) ──────────────────────────────────────────
SOL_RPC_HTTP      = settings.sol_rpc_http
SOL_RPC_WSS       = settings.sol_rpc_wss
# Ordered endpoints discovery rotates through when one keeps failing. A single
# provider having a bad hour left mint discovery blind with nothing to fall
# back on — the same reason ETH and RBH carry a second endpoint. Two, not
# three: a third logsSubscribe-capable provider is a much shorter list than a
# third plain JSON-RPC one.
SOL_WSS_ENDPOINTS = [u for u in (settings.sol_rpc_wss,
                                 settings.sol_rpc_wss_fallback) if u]
# The HTTP RPC is a different job entirely — the forwarder's own
# getAccountInfo check on a premium-group SOL address, nothing to do with
# discovery or the market-cap watch. Its own 2-way pair.
SOL_HTTP_ENDPOINTS = [u for u in (settings.sol_rpc_http,
                                  settings.sol_rpc_http_fallback) if u]
SOL_SCAN_INTERVAL = settings.sol_scan_interval
SOL_MIN_FEES      = settings.sol_min_fees
SOL_MIN_MCAP      = settings.sol_min_mcap
SOL_WATCH_WINDOW  = settings.sol_watch_window
SOL_CHAIN         = "sol"
SOL_PUMP_PROGRAM  = settings.sol_pump_program
SOL_BONK_PROGRAM  = settings.sol_bonk_program
SOL_BONKERS_PROGRAM = settings.sol_bonkers_program
SOL_BAGS_PROGRAM    = settings.sol_bags_program
# Registry switch `sol_onchain_discovery`, mirrored here so the scanner reads
# one flag instead of the services collection. True until the first reconcile
# overwrites it, which keeps behaviour unchanged for anything that starts
# before the registry is up.
SOL_DISCOVERY_ENABLED = True

# ── Cross-chain matching (from .env) ────────────────────────────
CROSS_CHAIN_CHAT_ID = settings.cross_chain_chat_id

# ── Ethereum on-chain (SOL→ETH) — from .env ─────────────────────
ETH_RPC_WSS        = settings.eth_rpc_wss
# Ordered endpoint list the WS provider rotates through.
ETH_WSS_ENDPOINTS  = [u for u in (settings.eth_rpc_wss,
                                  settings.eth_rpc_wss_fallback,
                                  settings.eth_rpc_wss_fallback2) if u]
ETH_RPC_HTTP       = settings.eth_rpc_http
# Unrelated to on-chain discovery: this is the premium-caller detection's
# eth_getCode + token-metadata lookup pool (forwarder.py). Its own 2-way pair.
ETH_HTTP_ENDPOINTS = [u for u in (settings.eth_rpc_http,
                                  settings.eth_rpc_http_fallback) if u]
ETH_V2_FACTORY     = settings.eth_v2_factory
ETH_V3_FACTORY     = settings.eth_v3_factory
ETH_V4_POOLMANAGER = settings.eth_v4_poolmanager
ETH_WETH           = settings.eth_weth

# ── Robinhood Chain on-chain (SOL→Robinhood) — from .env ────────
RBH_RPC_WSS        = settings.rbh_rpc_wss
RBH_WSS_ENDPOINTS  = [u for u in (settings.rbh_rpc_wss,
                                  settings.rbh_rpc_wss_fallback,
                                  settings.rbh_rpc_wss_fallback2) if u]
RBH_RPC_HTTP       = settings.rbh_rpc_http
# Same idea as ETH_HTTP_ENDPOINTS above, for Robinhood's side of the same
# premium-caller detection.
RBH_HTTP_ENDPOINTS = [u for u in (settings.rbh_rpc_http,
                                  settings.rbh_rpc_http_fallback) if u]
# ── BNB Chain (BSC) — premium-caller detection only, no discovery scanner ──
BNB_RPC_HTTP       = settings.bnb_rpc_http
BNB_HTTP_ENDPOINTS = [u for u in (settings.bnb_rpc_http,
                                  settings.bnb_rpc_http_fallback) if u]
BNB_WBNB           = settings.bnb_wbnb
# ── Base — premium-caller detection only, same shape as BNB above ──────────
GAS_MAX_TOKEN_AGE   = settings.gas_max_token_age_seconds
TRACKER_MEDIA_MAX   = settings.tracker_media_max_bytes
BASE_RPC_HTTP       = settings.base_rpc_http
BASE_HTTP_ENDPOINTS = [u for u in (settings.base_rpc_http,
                                   settings.base_rpc_http_fallback) if u]
# Settings → RPCs → BASE. Kept as its own flag rather than emptying the list,
# so switching the endpoints back on does not need a restart to remember what
# they were.
BASE_RPC_ENABLED    = True


def base_endpoints() -> list:
    """What the Base pool should try right now — nothing while it is switched
    off, so the RPC monitor's "disabled" is the truth and not a label."""
    return list(BASE_HTTP_ENDPOINTS) if BASE_RPC_ENABLED else []
BASE_WETH           = settings.base_weth

RBH_V2_FACTORY     = settings.rbh_v2_factory
RBH_V3_FACTORY     = settings.rbh_v3_factory
RBH_V4_POOLMANAGER = settings.rbh_v4_poolmanager
# V4 has no pool address to call — the price lives in the PoolManager and is
# read by pool id through StateView. The explorer API is only for pools with a
# hook, whose id cannot be computed; it is asked once per token, not per check.
ETH_V4_STATEVIEW   = settings.eth_v4_stateview
RBH_V4_STATEVIEW   = settings.rbh_v4_stateview
BNB_V4_POOLMANAGER = settings.bnb_v4_poolmanager
BNB_V4_STATEVIEW   = settings.bnb_v4_stateview
ETH_EXPLORER_API   = settings.eth_explorer_api
RBH_EXPLORER_API   = settings.rbh_explorer_api
BNB_EXPLORER_API   = settings.bnb_explorer_api
RBH_V4_HOOKS       = settings.rbh_v4_hooks
ETH_V4_HOOKS       = settings.eth_v4_hooks
BNB_V4_HOOKS       = settings.bnb_v4_hooks
RBH_WETH           = settings.rbh_weth
RBH_EXPLORER_TOKEN_URL = settings.rbh_explorer_token_url
ROBINHOOD_CHAT_ID  = settings.robinhood_chat_id
NOXA_FACTORY_ADDRESS      = settings.noxa_factory_address
NOXA_TOKEN_CREATED_TOPIC0 = settings.noxa_token_created_topic0

# Which sources the Robinhood detector subscribes to, from .env. Each is its own
# WS subscription: with only noxa on, a token launched through Uniswap on
# Robinhood is never seen, so a SOL ticker matching it can never fire.
RBH_NOXA_ENABLED = settings.rbh_noxa_enabled
RBH_V2_ENABLED   = settings.rbh_v2_enabled
RBH_V3_ENABLED   = settings.rbh_v3_enabled
RBH_V4_ENABLED   = settings.rbh_v4_enabled

# ── ETH Gas Fees: high-gas early buy detection (from .env) ──────
MIN_FEE_ETH              = settings.min_fee_eth
MONITOR_WINDOW_SECONDS   = settings.monitor_window_seconds
FIRST_BUY_WINDOW_SECONDS = settings.first_buy_window_seconds
MAX_GAS_MONITORS         = settings.max_gas_monitors
GAS_ALERT_CHAT_ID        = settings.gas_alert_chat_id
TRADING_ALERT_CHAT_ID    = settings.trading_alert_chat_id
# Dedicated endpoint for the gas feature; falls back to the ETH one so an
# upgrade with a blank value behaves exactly as before.
GAS_RPC_WSS  = settings.gas_rpc_wss or settings.eth_rpc_wss
# Ordered, like ETH and Robinhood already are. One endpoint alone meant that
# when Alchemy started answering 429 the gas socket retried the same refused
# URL every 60 seconds while the other feeds rotated past it.
GAS_WSS_ENDPOINTS = [u for u in (GAS_RPC_WSS, settings.gas_rpc_wss_fallback) if u]
GAS_RPC_HTTP = settings.gas_rpc_http or settings.eth_rpc_http

# ── Daily digest (from .env) ────────────────────────────────────
DIGEST_ENABLED = settings.digest_enabled
DIGEST_HOUR    = settings.digest_hour

# ── Health watchdog (from .env) ─────────────────────────────────
HEALTH_ALERT_ENABLED = settings.health_alert_enabled
HEALTH_DOWN_SECONDS  = settings.health_down_seconds
HEALTH_CHAT_ID       = settings.health_chat_id or settings.cross_chain_chat_id

# ── GMGN web fingerprint (from .env) ────────────────────────────
GMGN_DEVICE_ID = settings.gmgn_device_id
GMGN_FP_DID    = settings.gmgn_fp_did
GMGN_CLIENT_ID = settings.gmgn_client_id
CF_CLEARANCE   = settings.cf_clearance
GMGN_IMPERSONATE = settings.gmgn_impersonate

# ── Telethon userbot (Forwarder / flow4) — from .env ────────────
TELETHON_API_ID   = settings.telethon_api_id
TELETHON_API_HASH = settings.telethon_api_hash
TELETHON_SESSION  = settings.telethon_session
TELETHON_ENABLED  = bool(settings.telethon_api_id and settings.telethon_api_hash)

# ── Forwarder destination chat IDs (from .env; "" = route skipped) ──
def _int_or_none(v: str):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

DEST_OTTO               = _int_or_none(settings.dest_otto)
DEST_SIGNALS            = _int_or_none(settings.dest_signals)
DEST_DEXS               = _int_or_none(settings.dest_dexs)
DEST_PREMIUM_ALL        = _int_or_none(settings.dest_premium_all)

# chain -> the chat its premium detections are announced in. Keyed by the same
# chain slug the detection rows carry, so a new chain needs one entry and no
# branching anywhere else.
DEST_IMPORTANT_CALLER   = _int_or_none(settings.dest_important_caller)
DEST_RBH_X_MONITOR      = _int_or_none(settings.dest_rbh_x_monitor)
DEST_RBH_KEYWORD_MATCH  = _int_or_none(settings.dest_rbh_keyword_match)

# ── RSI Tracker ─────────────────────────────────────────────────
RSI_ETH_RPC_HTTP = settings.rsi_eth_rpc_http
RSI_BSC_RPC_HTTP = settings.rsi_bsc_rpc_http
RSI_RBH_RPC_HTTP = settings.rsi_rbh_rpc_http
RSI_SOL_RPC_HTTP = settings.rsi_sol_rpc_http
RSI_ENDPOINTS = {
    "eth": [u for u in (settings.rsi_eth_rpc_http, settings.rsi_eth_rpc_http_fallback) if u],
    "bsc": [u for u in (settings.rsi_bsc_rpc_http, settings.rsi_bsc_rpc_http_fallback) if u],
    "rbh": [u for u in (settings.rsi_rbh_rpc_http, settings.rsi_rbh_rpc_http_fallback) if u],
    "sol": [u for u in (settings.rsi_sol_rpc_http, settings.rsi_sol_rpc_http_fallback) if u],
}
RSI_ALERT_CHAT_ID = _int_or_none(settings.rsi_alert_chat_id)
BNB_V2_FACTORY = settings.bnb_v2_factory
BNB_V3_FACTORY = settings.bnb_v3_factory
RSI_RETENTION_DAYS = settings.rsi_retention_days

# ── Market Cap Alert ────────────────────────────────────────────
MCAP_ETH_RPC_HTTP = settings.mcap_eth_rpc_http
MCAP_BSC_RPC_HTTP = settings.mcap_bsc_rpc_http
MCAP_RBH_RPC_HTTP = settings.mcap_rbh_rpc_http
MCAP_SOL_RPC_HTTP = settings.mcap_sol_rpc_http
MCAP_ENDPOINTS = {
    "rbh": [u for u in (settings.mcap_rbh_rpc_http, settings.mcap_rbh_rpc_http_fallback) if u],
    "eth": [u for u in (settings.mcap_eth_rpc_http, settings.mcap_eth_rpc_http_fallback) if u],
    "bsc": [u for u in (settings.mcap_bsc_rpc_http, settings.mcap_bsc_rpc_http_fallback) if u],
    "sol": [u for u in (settings.mcap_sol_rpc_http, settings.mcap_sol_rpc_http_fallback) if u],
}
MCAP_ALERT_CHAT_ID = _int_or_none(settings.mcap_alert_chat_id)

DEST_PREMIUM_BY_CHAIN = {
    "eth": _int_or_none(settings.dest_premium_eth),
    "rbh": _int_or_none(settings.dest_premium_rbh),
    "bnb": _int_or_none(settings.dest_premium_bnb),
    "sol": _int_or_none(settings.dest_premium_sol),
}

# ── Robinhood — X — Token Monitor (from .env) ───────────────────
# Its own three-slot pool. Empty slots fall back to the Robinhood Chain
# endpoints — the same shape as GAS_RPC_WSS above, and what makes the feature
# run before its own endpoints have been set.
RBHX_WSS_ENDPOINTS = [u for u in (settings.rbhx_rpc_wss,
                                  settings.rbhx_rpc_wss_fallback,
                                  settings.rbhx_rpc_wss_fallback2) if u] or list(RBH_WSS_ENDPOINTS)
# True when the section is running on its own endpoints rather than borrowing
# Robinhood's — RPC Monitor says which, instead of showing slots that look set
# when they are not.
RBHX_OWN_ENDPOINTS = bool(settings.rbhx_rpc_wss or settings.rbhx_rpc_wss_fallback
                          or settings.rbhx_rpc_wss_fallback2)
RBHX_ALERT_CHAT_ID = _int_or_none(settings.rbhx_alert_chat_id)


def _parse_launchpads(raw: str) -> list[tuple[str, str, str, int]]:
    """"addr:topic:t1, addr:topic:d0" -> [(addr, topic, kind, index), ...].

    A malformed entry is skipped with a note rather than taking the whole
    feature down — one bad paste in .env should cost that launchpad, not all
    of them.
    """
    out = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 3:
            print(f"[scfg] RBHX_LAUNCHPADS: skipping {chunk!r} — need address:topic0:where")
            continue
        addr, topic, where = (p.strip() for p in parts)
        kind, idx = where[:1].lower(), where[1:]
        if kind not in ("t", "d") or not idx.isdigit():
            print(f"[scfg] RBHX_LAUNCHPADS: skipping {chunk!r} — 'where' must be t<N> or d<N>")
            continue
        out.append((addr.lower(), topic.lower(), kind, int(idx)))
    return out


RBHX_LAUNCHPADS = _parse_launchpads(settings.rbhx_launchpads)


def _addr_list(raw: str) -> list[str]:
    """Comma-separated addresses -> a clean lowercase list."""
    out = []
    for chunk in (raw or "").split(","):
        a = chunk.strip().lower()
        if a.startswith("0x") and len(a) == 42:
            out.append(a)
        elif chunk.strip():
            print(f"[scfg] not an address, skipped: {chunk.strip()!r}")
    return out


# ── Robinhood Launchpad Monitor ─────────────────────────────────
# Which factories each launchpad mints through. The adapters in
# scanners/launchpads/ carry the event topics and the reading logic; only the
# addresses live in .env, so a redeployed factory is a config change.
PONS_FACTORIES = _addr_list(settings.pons_factories)
# v2 is a separate deployment, not another address for v1 — its own factory,
# its own event, its own filter tab.
PONS_V2_FACTORIES = _addr_list(settings.pons_v2_factories)
# Uniswap's own LiquidityLauncher, which pools.trade launches through.
POOLS_FACTORIES = _addr_list(settings.pools_factories)
# The token factory underneath them — there is more than one launcher, and a
# launch was missed because of it.
POOLS_TOKEN_FACTORIES = _addr_list(settings.pools_token_factories)
# Virtuals Protocol's launcher proxy — one address, two events.
VIRTUALS_FACTORIES = _addr_list(settings.virtuals_factories)
# LetsCash's launchpad factory — one address, one TokenLaunched event.
LETSCASH_FACTORIES = _addr_list(settings.letscash_factories)
FLAP_PORTALS   = _addr_list(settings.flap_portals)
LAUNCHPAD_RETENTION_DAYS = settings.launchpad_retention_days
RBHX_DEV_BUY_MAX_ETH = settings.rbhx_dev_buy_max_eth
RBHX_DEV_BUY_WINDOW  = settings.rbhx_dev_buy_window
RBHX_DEV_BUY_STRONG_ETH = settings.rbhx_dev_buy_strong_eth
RBHX_RETENTION_DAYS = settings.rbhx_retention_days

# ── Forwarder source channels (from .env) ───────────────────────
SOURCE_CALL   = settings.source_call
SOURCE_BUYBOT = settings.source_buybot
SOURCE_DEXS   = settings.source_dexs
SOURCE_OTTO   = settings.source_otto


def refresh_from_registry(enabled: dict[str, bool]) -> None:
    """Overlay live dashboard toggles onto the module-level flags.

    `enabled` is registry.enabled_map() — {service_id: bool}. We only mirror the
    Robinhood source toggles here (the master on/off for a whole scanner is
    handled by the supervisor deciding whether to start the task at all).
    """
    global RBH_NOXA_ENABLED, RBH_V2_ENABLED, RBH_V3_ENABLED, RBH_V4_ENABLED
    global SOL_DISCOVERY_ENABLED, BASE_RPC_ENABLED
    if "rpc_base" in enabled:
        BASE_RPC_ENABLED = bool(enabled["rpc_base"])
    # SOL's on-chain discovery is a child task of a scanner the supervisor keeps
    # running for other reasons, so it cannot be started/stopped by "should this
    # worker exist" — it reads this flag instead (see SolanaScanner.sync_discovery).
    if "sol_onchain_discovery" in enabled:
        SOL_DISCOVERY_ENABLED = bool(enabled["sol_onchain_discovery"])
    # The registry's sol_to_rbh switch is the master: off means no Robinhood
    # source at all. Which sources run when it is on comes from .env. Applying
    # it to all four keeps them consistent — switching the flow off used to
    # silence noxa while leaving V2/V3/V4 armed.
    if "sol_to_rbh" in enabled:
        on = bool(enabled["sol_to_rbh"])
        RBH_NOXA_ENABLED = on and settings.rbh_noxa_enabled
        RBH_V2_ENABLED   = on and settings.rbh_v2_enabled
        RBH_V3_ENABLED   = on and settings.rbh_v3_enabled
        RBH_V4_ENABLED   = on and settings.rbh_v4_enabled
