"""
Database module — SQLite-only, auto-creates on first run.
All tables are created here. No external DB required.
"""

import sqlite3
import time
import logging
from contextlib import contextmanager
from config import DATABASE_PATH, MAX_LOG_ROWS

logger = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    chat_id          INTEGER PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,
    last_name        TEXT,
    joined_at        REAL    NOT NULL DEFAULT (unixepoch()),
    last_seen        REAL    NOT NULL DEFAULT (unixepoch()),
    is_banned        INTEGER NOT NULL DEFAULT 0,
    account_type     TEXT    NOT NULL DEFAULT 'spot',  -- 'spot' | 'futures'
    network          TEXT    NOT NULL DEFAULT 'testnet' -- 'testnet' | 'real'
);

CREATE TABLE IF NOT EXISTS api_keys (
    chat_id          INTEGER PRIMARY KEY REFERENCES users(chat_id) ON DELETE CASCADE,
    enc_api_key      BLOB    NOT NULL,
    enc_secret_key   BLOB    NOT NULL,
    is_valid         INTEGER NOT NULL DEFAULT 0,
    validated_at     REAL,
    updated_at       REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          INTEGER NOT NULL REFERENCES users(chat_id),
    binance_order_id TEXT,
    symbol           TEXT    NOT NULL,
    side             TEXT    NOT NULL,  -- 'BUY' | 'SELL'
    account_type     TEXT    NOT NULL,  -- 'spot' | 'futures'
    entry_price      REAL    NOT NULL,
    quantity         REAL    NOT NULL,
    stop_loss        REAL,
    take_profit      REAL,
    close_price      REAL,
    pnl              REAL,
    status           TEXT    NOT NULL DEFAULT 'open', -- 'open' | 'closed' | 'cancelled'
    strategy         TEXT,
    signal_score     REAL,
    signal_reason    TEXT,
    timeframe        TEXT,
    indicators_json  TEXT,
    opened_at        REAL    NOT NULL DEFAULT (unixepoch()),
    closed_at        REAL,
    duration_secs    REAL
);

