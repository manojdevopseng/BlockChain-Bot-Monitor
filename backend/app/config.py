"""Central configuration — loaded from environment / .env.

Uses pydantic-settings so every value is typed and overridable via env vars.
Cross-platform: no Linux-only assumptions (dev on Windows, deploy on Ubuntu).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _blank_means_default(cls, data):
        """Treat an empty value in .env as "not set" so the default applies.

        Without this, a numeric key left blank (e.g. `API_PORT=`) would raise a
        validation error and the app would refuse to boot — a nasty surprise
        after copying .env.example on a fresh EC2 box.
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and v.strip() == "")}
        return data

    # ── Server ──────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── Database ────────────────────────────────────────────
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "blockchain_bot"

    # ── Data retention (days) ───────────────────────────────
    # Enforced by MongoDB TTL indexes: mongod expires old documents in its own
    # background sweep (~every 60s), so the app does zero deletion work. Set a
    # value to 0 to keep that collection forever (not recommended on EC2).
    log_retention_days: int = 15
    alert_retention_days: int = 15
    token_retention_days: int = 30
    archive_retention_days: int = 15

    # ── Auth ────────────────────────────────────────────────
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 1440
    admin_username: str = "admin"
    admin_password: str = "admin"

    # ── GMGN ────────────────────────────────────────────────
    gmgn_api_key: str = ""
    gmgn_client_id: str = ""
    gmgn_device_id: str = ""

    # ── GMGN request pacing / retries ───────────────────────
    # Fallback pace (requests/sec) for any GMGN client created without an
    # explicit rate. The scanner uses GMGN_SCAN_RATE below.
    api_rate_limit: float = 1.5
    # Pace of the shared scanning client. Deliberately slower than the fallback:
    # gmgn.ai's Cloudflare starts returning 403 if polled too fast from a
    # datacenter IP. 0.4 req/s = one request every 2.5s.
    gmgn_scan_rate: float = 0.4
    # Attempts per GMGN call before giving up.
    retry_max: int = 3
    # Exponential backoff base, in seconds: 2 -> 4 -> 8 …
    retry_backoff_base: float = 2.0

    # ── Solana scanner thresholds ───────────────────────────
    # Seconds between GMGN new-pair scans.
    sol_scan_interval: float = 5.0
    # A token starts its watch window only when BOTH are met.
    sol_min_mcap: float = 40000.0
    sol_min_fees: float = 1.0
    # How long a triggered ticker stays watched for a cross-chain match (min).
    sol_watch_window: int = 360

    # ── ETH Gas Fees (high-gas early buy detection) ─────────
    # A buy whose gas fee reaches this many ETH fires the alert.
    min_fee_eth: float = 0.0009
    # Hard cap on how long one token is watched, counted from detection.
    monitor_window_seconds: int = 14400      # 4h
    # Shorter window started by the first buy; whichever timer fires first wins.
    first_buy_window_seconds: int = 240
    # Max tokens watched for swaps at once (each holds a WS subscription).
    max_gas_monitors: int = 150

    # ── Solana on-chain discovery (optional) ────────────────
    # Watch a launchpad's own program for new mints instead of relying on
    # GMGN's rolling new-pairs feed. Needs SOL_RPC_WSS; off without it.
    # Only the launchpads we watch: pump / bonk / bonkers / bags. Blank = that
    # launchpad is not watched on-chain and keeps coming from the GMGN feed.
    sol_pump_program: str = ""
    sol_bonk_program: str = ""
    sol_bonkers_program: str = ""
    sol_bags_program: str = ""

    # ── Daily digest ────────────────────────────────────────
    # One summary message a day to ALERT_CHAT_ID: what fired and how it did.
    digest_enabled: bool = True
    digest_hour: int = 9          # IST hour

    # ── Health watchdog ─────────────────────────────────────
    health_alert_enabled: bool = True
    # Alert if a chain's WebSocket stays down longer than this (seconds).
    health_down_seconds: int = 300

    # ── RPC endpoints ───────────────────────────────────────
    eth_rpc_http: str = ""
    eth_rpc_wss: str = ""
    rbh_rpc_http: str = ""
    rbh_rpc_wss: str = ""
    sol_rpc_http: str = ""
    sol_rpc_wss: str = ""
    # ETH Gas Fees runs on its own endpoint when these are set. It holds one
    # subscription per watched pair and reads a receipt for every buy, so on a
    # shared key it can eat the compute units that new-pair detection needs.
    # Blank = share ETH_RPC_*.
    gas_rpc_http: str = ""
    gas_rpc_wss: str = ""
    # Second endpoint per chain. One provider having a bad hour stopped
    # detection outright before, with nothing to fall back to. Blank = no
    # failover, exactly as before.
    eth_rpc_wss_fallback: str = ""
    rbh_rpc_wss_fallback: str = ""

    # ── Telegram ────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telethon_api_id: int = 0
    telethon_api_hash: str = ""
    telethon_session: str = "final_session"
    gmgn_fp_did: str = ""
    cf_clearance: str = ""
    # Browser profile curl_cffi impersonates when talking to GMGN. gmgn.ai
    # fingerprints the TLS handshake: older Chrome profiles (and plain aiohttp)
    # get a flat 403, a current one passes. Bump this when Cloudflare tightens.
    gmgn_impersonate: str = "chrome136"

    # ── Destination / alert chat IDs (deployment-specific — NOT hardcoded) ──
    # Empty by default; set the real values in .env. Empty = that route is
    # skipped / dry-run, so the app still runs on a fresh box.
    cross_chain_chat_id: str = ""
    robinhood_chat_id: str = ""
    health_chat_id: str = ""
    # BlockChainBot group — errors + start/stop/restart notices go here.
    alert_chat_id: str = ""
    # Where the bot answers /commands. Blank = same group as ALERT_CHAT_ID.
    # Anywhere else the bot stays silent and shows no "/" menu at all.
    command_chat_id: str = ""
    # "High Gas Early Activity" alerts (ETH Gas Fees feature).
    gas_alert_chat_id: str = ""
    # Repeat of the same error is re-sent at most once per this many seconds.
    error_alert_cooldown: int = 900
    # An identical WARNING/ERROR is written to the log at most once per this
    # many seconds; repeats are counted and reported on the next write. Stops a
    # scanner failing every few seconds from filling MongoDB. 0 = no throttle.
    log_dedup_seconds: int = 300
    # Max Telegram sends per minute per destination chat (Telegram's own limit
    # for groups is ~20/min; staying under it avoids FloodWait bans).
    tg_max_per_minute: int = 18
    dest_otto: str = ""
    dest_signals: str = ""
    dest_dexs: str = ""
    dest_premium_eth_caller: str = ""
    dest_premium_all: str = ""

    # ── Chain contract addresses (protocol constants — env-overridable) ──
    eth_v2_factory: str = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
    eth_v3_factory: str = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
    eth_v4_poolmanager: str = "0x000000000004444c5dc75cB358380D2e3dE08A90"
    eth_weth: str = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    rbh_v2_factory: str = ""
    rbh_v3_factory: str = ""
    rbh_v4_poolmanager: str = ""
    rbh_weth: str = ""
    rbh_explorer_token_url: str = "https://robinhoodchain.blockscout.com/token/{addr}"
    noxa_factory_address: str = ""
    noxa_token_created_topic0: str = ""

    # ── Which sources the Robinhood detector listens to ──
    # Robinhood Chain carries both noxa.fun launches and ordinary Uniswap
    # deployments, and a cross-chain match can come from either. Each source is
    # a separate WS subscription, so they are switched independently.
    rbh_noxa_enabled: bool = True
    rbh_v2_enabled: bool = True
    rbh_v3_enabled: bool = True
    rbh_v4_enabled: bool = True

    # ── Forwarder source channels (env-overridable, not hardcoded) ──
    source_call: str = "CallAnalyser2"
    source_buybot: str = "BuyBotTracker"
    source_dexs: str = "dexssignal"
    source_otto: str = "OttoEthDeployments"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
