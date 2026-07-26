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

# NOTE: the reference repo also carried MIN_LIQUIDITY_USD, but nothing ever
# read it (its own .env said "read by config (kept)"). The SOL scanner filters
# on SOL_MIN_MCAP + SOL_MIN_FEES instead, so it is not carried over here.

CHAIN = "eth"

# ── Solana (from .env) ──────────────────────────────────────────
SOL_RPC_HTTP      = settings.sol_rpc_http
SOL_RPC_WSS       = settings.sol_rpc_wss
SOL_SCAN_INTERVAL = settings.sol_scan_interval
SOL_MIN_FEES      = settings.sol_min_fees
SOL_MIN_MCAP      = settings.sol_min_mcap
SOL_WATCH_WINDOW  = settings.sol_watch_window
SOL_CHAIN         = "sol"

# ── Cross-chain matching (from .env) ────────────────────────────
CROSS_CHAIN_CHAT_ID = settings.cross_chain_chat_id

# ── Ethereum on-chain (SOL→ETH) — from .env ─────────────────────
ETH_RPC_WSS        = settings.eth_rpc_wss
ETH_RPC_HTTP       = settings.eth_rpc_http
ETH_V2_FACTORY     = settings.eth_v2_factory
ETH_V3_FACTORY     = settings.eth_v3_factory
ETH_V4_POOLMANAGER = settings.eth_v4_poolmanager
ETH_WETH           = settings.eth_weth

# ── Robinhood Chain on-chain (SOL→Robinhood) — from .env ────────
RBH_RPC_WSS        = settings.rbh_rpc_wss
RBH_RPC_HTTP       = settings.rbh_rpc_http
RBH_V2_FACTORY     = settings.rbh_v2_factory
RBH_V3_FACTORY     = settings.rbh_v3_factory
RBH_V4_POOLMANAGER = settings.rbh_v4_poolmanager
RBH_WETH           = settings.rbh_weth
RBH_EXPLORER_TOKEN_URL = settings.rbh_explorer_token_url
ROBINHOOD_CHAT_ID  = settings.robinhood_chat_id
NOXA_FACTORY_ADDRESS      = settings.noxa_factory_address
NOXA_TOKEN_CREATED_TOPIC0 = settings.noxa_token_created_topic0

# Robinhood detection-source toggles (overlaid from the registry at runtime).
RBH_NOXA_ENABLED = True
RBH_V2_ENABLED   = False
RBH_V3_ENABLED   = False
RBH_V4_ENABLED   = False

# ── ETH Gas Fees: high-gas early buy detection (from .env) ──────
MIN_FEE_ETH              = settings.min_fee_eth
MONITOR_WINDOW_SECONDS   = settings.monitor_window_seconds
FIRST_BUY_WINDOW_SECONDS = settings.first_buy_window_seconds
MAX_GAS_MONITORS         = settings.max_gas_monitors
GAS_ALERT_CHAT_ID        = settings.gas_alert_chat_id

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
DEST_PREMIUM_ETH_CALLER = _int_or_none(settings.dest_premium_eth_caller)
DEST_PREMIUM_ALL        = _int_or_none(settings.dest_premium_all)

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
    global RBH_NOXA_ENABLED
    # There is one master RBH toggle in the registry (sol_to_rbh); noxa follows it
    # by default. V2/V3/V4 stay off unless explicitly turned on via env later.
    if "sol_to_rbh" in enabled:
        RBH_NOXA_ENABLED = bool(enabled["sol_to_rbh"])