CREATE TABLE IF NOT EXISTS signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT    NOT NULL,
    direction        TEXT    NOT NULL,  -- 'BUY' | 'SELL'
    entry            REAL    NOT NULL,
    stop_loss        REAL    NOT NULL,
    take_profit      REAL    NOT NULL,
    rr_ratio         REAL    NOT NULL,
    score            REAL    NOT NULL,
    reasons          TEXT    NOT NULL,
    timeframe        TEXT    NOT NULL,
    trend            TEXT,
    volume_status    TEXT,
    momentum_status  TEXT,
    indicators_json  TEXT,
    created_at       REAL    NOT NULL DEFAULT (unixepoch()),
    sent             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS learning_data (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy         TEXT    NOT NULL,
    symbol           TEXT    NOT NULL,
    timeframe        TEXT    NOT NULL,
    indicators_json  TEXT    NOT NULL,
    signal_score     REAL,
    entry            REAL    NOT NULL,
    exit_price       REAL,
    pnl_pct          REAL,
    won              INTEGER,           -- 1 win, 0 loss, NULL open
    duration_secs    REAL,
    weight_boost     REAL    NOT NULL DEFAULT 1.0,
    recorded_at      REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS strategy_weights (
    strategy         TEXT    PRIMARY KEY,
    weight           REAL    NOT NULL DEFAULT 1.0,
    win_count        INTEGER NOT NULL DEFAULT 0,
    loss_count       INTEGER NOT NULL DEFAULT 0,
    total_pnl        REAL    NOT NULL DEFAULT 0.0,
    updated_at       REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    level            TEXT    NOT NULL DEFAULT 'INFO',
    event            TEXT    NOT NULL,
    detail           TEXT,
    chat_id          INTEGER,
    created_at       REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS broadcast_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_chat_id    INTEGER NOT NULL,
    message_text     TEXT,
    media_type       TEXT,
    total_users      INTEGER NOT NULL DEFAULT 0,
    success_count    INTEGER NOT NULL DEFAULT 0,
    fail_count       INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'pending',
    created_at       REAL    NOT NULL DEFAULT (unixepoch()),
    finished_at      REAL
);

CREATE TABLE IF NOT EXISTS rate_limits (
    chat_id          INTEGER PRIMARY KEY,
    last_action      REAL    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_trades_chat_id    ON trades(chat_id);
CREATE INDEX IF NOT EXISTS idx_trades_status     ON trades(status);
CREATE INDEX IF NOT EXISTS idx_signals_created   ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_logs_created      ON logs(created_at);
CREATE INDEX IF NOT EXISTS idx_learning_strategy ON learning_data(strategy);
"""

# ── Connection factory ─────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Return a WAL-mode connection with row_factory set."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

@contextmanager
def db_conn():
    """Context manager that commits on success, rolls back on error."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ── Init ───────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create all tables and seed default settings if needed."""
    with db_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        # Seed encryption key placeholder (actual key generated in security.py)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('schema_version', '1')"
        )
    logger.info("Database initialised at %s", DATABASE_PATH)

# ── Settings helpers ───────────────────────────────────────────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key: str, value: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

# ── User helpers ───────────────────────────────────────────────────────────────

def upsert_user(chat_id: int, username: str | None, first_name: str | None, last_name: str | None) -> None:
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO users(chat_id,username,first_name,last_name,last_seen)
               VALUES(?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   username=excluded.username,
                   first_name=excluded.first_name,
                   last_name=excluded.last_name,
                   last_seen=excluded.last_seen""",
            (chat_id, username, first_name, last_name, time.time()),
        )

def get_user(chat_id: int) -> sqlite3.Row | None:
    with db_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()

def get_all_users(include_banned: bool = False) -> list[sqlite3.Row]:
    with db_conn() as conn:
        if include_banned:
            return conn.execute("SELECT * FROM users").fetchall()
        return conn.execute("SELECT * FROM users WHERE is_banned=0").fetchall()

def set_user_network(chat_id: int, network: str) -> None:
    """network: 'testnet' | 'real'"""
    with db_conn() as conn:
        conn.execute("UPDATE users SET network=? WHERE chat_id=?", (network, chat_id))

def set_user_account_type(chat_id: int, account_type: str) -> None:
    """account_type: 'spot' | 'futures'"""
    with db_conn() as conn:
        conn.execute("UPDATE users SET account_type=? WHERE chat_id=?", (account_type, chat_id))

def ban_user(chat_id: int, banned: bool = True) -> None:
    with db_conn() as conn:
        conn.execute("UPDATE users SET is_banned=? WHERE chat_id=?", (int(banned), chat_id))

def count_users() -> int:
    with db_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=0").fetchone()[0]

# ── API key helpers ────────────────────────────────────────────────────────────

def save_api_keys(chat_id: int, enc_api_key: bytes, enc_secret_key: bytes) -> None:
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO api_keys(chat_id,enc_api_key,enc_secret_key,updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   enc_api_key=excluded.enc_api_key,
                   enc_secret_key=excluded.enc_secret_key,
                   is_valid=0,
                   updated_at=excluded.updated_at""",
            (chat_id, enc_api_key, enc_secret_key, time.time()),
        )

def mark_api_valid(chat_id: int, valid: bool) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE api_keys SET is_valid=?, validated_at=? WHERE chat_id=?",
            (int(valid), time.time(), chat_id),
        )

def get_api_keys(chat_id: int) -> sqlite3.Row | None:
    with db_conn() as conn:
        return conn.execute("SELECT * FROM api_keys WHERE chat_id=?", (chat_id,)).fetchone()

def delete_api_keys(chat_id: int) -> None:
    with db_conn() as conn:
        conn.execute("DELETE FROM api_keys WHERE chat_id=?", (chat_id,))

# ── Trade helpers ──────────────────────────────────────────────────────────────

def insert_trade(
    chat_id: int,
    symbol: str,
    side: str,
    account_type: str,
    entry_price: float,
    quantity: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    strategy: str | None = None,
    signal_score: float | None = None,
    signal_reason: str | None = None,
    timeframe: str | None = None,
    indicators_json: str | None = None,
    binance_order_id: str | None = None,
) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades(chat_id,binance_order_id,symbol,side,account_type,
               entry_price,quantity,stop_loss,take_profit,strategy,signal_score,
               signal_reason,timeframe,indicators_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (chat_id, binance_order_id, symbol, side, account_type,
             entry_price, quantity, stop_loss, take_profit,
             strategy, signal_score, signal_reason, timeframe, indicators_json),
        )
        return cur.lastrowid  # type: ignore[return-value]

