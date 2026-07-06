"""
Trade Manager — executes all trade operations directly on Binance.
Supports: Open, Close, Partial Close, Trailing Stop, SL to Breakeven,
Update TP/SL, Reverse, Manual Close, Emergency Close.
"""

import asyncio
import json
import logging
import time
from typing import Any

import binance_client as bc
import database as db
import adaptive_learning as al

logger = logging.getLogger(__name__)


# ── Open trade ─────────────────────────────────────────────────────────────────

async def open_trade(
    chat_id: int,
    symbol: str,
    side: str,
    quantity: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    strategy: str | None = None,
    signal_score: float | None = None,
    signal_reason: str | None = None,
    timeframe: str | None = None,
    indicators: dict | None = None,
) -> dict:
    """
    Place a market order and record the trade in the database.
    Returns dict with keys: success, trade_id, order, message.
    """
    try:
        user = db.get_user(chat_id)
        order = await bc.place_market_order(chat_id, symbol, side, quantity)
        fill_price = float(order.get("avgPrice") or order.get("fills", [{}])[0].get("price", 0))
        if fill_price == 0:
            # Fallback: fetch current price
            fill_price = await bc.get_current_price(symbol, futures=(user["account_type"] == "futures"))

        indicators_json = json.dumps(indicators) if indicators else None
        trade_id = db.insert_trade(
            chat_id=chat_id,
            symbol=symbol,
            side=side,
            account_type=user["account_type"],
            entry_price=fill_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
            signal_score=signal_score,
            signal_reason=signal_reason,
            timeframe=timeframe,
            indicators_json=indicators_json,
            binance_order_id=str(order.get("orderId", "")),
        )

        # Place SL order on exchange if provided
        if stop_loss:
            try:
                await bc.place_stop_loss_order(chat_id, symbol, side, quantity, stop_loss)
            except Exception as exc:
                logger.warning("SL placement failed: %s", exc)

        # Record in learning system
        if indicators:
            al.record_trade_open(symbol, timeframe or "1h", indicators, signal_score, fill_price)

        db.log_event("Trade Opened", f"{side} {quantity} {symbol} @ {fill_price}", "INFO", chat_id)
        return {
            "success": True,
            "trade_id": trade_id,
            "order": order,
            "fill_price": fill_price,
            "message": f"✅ {side} {quantity} {symbol} @ {fill_price:.6f}",
        }
    except Exception as exc:
        logger.error("open_trade error: %s", exc)
        return {"success": False, "message": f"❌ Order failed: {exc}"}


# ── Close trade ────────────────────────────────────────────────────────────────

async def close_trade(chat_id: int, trade_id: int) -> dict:
    """Close an open trade at market price."""
    try:
        trade = db.get_trade(trade_id)
        if not trade or trade["status"] != "open":
            return {"success": False, "message": "❌ Trade not found or already closed."}

        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        order = await bc.place_market_order(
            chat_id, trade["symbol"], close_side, trade["quantity"], reduce_only=True
        )
        close_price = float(order.get("avgPrice") or 0)
        if close_price == 0:
            user = db.get_user(chat_id)
            close_price = await bc.get_current_price(
                trade["symbol"], futures=(user["account_type"] == "futures")
            )

        pnl = (close_price - trade["entry_price"]) * trade["quantity"]
        if trade["side"] == "SELL":
            pnl = -pnl
        db.close_trade(trade_id, close_price, pnl)

        # Update learning
        duration = time.time() - trade["opened_at"]
        indicators = json.loads(trade["indicators_json"]) if trade["indicators_json"] else None
        al.record_trade_close(trade_id, trade["entry_price"], close_price, duration, indicators)

        db.log_event("Trade Closed", f"trade#{trade_id} {trade['symbol']} pnl={pnl:.4f}", "INFO", chat_id)
        return {
            "success": True,
            "close_price": close_price,
            "pnl": pnl,
            "message": f"✅ Closed {trade['symbol']} @ {close_price:.6f}\nPnL: {'🟢' if pnl > 0 else '🔴'} {pnl:.4f} USDT",
        }
    except Exception as exc:
        logger.error("close_trade error: %s", exc)
        return {"success": False, "message": f"❌ Close failed: {exc}"}


# ── Partial close ──────────────────────────────────────────────────────────────

async def close_partial(chat_id: int, trade_id: int, close_pct: float) -> dict:
    """Close a percentage (0–100) of a position."""
    try:
        trade = db.get_trade(trade_id)
        if not trade or trade["status"] != "open":
            return {"success": False, "message": "❌ Trade not found or already closed."}
        close_qty = round(trade["quantity"] * close_pct / 100, 8)
        if close_qty <= 0:
            return {"success": False, "message": "❌ Quantity too small."}
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        order = await bc.place_market_order(
            chat_id, trade["symbol"], close_side, close_qty, reduce_only=True
        )
        close_price = float(order.get("avgPrice") or 0)
        if close_price == 0:
            user = db.get_user(chat_id)
            close_price = await bc.get_current_price(trade["symbol"])
        partial_pnl = (close_price - trade["entry_price"]) * close_qty
        if trade["side"] == "SELL":
            partial_pnl = -partial_pnl
        db.log_event("Trade Partial Close", f"trade#{trade_id} {close_pct}% pnl={partial_pnl:.4f}", "INFO", chat_id)
        return {
            "success": True,
            "close_price": close_price,
            "pnl": partial_pnl,
            "message": f"✅ Closed {close_pct}% of {trade['symbol']} @ {close_price:.6f}\nPartial PnL: {'🟢' if partial_pnl > 0 else '🔴'} {partial_pnl:.4f}",
        }
    except Exception as exc:
        return {"success": False, "message": f"❌ Partial close failed: {exc}"}


