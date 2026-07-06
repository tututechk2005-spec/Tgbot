"""
Configuration module for the Telegram Binance Trading Bot.
Reads environment variables set on Railway and defines all constants.
"""

import os

# ── Telegram ─────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID: int = int(os.environ["ADMIN_CHAT_ID"])

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = "database.db"

# ── Encryption ────────────────────────────────────────────────────────────────
# 32-byte key derived at first run and stored in the DB settings table.
# We keep a module-level cache so we never hit the DB twice per process.
ENCRYPTION_KEY_CACHE: bytes | None = None

# ── Binance endpoints ─────────────────────────────────────────────────────────
BINANCE_SPOT_BASE: str = "https://api.binance.com"
BINANCE_FUTURES_BASE: str = "https://fapi.binance.com"
BINANCE_SPOT_TESTNET: str = "https://testnet.binance.vision"
BINANCE_FUTURES_TESTNET: str = "https://testnet.binancefuture.com"

# ── Scanner ───────────────────────────────────────────────────────────────────
SCANNER_FAST_INTERVAL: int = 1    # seconds
SCANNER_SLOW_INTERVAL: int = 10   # seconds
MIN_VOLUME_USDT: float = 5_000_000.0   # 5 M USDT 24 h volume minimum
MIN_QUOTE_VOLUME: float = 100_000.0    # min quote volume per kline
MAX_SPREAD_PCT: float = 0.15           # 0.15% max bid/ask spread

# ── Signal engine ─────────────────────────────────────────────────────────────
MIN_SIGNAL_SCORE: float = 90.0   # minimum confidence to emit a signal
TARGET_RR_OPTIONS: list[float] = [2.0, 2.5, 3.0]

# ── Timeframes used for multi-TF analysis ─────────────────────────────────────
ANALYSIS_TIMEFRAMES: list[str] = ["5m", "15m", "1h", "4h", "1d"]

# ── Position monitor ─────────────────────────────────────────────────────────
MONITOR_POLL_SECONDS: int = 5

# ── Rate limiting ─────────────────────────────────────────────────────────────
USER_RATE_LIMIT_SECONDS: float = 0.5   # min seconds between commands per user

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = "INFO"
MAX_LOG_ROWS: int = 5000   # rows kept in the logs table

# ── Misc ──────────────────────────────────────────────────────────────────────
BOT_VERSION: str = "1.0.0"