def close_trade(trade_id: int, close_price: float, pnl: float) -> None:
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            """UPDATE trades SET close_price=?,pnl=?,status='closed',closed_at=?,
               duration_secs=(? - opened_at) WHERE id=?""",
            (close_price, pnl, now, now, trade_id),
        )

def get_open_trades(chat_id: int) -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM trades WHERE chat_id=? AND status='open' ORDER BY opened_at DESC",
            (chat_id,),
        ).fetchall()

def get_all_open_trades() -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY opened_at",
        ).fetchall()

def get_trade(trade_id: int) -> sqlite3.Row | None:
    with db_conn() as conn:
        return conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()

def update_trade_levels(trade_id: int, stop_loss: float | None = None, take_profit: float | None = None) -> None:
    with db_conn() as conn:
        if stop_loss is not None:
            conn.execute("UPDATE trades SET stop_loss=? WHERE id=?", (stop_loss, trade_id))
        if take_profit is not None:
            conn.execute("UPDATE trades SET take_profit=? WHERE id=?", (take_profit, trade_id))

# ── Statistics helpers ─────────────────────────────────────────────────────────

def get_user_stats(chat_id: int) -> dict:
    with db_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE chat_id=? AND status='closed'", (chat_id,)
        ).fetchone()[0]
        wins = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE chat_id=? AND status='closed' AND pnl>0", (chat_id,)
        ).fetchone()[0]
        losses = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE chat_id=? AND status='closed' AND pnl<=0", (chat_id,)
        ).fetchone()[0]
        total_pnl = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE chat_id=? AND status='closed'", (chat_id,)
        ).fetchone()[0]
        day_ago = time.time() - 86400
        week_ago = time.time() - 604800
        month_ago = time.time() - 2592000
        daily_pnl = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE chat_id=? AND status='closed' AND closed_at>=?",
            (chat_id, day_ago),
        ).fetchone()[0]
        weekly_pnl = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE chat_id=? AND status='closed' AND closed_at>=?",
            (chat_id, week_ago),
        ).fetchone()[0]
        monthly_pnl = conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE chat_id=? AND status='closed' AND closed_at>=?",
            (chat_id, month_ago),
        ).fetchone()[0]
        open_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE chat_id=? AND status='open'", (chat_id,)
        ).fetchone()[0]
        avg_rr_row = conn.execute(
            """SELECT AVG(ABS(close_price-entry_price)/NULLIF(ABS(stop_loss-entry_price),0))
               FROM trades WHERE chat_id=? AND status='closed' AND stop_loss IS NOT NULL""",
            (chat_id,),
        ).fetchone()[0]
        win_rate = (wins / total * 100) if total > 0 else 0.0
        return {
            "total": total, "wins": wins, "losses": losses,
            "win_rate": win_rate, "total_pnl": total_pnl,
            "daily_pnl": daily_pnl, "weekly_pnl": weekly_pnl,
            "monthly_pnl": monthly_pnl, "open_count": open_count,
            "avg_rr": avg_rr_row or 0.0,
        }

def get_global_stats() -> dict:
    with db_conn() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=0").fetchone()[0]
        trades = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
        open_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
        total_pnl = conn.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='closed'").fetchone()[0]
        signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        return {
            "users": users, "trades": trades,
            "open_trades": open_trades, "total_pnl": total_pnl, "signals": signals,
        }

# ── Signal helpers ─────────────────────────────────────────────────────────────

def insert_signal(
    symbol: str, direction: str, entry: float, stop_loss: float,
    take_profit: float, rr_ratio: float, score: float, reasons: str,
    timeframe: str, trend: str | None, volume_status: str | None,
    momentum_status: str | None, indicators_json: str | None,
) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO signals(symbol,direction,entry,stop_loss,take_profit,
               rr_ratio,score,reasons,timeframe,trend,volume_status,
               momentum_status,indicators_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol, direction, entry, stop_loss, take_profit, rr_ratio,
             score, reasons, timeframe, trend, volume_status,
             momentum_status, indicators_json),
        )
        return cur.lastrowid  # type: ignore[return-value]

