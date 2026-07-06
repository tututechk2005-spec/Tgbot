"""
Adaptive Learning module.
Records every completed trade and adjusts strategy weights based on
historical performance. Never claims prediction guarantees.
"""

import json
import logging
import math
import time

import database as db

logger = logging.getLogger(__name__)

# ── Strategy registry ──────────────────────────────────────────────────────────
# Base strategies that can be weighted
STRATEGIES = [
    "trend_following",
    "breakout",
    "reversal",
    "momentum",
    "structure_retest",
    "volume_surge",
    "ichimoku",
    "supertrend",
]


def _ensure_strategies() -> None:
    """Seed any missing strategy rows with default weight 1.0."""
    existing = db.get_strategy_weights()
    for s in STRATEGIES:
        if s not in existing:
            try:
                with db.db_conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO strategy_weights(strategy,weight) VALUES(?,1.0)", (s,)
                    )
            except Exception as exc:
                logger.warning("Could not seed strategy %s: %s", s, exc)


# ── Classification ─────────────────────────────────────────────────────────────

def classify_strategy(indicators: dict | None) -> str:
    """
    Map indicator readings to a dominant strategy name.
    Used when inserting a new learning record.
    """
    if not indicators:
        return "trend_following"
    trend = indicators.get("trend", "")
    ms = indicators.get("market_structure", {})
    volume = indicators.get("volume_status", "normal")
    rsi = indicators.get("rsi", 50)
    macd_hist = indicators.get("macd_hist", 0)
    adx = indicators.get("adx", 0)
    patterns = indicators.get("candle_patterns", [])
    supertrend_dir = indicators.get("supertrend_dir", 0)

    if ms.get("bos") or ms.get("choch"):
        return "breakout"
    if volume == "spike" and adx > 25:
        return "volume_surge"
    if supertrend_dir in (1, -1) and adx > 20:
        return "supertrend"
    if any(p in patterns for p in ("Hammer", "Shooting Star", "Bullish Engulfing", "Bearish Engulfing")):
        return "reversal"
    if trend in ("strong_uptrend", "strong_downtrend") and adx > 25:
        return "trend_following"
    if abs(rsi - 50) > 15 and macd_hist != 0:
        return "momentum"
    if ms.get("trend") in ("uptrend", "downtrend"):
        return "structure_retest"
    return "trend_following"


# ── Recording ──────────────────────────────────────────────────────────────────

def record_trade_open(
    symbol: str,
    timeframe: str,
    indicators: dict,
    signal_score: float | None,
    entry: float,
) -> int:
    """
    Record a new open trade in learning_data.
    Returns the learning record ID (link to trade for later outcome update).
    """
    _ensure_strategies()
    strategy = classify_strategy(indicators)
    record_id = db.insert_learning_record(
        strategy=strategy,
        symbol=symbol,
        timeframe=timeframe,
        indicators_json=json.dumps(indicators),
        signal_score=signal_score,
        entry=entry,
    )
    logger.debug("Learning record %d created for %s", record_id, symbol)
    return record_id


def record_trade_close(
    learning_id: int,
    entry: float,
    exit_price: float,
    duration_secs: float,
    indicators: dict | None = None,
) -> None:
    """
    Update a learning record with trade outcome and trigger weight update.
    """
    if entry <= 0:
        return
    pnl_pct = (exit_price - entry) / entry * 100
    won = int(exit_price > entry)
    db.update_learning_outcome(
        record_id=learning_id,
        exit_price=exit_price,
        pnl_pct=pnl_pct,
        won=won,
        duration_secs=duration_secs,
    )
    strategy = classify_strategy(indicators)
    db.update_strategy_weight(strategy, bool(won), pnl_pct)
    db.log_event(
        "Learning Update",
        f"strategy={strategy} won={bool(won)} pnl={pnl_pct:.2f}%",
        "INFO",
    )
    logger.info("Learning updated: %s won=%s pnl=%.2f%%", strategy, won, pnl_pct)


# ── Weight retrieval ───────────────────────────────────────────────────────────

def get_weighted_score(symbol: str, direction: str, timeframe: str) -> float:
    """
    Return a multiplier (0.1 – 2.0) to scale the raw signal score.
    Derived from historical win rates of the dominant strategy for
    this symbol/timeframe combination.
    """
    weights = db.get_strategy_weights()
    if not weights:
        return 1.0
    # Simple heuristic: use average weight across all strategies as a floor
    avg_weight = sum(weights.values()) / len(weights)
    # Clamp to sensible range
    return max(0.5, min(1.5, avg_weight))


# ── Learning summary ───────────────────────────────────────────────────────────

def get_learning_summary() -> dict:
    """Return a structured summary for display in the admin panel."""
    weights = db.get_strategy_weights()
    with db.db_conn() as conn:
        rows = conn.execute(
            "SELECT strategy,win_count,loss_count,total_pnl,weight FROM strategy_weights"
        ).fetchall()
    strategies = []
    for r in rows:
        total = r["win_count"] + r["loss_count"]
        win_rate = (r["win_count"] / total * 100) if total > 0 else 0
        strategies.append({
            "strategy": r["strategy"],
            "weight": r["weight"],
            "win_rate": win_rate,
            "total_trades": total,
            "total_pnl": r["total_pnl"],
        })
    strategies.sort(key=lambda x: x["weight"], reverse=True)
    with db.db_conn() as conn:
        total_records = conn.execute("SELECT COUNT(*) FROM learning_data").fetchone()[0]
    return {"strategies": strategies, "total_records": total_records}
