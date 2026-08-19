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
    # Second Dashboard. One row per call is far more rows than the merged
    # panel keeps, and the images are the heaviest thing the app stores — so
    # the pictures age out well before the text they arrived with.
    calls_retention_days: int = 30
    calls_media_retention_days: int = 7

    # ── Auth ────────────────────────────────────────────────
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 1440
    admin_username: str = "admin"
    admin_password: str = "admin"
    # The read-only account. It sees the whole dashboard and can change nothing
    # — Forwarder, Commands and Settings are closed to it. Both blank (the
    # default) means the account does not exist and only the admin can log in;
    # set them in .env to create it.
    user_username: str = ""
    user_password: str = ""

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
    # Half an hour rather than the four minutes this started at: the first buy
    # is when a token starts being traded, not when it stops, and the gas fees
    # worth seeing kept landing after the watch had already been dropped.
    first_buy_window_seconds: int = 1800
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

    # ── AI narrative agent (Robinhood) ──────────────────────
    # Reads a new token's X (Twitter) link, checks the account is verified, and
    # asks Grok whether the post matches one of the watched narratives.
    xai_api_key: str = ""
    xai_base_url: str = "https://api.x.ai/v1"
    # The narrative pass: one label out of sixteen, on every launch that clears
    # the gates — a few hundred an hour, so this is the call that costs money.
    # Measured on ten posts: grok-4.3 got 10/10, and the cheaper
    # grok-4.20-non-reasoning got 9/10, its miss being a false positive — it
    # read "wen lambo ser" as Elon Musk. For a filter, a false positive is the
    # expensive error, so the accurate one wins even at ~7x the latency.
    xai_model: str = "grok-4.3"
    # Fact check: asked about one token when somebody presses the button, a few
    # times a day. Volume that small buys the reasoning model, and "is this
    # real" is the judgement worth thinking about.
    #
    # Note it has no live data: xAI retired Live Search in favour of the Agent
    # Tools API, so this answers from training knowledge until that is wired in.
    xai_fact_model: str = "grok-4.20-reasoning"
    # PumpPortal's public realtime feed: pump.fun launches, pushed. No key —
    # the key on that site is for its trading endpoints, which nothing here
    # touches. Each token carries its own metadata URI, and that URI carries the
    # X link, so the link arrives with the token instead of a minute later.
    pumpportal_ws: str = "wss://pumpportal.fun/api/data"
    # Seconds between passes of the judging loop over tokens already collected.
    ai_scan_interval: int = 20
    # Decisions are recorded but nothing is sent while this is on. Leave it on
    # for the first day and read the log before letting it post.
    ai_dry_run: bool = True
    # Verdict must reach this to notify.
    ai_min_confidence: int = 7
    # Which kinds of verified account count. Paid blue is `individual`; drop it
    # from this list if blue-tick spam gets through.
    ai_verified_types: str = "government,business,individual"
    # Where notifications go. Blank = ROBINHOOD_CHAT_ID.
    ai_chat_id: str = ""
    # NOTE: AI_OTHER_MIN_CONFIDENCE, AI_BIG_ACCOUNT_FOLLOWERS,
    # AI_TWEET_MAX_AGE_HOURS and AI_MAX_LINK_READS used to live here. The gate
    # rewrite in 26cc827 removed the code that read them but left the fields,
    # so they sat here and in .env.example looking adjustable while changing
    # them did nothing at all. Removed rather than re-wired: the gates they
    # belonged to no longer exist.

    # ── What a launch has to be worth ───────────────────────
    # The Telegram rule, and only that — the model's gate is separate and does
    # not count launches. A link carrying this many launches inside the window
    # is a coordinated push, whatever names they went out under, and one of
    # those that also runs is what is worth a message. Counted per IST day.
    ai_link_burst_count: int = 5
    ai_link_burst_window: int = 300
    # A launch is watched for its market cap for this long after it opens, and
    # crossing this many dollars inside that minute is what puts it in front of
    # a person. Measured live: a launch opens at ~28 SOL, so at $74 SOL this is
    # about a 4x inside the first minute.
    ai_mcap_watch_seconds: int = 60
    ai_telegram_mcap_usd: float = 8000.0

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
    gas_rpc_wss_fallback: str = ""
    # Second endpoint per chain. One provider having a bad hour stopped
    # detection outright before, with nothing to fall back to. Blank = no
    # failover, exactly as before.
    eth_rpc_wss_fallback: str = ""
    rbh_rpc_wss_fallback: str = ""
    # Second endpoint for SOL mint discovery. Note it must support
    # `logsSubscribe` — Alchemy's Solana endpoint does not ("Method not found",
    # tested), so a second Helius key is the usual choice.
    sol_rpc_wss_fallback: str = ""
    # Third endpoint. Two is enough to survive one provider going down; it is
    # not enough when a quota runs out, because the second is usually being
    # drained by the same traffic and follows within the hour. With three,
    # rotation has somewhere to go while the first two reset. ETH and Robinhood
    # only — SOL's WSS stays a 2-way pair (see sol_rpc_wss_fallback above): it
    # is a different provider family (needs logsSubscribe) with a much smaller
    # pool of candidates, so a third slot was more clutter than safety margin.
    eth_rpc_wss_fallback2: str = ""
    rbh_rpc_wss_fallback2: str = ""
    # Second endpoint for the SOL HTTP RPC. Unlike SOL_RPC_WSS, this one is not
    # discovery or the market-cap watch — it is a single getAccountInfo check
    # the forwarder makes to confirm a Solana address seen in a premium group is
    # a real on-chain account before recording it. A different job from the WSS
    # pair, so it gets its own 2-way failover rather than sharing one.
    sol_rpc_http_fallback: str = ""
    # Same idea for the ETH/RBH side of premium detection: eth_getCode + token
    # metadata lookups over ETH_RPC_HTTP/RBH_RPC_HTTP. Found live on 2026-07-30
    # that the account behind these had hit its *monthly* cap — not the kind of
    # thing rotation-on-a-per-second-429 fixes on its own, so these need a
    # genuinely different key to help at all.
    eth_rpc_http_fallback: str = ""
    rbh_rpc_http_fallback: str = ""
    # BNB Chain (BSC) — premium-caller detection only. There is no BNB
    # discovery scanner, so unlike ETH/RBH there is no WSS pair here: an
    # address seen in a premium group is checked against BSC the same way it
    # is checked against Ethereum, and that is all.
    bnb_rpc_http: str = ""
    bnb_rpc_http_fallback: str = ""
    # WBNB, the base token a BSC pair is priced against — the equivalent of
    # ETH_WETH. Used to work out which side of a pair is the actual token.
    bnb_wbnb: str = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
    # Base — premium-caller detection only, same shape as BNB above: no
    # discovery scanner, no WSS, just "is this address a contract on Base".
    # Two endpoints because one public RPC going quiet must not take the chain
    # off the board; the pool tries them in order.
    base_rpc_http: str = "https://mainnet.base.org"
    base_rpc_http_fallback: str = "https://base.llamarpc.com"
    # WETH on Base, the token a Base pair is priced against.
    base_weth: str = "0x4200000000000000000000000000000000000006"

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
    dest_premium_all: str = ""
    # One chat per chain for premium-caller detections. Sent by the BOT, not
    # the userbot: the bot cannot forward from the premium source groups (it is
    # not in them — that is what the Telethon session is for), so it posts a
    # composed message instead. Blank = that chain's detections are recorded in
    # the dashboard panel and nothing is sent.
    dest_premium_eth: str = ""
    dest_premium_rbh: str = ""
    dest_premium_bnb: str = ""
    dest_premium_sol: str = ""
    # The Important Caller group. Callers starred in Forwarder → Premium Groups
    # have their messages mirrored here as well as to DEST_PREMIUM_ALL, so it
    # is the filtered read of the same feed. Blank = the feature is off.
    dest_important_caller: str = ""

    # ── Robinhood — X — Token Monitor ───────────────────────
    # A Robinhood launchpad token carries its socials in the contract's own
    # metadata() string. This watches new pairs, pulls the X profile link out
    # of it and records who is behind the launch. Only @username links: a link
    # to one post says nothing about the account.
    #
    # Its own WebSockets, so a busy discovery socket cannot starve it and its
    # quota is its own. Blank falls back to the Robinhood Chain endpoints,
    # which is also how it runs before you have set any.
    rbhx_rpc_wss: str = ""
    rbhx_rpc_wss_fallback: str = ""
    rbhx_rpc_wss_fallback2: str = ""
    # Where detections are posted. Blank = recorded in the panel, not sent.
    dest_rbh_x_monitor: str = ""
    dest_rbh_keyword_match: str = ""

    # ── RSI Tracker ──────────────────────────────────────────────────────────
    # Its own endpoints per chain, two each; blank borrows that chain's own.
    rsi_eth_rpc_http: str = ""
    rsi_eth_rpc_http_fallback: str = ""
    rsi_bsc_rpc_http: str = ""
    rsi_bsc_rpc_http_fallback: str = ""
    rsi_rbh_rpc_http: str = ""
    rsi_rbh_rpc_http_fallback: str = ""
    rsi_sol_rpc_http: str = ""
    rsi_sol_rpc_http_fallback: str = ""
    # Where RSI alerts go.
    rsi_alert_chat_id: str = ""
    # PancakeSwap's factories — the BSC equivalents of the ETH ones above.
    bnb_v2_factory: str = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
    bnb_v3_factory: str = "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7"
    # How long candles and readings are kept, like every other panel.
    rsi_retention_days: int = 15

    # ── Market Cap Alert ─────────────────────────────────────────────────────
    # Its own endpoints again, two per chain, so a fifteen-second market cap
    # loop cannot spend the RSI tracker's rate limit. Blank borrows RSI's, and
    # then the chain's own — which is what makes it testable before they are
    # filled in.
    mcap_eth_rpc_http: str = ""
    mcap_eth_rpc_http_fallback: str = ""
    mcap_bsc_rpc_http: str = ""
    mcap_bsc_rpc_http_fallback: str = ""
    mcap_rbh_rpc_http: str = ""
    mcap_rbh_rpc_http_fallback: str = ""
    mcap_sol_rpc_http: str = ""
    mcap_sol_rpc_http_fallback: str = ""
    # Where market cap alerts go, and where its /menu screen answers.
    mcap_alert_chat_id: str = ""
    # Where "all its endpoints are refusing" goes. Its own chat, not the
    # general alert group, so this feed's health is readable on its own.
    rbhx_alert_chat_id: str = ""
    # Rows and skip/watch entries are dropped this many days after they land.
    rbhx_retention_days: int = 15
    # Launchpads to watch for a new token, so a launch is seen when it is
    # minted rather than when it graduates to a Uniswap pool — a curve can run
    # for hours, and a token that never graduates would never be seen at all.
    #
    #   address:topic0:where   (comma-separated)
    #
    # `where` says which part of the event carries the new token's address:
    # t<N> for topic N, d<N> for data word N. Each launchpad shapes its event
    # differently; all three below were read off real creation receipts.
    # Blank = fall back to watching pool creation only.
    rbhx_launchpads: str = ""

    # ── Robinhood Launchpad Monitor ─────────────────────────
    # Every launch from the launchpads below, whether or not it carries an X
    # profile — the launchpad-centric view, where the X monitor is the
    # profile-centric one. Both are fed by the same worker over the same
    # socket, so this costs no extra RPC.
    #
    # One key per launchpad, comma-separated where it runs more than one
    # factory. Addresses come from each launchpad's own docs; the event shape
    # and how to read its socials live in app/scanners/launchpads/<name>.py,
    # because every launchpad does that differently.
    #
    # Pons — docs.ponsfamily.com: active factory first, then legacy.
    pons_factories: str = ""
    pons_v2_factories: str = ""
    pools_factories: str = ""
    pools_token_factories: str = ""
    virtuals_factories: str = ""
    # LetsCash — letscash.fun: the docs print "n/a" for every address, so this
    # one comes from the site's own config endpoint (api.letscash.fun/api/config,
    # `contracts.launchpadFactory`).
    letscash_factories: str = ""
    # Flap — docs.flap.sh: the Portal that mints both token implementations.
    flap_portals: str = ""
    # Rows are dropped this many days after they land.
    launchpad_retention_days: int = 15
    # Skip a launch whose own deployer buys more than this much of it, in the
    # chain's native ETH. The dev wallet comes from the same signed blob the
    # handle does, so this only applies to launches that carry one — and only
    # to that wallet, not to a fresh one funded on the side.
    #
    # 0 disables the check and every launch is kept.
    rbhx_dev_buy_max_eth: float = 3.0
    # A ceiling on how long the deployer is watched after a launch, not a
    # wait: the watch stops the moment a buy lands. The first trades on a
    # measured launch came at +30s, so this has to outlast that; three minutes
    # is where it stops being worth a held subscription.
    rbhx_dev_buy_window: int = 180
    # The other end of the same number. Above this the deployer has put real
    # money into their own launch, which is the one thing on this panel that
    # costs the person doing it something — so it is called out rather than
    # left as a figure in a column: the row is highlighted and the Telegram
    # alert says so.
    #
    # 0 turns the marking off; every launch still lands as before.
    rbhx_dev_buy_strong_eth: float = 0.199

    # ── Chain contract addresses (protocol constants — env-overridable) ──
    eth_v2_factory: str = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
    eth_v3_factory: str = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
    eth_v4_poolmanager: str = "0x000000000004444c5dc75cB358380D2e3dE08A90"
    eth_weth: str = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    rbh_v2_factory: str = ""
    rbh_v3_factory: str = ""
    rbh_v4_poolmanager: str = ""
    # V4 keeps every pool inside the PoolManager, so there is no pool address to
    # call: the price is read through the StateView periphery contract by pool
    # id. One per chain, at the canonical deployment.
    eth_v4_stateview: str = "0x7fFE42C4a5DEeA5b0feC41C94C136Cf115597227"
    rbh_v4_stateview: str = "0xF3334192D15450CdD385c8B70e03f9A6bD9E673b"
    bnb_v4_poolmanager: str = "0x28e2Ea090877bF75740558f6BFB36A5ffeE9e9dF"
    bnb_v4_stateview: str = "0xd13Dd3D6E93f276FAFc9Db9E6BB47C1180aeE0c4"
    # A pool with a hook has no id we can guess — the fee, tick spacing and hook
    # address are the launchpad's own — so it is looked up once, by log, through
    # the chain's public explorer API (Blockscout's etherscan-compatible one).
    # Blank means hooked pools are not found on that chain; the standard ones
    # still are, because those ids are computed rather than looked up.
    eth_explorer_api: str = "https://eth.blockscout.com/api"
    rbh_explorer_api: str = "https://robinhoodchain.blockscout.com/api"
    bnb_explorer_api: str = ""
    # Hook contracts we know about, comma-separated. A launchpad runs one hook
    # for every token it mints, so its address is all that stands between "this
    # pool id cannot be guessed" and computing it directly — no explorer, no
    # rate limit. LetsCash's is the default because it is the one on Robinhood
    # minting into V4 today.
    rbh_v4_hooks: str = "0x75A54357D9C78a2Db19004a5FDc76c50F9242AEC"
    eth_v4_hooks: str = ""
    bnb_v4_hooks: str = ""
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