def get_recent_signals(limit: int = 10) -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

# ── Learning helpers ───────────────────────────────────────────────────────────

def insert_learning_record(
    strategy: str, symbol: str, timeframe: str, indicators_json: str,
    signal_score: float | None, entry: float,
) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO learning_data(strategy,symbol,timeframe,indicators_json,signal_score,entry)
               VALUES(?,?,?,?,?,?)""",
            (strategy, symbol, timeframe, indicators_json, signal_score, entry),
        )
        return cur.lastrowid  # type: ignore[return-value]

def update_learning_outcome(record_id: int, exit_price: float, pnl_pct: float, won: int, duration_secs: float) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE learning_data SET exit_price=?,pnl_pct=?,won=?,duration_secs=? WHERE id=?",
            (exit_price, pnl_pct, won, duration_secs, record_id),
        )

def get_strategy_weights() -> dict[str, float]:
    with db_conn() as conn:
        rows = conn.execute("SELECT strategy, weight FROM strategy_weights").fetchall()
        return {r["strategy"]: r["weight"] for r in rows}

def update_strategy_weight(strategy: str, won: bool, pnl_pct: float) -> None:
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO strategy_weights(strategy,weight,win_count,loss_count,total_pnl)
               VALUES(?,1.0,?,?,?)
               ON CONFLICT(strategy) DO UPDATE SET
                   win_count  = win_count  + excluded.win_count,
                   loss_count = loss_count + excluded.loss_count,
                   total_pnl  = total_pnl  + excluded.total_pnl,
                   updated_at = unixepoch()""",
            (strategy, int(won), int(not won), pnl_pct),
        )
        # Recalculate weight: Wilson lower bound approximation
        row = conn.execute("SELECT win_count,loss_count FROM strategy_weights WHERE strategy=?", (strategy,)).fetchone()
        w, l = row["win_count"], row["loss_count"]
        n = w + l
        if n > 0:
            p_hat = w / n
            new_weight = max(0.1, round(p_hat * 2, 4))
        else:
            new_weight = 1.0
        conn.execute("UPDATE strategy_weights SET weight=? WHERE strategy=?", (new_weight, strategy))

# ── Log helpers ────────────────────────────────────────────────────────────────

def log_event(event: str, detail: str | None = None, level: str = "INFO", chat_id: int | None = None) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO logs(level,event,detail,chat_id) VALUES(?,?,?,?)",
            (level, event, detail, chat_id),
        )
        # Prune old logs to keep table small
        conn.execute(
            """DELETE FROM logs WHERE id NOT IN (
               SELECT id FROM logs ORDER BY created_at DESC LIMIT ?)""",
            (MAX_LOG_ROWS,),
        )

def get_recent_logs(limit: int = 50) -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

# ── Rate limit helpers ─────────────────────────────────────────────────────────

def check_rate_limit(chat_id: int, min_interval: float) -> bool:
    """Returns True if the user is allowed (not rate-limited)."""
    now = time.time()
    with db_conn() as conn:
        row = conn.execute("SELECT last_action FROM rate_limits WHERE chat_id=?", (chat_id,)).fetchone()
        if row and (now - row["last_action"]) < min_interval:
            return False
        conn.execute(
            "INSERT INTO rate_limits(chat_id,last_action) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET last_action=excluded.last_action",
            (chat_id, now),
        )
        return True

# ── Broadcast helpers ──────────────────────────────────────────────────────────

def create_broadcast(admin_chat_id: int, message_text: str | None, media_type: str | None, total_users: int) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO broadcast_history(admin_chat_id,message_text,media_type,total_users) VALUES(?,?,?,?)",
            (admin_chat_id, message_text, media_type, total_users),
        )
        return cur.lastrowid  # type: ignore[return-value]

def update_broadcast_progress(bc_id: int, success: int, fail: int, status: str = "running") -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE broadcast_history SET success_count=?,fail_count=?,status=? WHERE id=?",
            (success, fail, status, bc_id),
        )

def finish_broadcast(bc_id: int) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE broadcast_history SET status='done',finished_at=? WHERE id=?",
            (time.time(), bc_id),
        )
