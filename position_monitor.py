"""
Position Monitor — runs as a background asyncio task.
Continuously monitors all open trades, detects adverse conditions,
auto-closes on signal invalidation, locks profit, updates trailing stops.
"""

import asyncio
import json
import logging
import math
import time

import binance_client as bc
import database as db
import trade_manager as tm
from analysis import analyze
from config import MONITOR_POLL_SECONDS

logger = logging.getLogger(__name__)

_monitor_status: dict = {"running": False, "last_check": 0.0, "trades_monitored": 0}


def get_monitor_status() -> dict:
    return dict(_monitor_status)


# ── Condition checks ───────────────────────────────────────────────────────────

def _is_trend_reversed(ar, trade_side: str) -> bool:
    """Check if trend has reversed against the trade direction."""
    if trade_side == "BUY":
        return ar.trend in ("downtrend", "strong_downtrend")
    return ar.trend in ("uptrend", "strong_uptrend")


def _is_momentum_weak(ar, trade_side: str) -> bool:
    """Check for weak momentum aligned against trade direction."""
    rsi = ar.rsi_val
    if math.isnan(rsi):
        return False
    if trade_side == "BUY" and rsi < 35:
        return True
    if trade_side == "SELL" and rsi > 65:
        return True
    return False


def _is_volume_collapsed(ar) -> bool:
    """Volume collapse = current vol < 30% of average."""
    ratio = ar.volume_info.get("ratio", 1.0)
    return ratio < 0.3


def _is_structure_broken(ar, trade_side: str) -> bool:
    """Market structure moved against the trade."""
    ms = ar.market_structure
    if trade_side == "BUY" and ms.get("trend") == "downtrend":
        return True
    if trade_side == "SELL" and ms.get("trend") == "uptrend":
        return True
    return False


def _is_fake_breakout(ar, trade_side: str, entry_price: float, current_price: float) -> bool:
    """Detect price returning back through entry after initial move."""
    if trade_side == "BUY":
        return current_price < entry_price * 0.998 and ar.supertrend_dir == -1
    return current_price > entry_price * 1.002 and ar.supertrend_dir == 1


# ── TP / SL trigger ────────────────────────────────────────────────────────────

async def _check_tp_sl(trade: dict, current_price: float, chat_id: int) -> bool:
    """Returns True if trade was closed by TP or SL."""
    sl = trade["stop_loss"]
    tp = trade["take_profit"]
    side = trade["side"]
    if sl and (
        (side == "BUY" and current_price <= sl) or
        (side == "SELL" and current_price >= sl)
    ):
        result = await tm.close_trade(chat_id, trade["id"])
        db.log_event("Trade SL Hit", f"trade#{trade['id']} {trade['symbol']} price={current_price}", "INFO", chat_id)
        return True
    if tp and (
        (side == "BUY" and current_price >= tp) or
        (side == "SELL" and current_price <= tp)
    ):
        result = await tm.close_trade(chat_id, trade["id"])
        db.log_event("Trade TP Hit", f"trade#{trade['id']} {trade['symbol']} price={current_price}", "INFO", chat_id)
        return True
    return False


# ── Auto profit lock ───────────────────────────────────────────────────────────

def _should_lock_profit(trade: dict, current_price: float) -> float | None:
    """
    If trade is 1.5R in profit, return new SL = entry + 0.5R (protect profit).
    Returns new SL or None.
    """
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    if not sl:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    profit_r = (current_price - entry) / risk if trade["side"] == "BUY" else (entry - current_price) / risk
    if profit_r >= 1.5:
        # Move SL to entry + 0.5R profit
        new_sl = entry + risk * 0.5 if trade["side"] == "BUY" else entry - risk * 0.5
        if trade["side"] == "BUY" and new_sl > (trade["stop_loss"] or 0):
            return new_sl
        if trade["side"] == "SELL" and new_sl < (trade["stop_loss"] or float("inf")):
            return new_sl
    return None


# ── Main monitor loop ──────────────────────────────────────────────────────────

async def run_position_monitor(bot=None) -> None:
    """
    Continuously poll all open trades and take protective action.
    Optional `bot` parameter: if provided, sends Telegram alerts on auto-close.
    """
    _monitor_status["running"] = True
    db.log_event("Monitor Started", "Position monitor background task started", "INFO")
    logger.info("Position monitor started")

    while True:
        try:
            open_trades = db.get_all_open_trades()
            _monitor_status["last_check"] = time.time()
            _monitor_status["trades_monitored"] = len(open_trades)

            for trade in open_trades:
                try:
                    chat_id = trade["chat_id"]
                    symbol = trade["symbol"]
                    user = db.get_user(chat_id)
                    if not user:
                        continue
                    is_futures = user["account_type"] == "futures"
                    current_price = await bc.get_current_price(symbol, futures=is_futures)
                    if current_price <= 0:
                        continue

                    # Check TP/SL first
                    closed = await _check_tp_sl(trade, current_price, chat_id)
                    if closed:
                        if bot:
                            try:
                                await bot.send_message(
                                    chat_id,
                                    f"🔔 <b>Trade auto-closed</b>\n{symbol} @ {current_price:.6f}",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
                        continue

                    # Profit lock
                    new_sl = _should_lock_profit(dict(trade), current_price)
                    if new_sl:
                        db.update_trade_levels(trade["id"], stop_loss=new_sl)
                        logger.info("Profit locked trade#%d new_sl=%.6f", trade["id"], new_sl)

                    # Update trailing stops
                    await tm.update_trailing_stops(chat_id)

                    # Fetch quick 15m analysis to detect structural changes
                    from binance_client import get_klines
                    klines = await get_klines(symbol, "15m", limit=100, futures=is_futures)
                    if not klines:
                        continue
                    from analysis import analyze
                    ar = analyze(symbol, "15m", klines)
                    if not ar:
                        continue

                    reasons_to_close: list[str] = []
                    if _is_trend_reversed(ar, trade["side"]):
                        reasons_to_close.append("Trend reversed")
                    if _is_momentum_weak(ar, trade["side"]):
                        reasons_to_close.append("Momentum weak")
                    if _is_volume_collapsed(ar):
                        reasons_to_close.append("Volume collapsed")
                    if _is_structure_broken(ar, trade["side"]):
                        reasons_to_close.append("Market structure broken")
                    if _is_fake_breakout(ar, trade["side"], trade["entry_price"], current_price):
                        reasons_to_close.append("Fake breakout detected")

                    # Need at least 2 bearish conditions to auto-close
                    if len(reasons_to_close) >= 2:
                        result = await tm.close_trade(chat_id, trade["id"])
                        db.log_event(
                            "Trade Auto-Closed",
                            f"trade#{trade['id']} {symbol} reasons={reasons_to_close}",
                            "WARNING",
                            chat_id,
                        )
                        if bot and result.get("success"):
                            try:
                                await bot.send_message(
                                    chat_id,
                                    f"⚠️ <b>Auto-close triggered</b>\n"
                                    f"<b>{symbol}</b> @ {current_price:.6f}\n"
                                    f"📋 Reasons: {', '.join(reasons_to_close)}\n"
                                    f"{'🟢' if result['pnl'] > 0 else '🔴'} PnL: {result['pnl']:.4f} USDT",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass

                except Exception as exc:
                    logger.error("Monitor error for trade#%s: %s", trade.get("id"), exc)

            await asyncio.sleep(MONITOR_POLL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Position monitor cancelled")
            break
        except Exception as exc:
            logger.error("Position monitor loop error: %s", exc)
            await asyncio.sleep(15)

    _monitor_status["running"] = False
    logger.info("Position monitor stopped")