# ── Move SL to breakeven ───────────────────────────────────────────────────────

async def move_sl_to_breakeven(chat_id: int, trade_id: int) -> dict:
    """Move stop loss to entry price (breakeven)."""
    trade = db.get_trade(trade_id)
    if not trade or trade["status"] != "open":
        return {"success": False, "message": "❌ Trade not found."}
    db.update_trade_levels(trade_id, stop_loss=trade["entry_price"])
    try:
        await bc.place_stop_loss_order(
            chat_id, trade["symbol"], trade["side"], trade["quantity"], trade["entry_price"]
        )
    except Exception as exc:
        logger.warning("BE SL exchange placement failed: %s", exc)
    return {"success": True, "message": f"✅ SL moved to breakeven @ {trade['entry_price']:.6f}"}


# ── Update SL / TP ─────────────────────────────────────────────────────────────

async def update_stop_loss(chat_id: int, trade_id: int, new_sl: float) -> dict:
    trade = db.get_trade(trade_id)
    if not trade or trade["status"] != "open":
        return {"success": False, "message": "❌ Trade not found."}
    db.update_trade_levels(trade_id, stop_loss=new_sl)
    return {"success": True, "message": f"✅ Stop Loss updated to {new_sl:.6f}"}


async def update_take_profit(chat_id: int, trade_id: int, new_tp: float) -> dict:
    trade = db.get_trade(trade_id)
    if not trade or trade["status"] != "open":
        return {"success": False, "message": "❌ Trade not found."}
    db.update_trade_levels(trade_id, take_profit=new_tp)
    return {"success": True, "message": f"✅ Take Profit updated to {new_tp:.6f}"}


# ── Trailing stop ──────────────────────────────────────────────────────────────

# Trailing stop state: {trade_id: {"trail_pct": float, "best_price": float}}
_trailing_state: dict[int, dict] = {}


def activate_trailing_stop(trade_id: int, trail_pct: float) -> None:
    trade = db.get_trade(trade_id)
    if trade and trade["status"] == "open":
        _trailing_state[trade_id] = {
            "trail_pct": trail_pct,
            "best_price": trade["entry_price"],
        }
        logger.info("Trailing stop activated: trade#%d trail=%.2f%%", trade_id, trail_pct)


async def update_trailing_stops(chat_id: int) -> None:
    """Called by position monitor to update trailing stops."""
    for trade_id, state in list(_trailing_state.items()):
        try:
            trade = db.get_trade(trade_id)
            if not trade or trade["status"] != "open":
                del _trailing_state[trade_id]
                continue
            current_price = await bc.get_current_price(trade["symbol"])
            trail_pct = state["trail_pct"] / 100
            if trade["side"] == "BUY":
                if current_price > state["best_price"]:
                    _trailing_state[trade_id]["best_price"] = current_price
                    new_sl = current_price * (1 - trail_pct)
                    db.update_trade_levels(trade_id, stop_loss=new_sl)
                elif trade["stop_loss"] and current_price <= trade["stop_loss"]:
                    await close_trade(chat_id, trade_id)
                    del _trailing_state[trade_id]
            else:
                if current_price < state["best_price"]:
                    _trailing_state[trade_id]["best_price"] = current_price
                    new_sl = current_price * (1 + trail_pct)
                    db.update_trade_levels(trade_id, stop_loss=new_sl)
                elif trade["stop_loss"] and current_price >= trade["stop_loss"]:
                    await close_trade(chat_id, trade_id)
                    del _trailing_state[trade_id]
        except Exception as exc:
            logger.error("Trailing stop update error: %s", exc)


# ── Reverse position ───────────────────────────────────────────────────────────

async def reverse_position(chat_id: int, trade_id: int) -> dict:
    """Close current position and open the opposite side."""
    trade = db.get_trade(trade_id)
    if not trade or trade["status"] != "open":
        return {"success": False, "message": "❌ Trade not found."}
    close_result = await close_trade(chat_id, trade_id)
    if not close_result["success"]:
        return close_result
    new_side = "SELL" if trade["side"] == "BUY" else "BUY"
    open_result = await open_trade(
        chat_id=chat_id, symbol=trade["symbol"], side=new_side,
        quantity=trade["quantity"], strategy="reversal",
    )
    if open_result["success"]:
        return {"success": True, "message": f"✅ Position reversed to {new_side} {trade['symbol']}"}
    return {"success": False, "message": f"❌ Reversal close OK but re-open failed: {open_result['message']}"}


# ── Emergency close all ────────────────────────────────────────────────────────

async def emergency_close_all(chat_id: int) -> dict:
    """Close all open trades for a user immediately."""
    open_trades = db.get_open_trades(chat_id)
    if not open_trades:
        return {"success": True, "message": "✅ No open trades to close."}
    results = await asyncio.gather(
        *[close_trade(chat_id, t["id"]) for t in open_trades], return_exceptions=True
    )
    success_count = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
    return {
        "success": True,
        "message": f"🚨 Emergency close: {success_count}/{len(open_trades)} trades closed.",
    }
