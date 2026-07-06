"""
Main Telegram bot handlers — start menu, dashboard, trading, statistics.
Uses ConversationHandler for multi-step flows (API setup, order entry).
"""

import asyncio
import json
import logging
import time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.constants import ParseMode

import binance_client as bc
import database as db
import keyboards as kb
import security
import trade_manager as tm
import scanner as sc
import signals as sig
from config import ADMIN_CHAT_ID, USER_RATE_LIMIT_SECONDS

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
AWAIT_API_KEY, AWAIT_SECRET_KEY = range(2)
AWAIT_SYMBOL, AWAIT_QUANTITY, AWAIT_STOP_LOSS, AWAIT_TAKE_PROFIT = range(4, 8)
AWAIT_NEW_SL, AWAIT_NEW_TP = range(8, 10)
AWAIT_LEVERAGE_SYMBOL, AWAIT_LEVERAGE_VALUE = range(10, 12)
AWAIT_CLOSE_PRICE = range(12, 13)

# ── Helpers ────────────────────────────────────────────────────────────────────

async def _safe_edit(update: Update, text: str, markup: InlineKeyboardMarkup | None = None, parse_mode: str = ParseMode.HTML) -> None:
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
        else:
            await update.message.reply_html(text, reply_markup=markup)
    except Exception:
        try:
            if update.callback_query:
                await update.callback_query.message.reply_html(text, reply_markup=markup)
        except Exception:
            pass

def _rate_limited(chat_id: int) -> bool:
    return not db.check_rate_limit(chat_id, USER_RATE_LIMIT_SECONDS)

def _loading_text(msg: str) -> str:
    return f"⏳ <i>{msg}</i>"

# ── /start ─────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    db.log_event("User Joined", f"@{user.username}", "INFO", user.id)
    text = (
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        f"🤖 <b>Binance Trading Bot v1.0</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📡 AI-powered market scanner\n"
        f"🎯 High-confidence signals (≥90%)\n"
        f"⚡ Full Spot & Futures support\n"
        f"🧪 Testnet & Real account modes\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Select an option below to get started.</i>"
    )
    await update.message.reply_html(text, reply_markup=kb.main_menu(user.id))


async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    user = update.effective_user
    await _safe_edit(
        update,
        "🏠 <b>Main Menu</b>\n<i>Select an option:</i>",
        kb.main_menu(user.id),
    )

# ── Dashboard ──────────────────────────────────────────────────────────────────

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Loading dashboard...")
    chat_id = update.effective_user.id
    await _safe_edit(update, _loading_text("Fetching live account data..."))
    try:
        info = await bc.get_account_info(chat_id)
        stats = db.get_user_stats(chat_id)
        user = db.get_user(chat_id)
        pnl_emoji = "🟢" if info.get("unrealised_pnl", 0) >= 0 else "🔴"
        text = (
            f"🏠 <b>Live Dashboard</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>Mode:</b> {user['account_type'].upper()} | {user['network'].upper()}\n"
            f"💰 <b>Wallet Balance:</b> <code>{info['wallet_balance']:.2f} USDT</code>\n"
            f"💵 <b>Available:</b> <code>{info['available_balance']:.2f} USDT</code>\n"
        )
        if user["account_type"] == "futures":
            text += (
                f"📊 <b>Margin Balance:</b> <code>{info['margin_balance']:.2f} USDT</code>\n"
                f"🏦 <b>Equity:</b> <code>{info['equity']:.2f} USDT</code>\n"
                f"{pnl_emoji} <b>Unrealised PnL:</b> <code>{info['unrealised_pnl']:.4f} USDT</code>\n"
                f"⚙️ <b>Margin Mode:</b> {info.get('margin_mode','N/A').upper()}\n"
                f"📐 <b>Open Positions:</b> {len(info.get('open_positions', []))}\n"
            )
        text += (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Today's PnL:</b> {'🟢' if stats['daily_pnl']>=0 else '🔴'} <code>{stats['daily_pnl']:.4f} USDT</code>\n"
            f"📊 <b>Total PnL:</b> {'🟢' if stats['total_pnl']>=0 else '🔴'} <code>{stats['total_pnl']:.4f} USDT</code>\n"
            f"🎯 <b>Win Rate:</b> <code>{stats['win_rate']:.1f}%</code>\n"
            f"📋 <b>Open Trades:</b> {stats['open_count']}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>🔄 Updated: just now</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="dashboard")],
            [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
        ])
        await _safe_edit(update, text, markup)
    except Exception as exc:
        await _safe_edit(
            update,
            f"❌ <b>Dashboard Error</b>\n<code>{exc}</code>\n\n<i>Please connect your API keys first.</i>",
            kb.back_to_main(),
        )

# ── Binance Account ────────────────────────────────────────────────────────────

async def binance_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    chat_id = update.effective_user.id
    user = db.get_user(chat_id)
    keys = db.get_api_keys(chat_id)
    status = "✅ Connected" if keys and keys["is_valid"] else "❌ Not Connected"
    text = (
        f"🔑 <b>Binance Account</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📡 <b>Status:</b> {status}\n"
        f"💎 <b>Account Type:</b> {user['account_type'].upper()}\n"
        f"🌐 <b>Network:</b> {user['network'].upper()}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Choose an action below:</i>"
    )
    await _safe_edit(update, text, kb.binance_account_menu())


async def set_account_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    data = update.callback_query.data
    chat_id = update.effective_user.id
    account_type = "spot" if data == "set_spot" else "futures"
    db.set_user_account_type(chat_id, account_type)
    await _safe_edit(
        update,
        f"✅ Account type set to <b>{account_type.upper()}</b>.",
        kb.binance_account_menu(),
    )


async def set_network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    data = update.callback_query.data
    chat_id = update.effective_user.id
    network = "testnet" if data in ("set_testnet", "net_testnet") else "real"
    db.set_user_network(chat_id, network)
    label = "🧪 Testnet" if network == "testnet" else "🌍 Real Account"
    await _safe_edit(
        update,
        f"✅ Network set to <b>{label}</b>.",
        kb.binance_account_menu(),
    )


async def remove_api(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    chat_id = update.effective_user.id
    db.delete_api_keys(chat_id)
    await _safe_edit(update, "🗑 <b>API keys removed.</b>", kb.binance_account_menu())

# ── API key conversation ───────────────────────────────────────────────────────

async def connect_api_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await _safe_edit(
        update,
        "🔑 <b>API Key Setup</b>\n\n"
        "Step 1️⃣: Please send your <b>Binance API Key</b>.\n\n"
        "<i>⚠️ Keys are stored with AES-256 encryption. Never share your keys.</i>",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="binance_account")]]),
    )
    return AWAIT_API_KEY


async def receive_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    api_key = update.message.text.strip()
    await update.message.delete()
    if len(api_key) < 20:
        await update.message.reply_html("❌ Invalid API Key. Please try again.")
        return AWAIT_API_KEY
    context.user_data["api_key"] = api_key
    await update.message.reply_html(
        "Step 2️⃣: Please send your <b>Binance Secret Key</b>.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="binance_account")]]),
    )
    return AWAIT_SECRET_KEY


async def receive_secret_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    secret_key = update.message.text.strip()
    await update.message.delete()
    if len(secret_key) < 20:
        await update.message.reply_html("❌ Invalid Secret Key. Please try again.")
        return AWAIT_SECRET_KEY
    chat_id = update.effective_user.id
    api_key = context.user_data.pop("api_key", "")
    enc_api = security.encrypt(api_key)
    enc_secret = security.encrypt(secret_key)
    db.save_api_keys(chat_id, enc_api, enc_secret)
    msg = await update.message.reply_html("⏳ <i>Validating API keys against Binance...</i>")
    success, message = await bc.validate_api_keys(chat_id)
    status_text = (
        f"{'✅' if success else '❌'} <b>API Validation</b>\n\n{message}\n\n"
        f"<i>You can now use all trading features.</i>" if success else
        f"{'❌'} <b>API Validation Failed</b>\n\n{message}\n\n"
        f"<i>Please check your keys and network selection.</i>"
    )
    await msg.edit_text(status_text, parse_mode=ParseMode.HTML, reply_markup=kb.binance_account_menu())
    return ConversationHandler.END


async def api_conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("api_key", None)
    if update.callback_query:
        await update.callback_query.answer()
        await _safe_edit(update, "❌ <b>API setup cancelled.</b>", kb.binance_account_menu())
    return ConversationHandler.END

# ── Live Dashboard ─────────────────────────────────────────────────────────────

async def live_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Loading...")
    await dashboard(update, context)

# ── AI Scanner ─────────────────────────────────────────────────────────────────

async def ai_scanner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    status = sc.get_scanner_status()
    opps = status.get("opportunities", [])[:10]
    opp_text = ""
    for i, o in enumerate(opps, 1):
        change_emoji = "📈" if o["change_pct"] >= 0 else "📉"
        opp_text += (
            f"  {i}. <code>{o['symbol']}</code> "
            f"{change_emoji} {o['change_pct']:+.2f}% "
            f"Vol: {o['volume']/1e6:.1f}M\n"
        )
    if not opp_text:
        opp_text = "  <i>Scanning markets...</i>\n"
    text = (
        f"🤖 <b>AI Market Scanner</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📡 <b>Status:</b> {'🟢 Running' if status['running'] else '🔴 Stopped'}\n"
        f"⏱ <b>Interval:</b> {status['interval']}s\n"
        f"📊 <b>Pairs Scanned:</b> {status['pairs_scanned']}\n"
        f"⚠️ <b>Errors:</b> {status['errors']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Top Opportunities:</b>\n{opp_text}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Scanner auto-ranks by volume × momentum.</i>"
    )
    await _safe_edit(update, text, kb.scanner_menu())


async def scanner_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    data = update.callback_query.data
    if data == "scanner_fast":
        sc.set_scanner_interval(1)
        await _safe_edit(update, "⚡ Scanner set to <b>1 second</b> (fast mode).", kb.scanner_menu())
    else:
        sc.set_scanner_interval(10)
        await _safe_edit(update, "🐢 Scanner set to <b>10 seconds</b> (normal mode).", kb.scanner_menu())


async def scanner_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Fetching...")
    opps = await sc.get_top_opportunities(15)
    if not opps:
        await _safe_edit(update, "⏳ <i>Scanner is warming up, please wait...</i>", kb.scanner_menu())
        return
    lines = [f"🏆 <b>Top 15 Pairs by Opportunity Score</b>\n━━━━━━━━━━━━━━━━━━"]
    for i, o in enumerate(opps, 1):
        lines.append(
            f"<b>{i}.</b> <code>{o['symbol']}</code> "
            f"{'📈' if o['change_pct'] >= 0 else '📉'} {o['change_pct']:+.2f}% "
            f"| Vol: {o['volume']/1e6:.1f}M USDT "
            f"| Score: {o['score']:.0f}"
        )
    await _safe_edit(update, "\n".join(lines), kb.scanner_menu())

# ── AI Signals ─────────────────────────────────────────────────────────────────

async def ai_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    recent = db.get_recent_signals(1)
    if recent:
        s = recent[0]
        score_bar = "█" * int(s["score"] / 10) + "░" * (10 - int(s["score"] / 10))
        text = (
            f"🎯 <b>AI Signal Engine</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 Last Signal: <code>{s['symbol']}</code>\n"
            f"{'📈' if s['direction']=='BUY' else '📉'} <b>{s['direction']}</b> | Score: {s['score']:.1f}%\n"
            f"<code>[{score_bar}]</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>Min confidence: 90% | Target RR: 1:2 – 1:3</i>"
        )
    else:
        text = (
            f"🎯 <b>AI Signal Engine</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>No signals yet. Scanner must be running.</i>"
        )
    await _safe_edit(update, text, kb.signals_menu())


async def latest_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Loading signals...")
    signals_list = db.get_recent_signals(5)
    if not signals_list:
        await _safe_edit(update, "📭 <b>No signals found yet.</b>", kb.signals_menu())
        return
    lines = ["🎯 <b>Latest AI Signals</b>\n━━━━━━━━━━━━━━━━━━"]
    for s in signals_list:
        d_emoji = "📈" if s["direction"] == "BUY" else "📉"
        lines.append(
            f"{d_emoji} <b>{s['symbol']}</b> {s['direction']}\n"
            f"   Entry: <code>{s['entry']:.6f}</code> | SL: <code>{s['stop_loss']:.6f}</code>\n"
            f"   TP: <code>{s['take_profit']:.6f}</code> | RR: 1:{s['rr_ratio']} | Score: {s['score']:.1f}%"
        )
    await _safe_edit(update, "\n".join(lines), kb.signals_menu())

# ── Trade Manager ──────────────────────────────────────────────────────────────

async def trade_manager_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    chat_id = update.effective_user.id
    open_trades = db.get_open_trades(chat_id)
    trade_ids = [t["id"] for t in open_trades]
    lines = ["📉 <b>Trade Manager</b>\n━━━━━━━━━━━━━━━━━━"]
    if open_trades:
        for t in open_trades[:5]:
            pnl_label = ""
            lines.append(
                f"• <code>{t['symbol']}</code> {t['side']} "
                f"@ {t['entry_price']:.6f} | Qty: {t['quantity']}"
            )
    else:
        lines.append("<i>No open trades.</i>")
    await _safe_edit(update, "\n".join(lines), kb.trade_manager_menu(trade_ids))


async def manage_specific_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    data = update.callback_query.data
    trade_id = int(data.split("_")[-1])
    trade = db.get_trade(trade_id)
    if not trade:
        await _safe_edit(update, "❌ Trade not found.", kb.back_to_main())
        return
    chat_id = update.effective_user.id
    user = db.get_user(chat_id)
    current_price = await bc.get_current_price(trade["symbol"], futures=(user["account_type"] == "futures"))
    pnl_unrealised = (current_price - trade["entry_price"]) * trade["quantity"]
    if trade["side"] == "SELL":
        pnl_unrealised = -pnl_unrealised
    pnl_emoji = "🟢" if pnl_unrealised >= 0 else "🔴"
    text = (
        f"📋 <b>Trade #{trade['id']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Symbol:</b> {trade['symbol']}\n"
        f"{'📈' if trade['side']=='BUY' else '📉'} <b>Side:</b> {trade['side']}\n"
        f"💰 <b>Entry:</b> <code>{trade['entry_price']:.6f}</code>\n"
        f"📡 <b>Current:</b> <code>{current_price:.6f}</code>\n"
        f"📦 <b>Quantity:</b> {trade['quantity']}\n"
        f"🛡 <b>SL:</b> <code>{trade['stop_loss'] or 'None'}</code>\n"
        f"🎯 <b>TP:</b> <code>{trade['take_profit'] or 'None'}</code>\n"
        f"{pnl_emoji} <b>Unrealised PnL:</b> <code>{pnl_unrealised:.4f} USDT</code>\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await _safe_edit(update, text, kb.manage_trade_menu(trade_id))


async def close_trade_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Closing...")
    trade_id = int(update.callback_query.data.split("_")[-1])
    chat_id = update.effective_user.id
    await _safe_edit(update, _loading_text("Closing trade on Binance..."))
    result = await tm.close_trade(chat_id, trade_id)
    await _safe_edit(update, result["message"], kb.trade_manager_menu([]))


async def close_partial_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Processing...")
    parts = update.callback_query.data.split("_")
    pct = int(parts[2])
    trade_id = int(parts[-1])
    chat_id = update.effective_user.id
    await _safe_edit(update, _loading_text(f"Closing {pct}% of position..."))
    result = await tm.close_partial(chat_id, trade_id, float(pct))
    await _safe_edit(update, result["message"], kb.manage_trade_menu(trade_id))


async def sl_to_be_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    trade_id = int(update.callback_query.data.split("_")[-1])
    chat_id = update.effective_user.id
    result = await tm.move_sl_to_breakeven(chat_id, trade_id)
    await _safe_edit(update, result["message"], kb.manage_trade_menu(trade_id))


async def trail_stop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    parts = update.callback_query.data.split("_")
    trail_pct = float(parts[1])
    trade_id = int(parts[-1])
    tm.activate_trailing_stop(trade_id, trail_pct)
    await _safe_edit(
        update,
        f"✅ Trailing stop activated at <b>{trail_pct}%</b>.",
        kb.manage_trade_menu(trade_id),
    )


async def reverse_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Reversing position...")
    trade_id = int(update.callback_query.data.split("_")[-1])
    chat_id = update.effective_user.id
    await _safe_edit(update, _loading_text("Reversing position..."))
    result = await tm.reverse_position(chat_id, trade_id)
    await _safe_edit(update, result["message"], kb.trade_manager_menu([]))


async def emergency_close_all_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("🚨 Emergency closing all...")
    chat_id = update.effective_user.id
    await _safe_edit(update, _loading_text("Emergency closing all positions..."))
    result = await tm.emergency_close_all(chat_id)
    await _safe_edit(update, result["message"], kb.trade_manager_menu([]))


# ── Open trade flow ────────────────────────────────────────────────────────────

async def open_trade_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await _safe_edit(
        update,
        "➕ <b>Open New Trade</b>\n\nEnter the <b>symbol</b> (e.g. BTCUSDT):",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="trade_manager")]]),
    )
    return AWAIT_SYMBOL


async def receive_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    symbol = update.message.text.strip().upper()
    context.user_data["trade_symbol"] = symbol
    await update.message.reply_html(
        f"💱 Symbol: <code>{symbol}</code>\n\nEnter <b>quantity</b>:",
    )
    return AWAIT_QUANTITY


async def receive_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        qty = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_html("❌ Invalid quantity. Enter a number.")
        return AWAIT_QUANTITY
    context.user_data["trade_qty"] = qty
    await update.message.reply_html(
        f"📦 Quantity: <code>{qty}</code>\n\nEnter <b>stop loss price</b> (or send <code>0</code> to skip):",
    )
    return AWAIT_STOP_LOSS


async def receive_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        sl = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_html("❌ Invalid price. Enter a number.")
        return AWAIT_STOP_LOSS
    context.user_data["trade_sl"] = sl if sl > 0 else None
    await update.message.reply_html(
        "Enter <b>take profit price</b> (or send <code>0</code> to skip):",
    )
    return AWAIT_TAKE_PROFIT


async def receive_take_profit_and_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        tp = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_html("❌ Invalid price. Enter a number.")
        return AWAIT_TAKE_PROFIT
    context.user_data["trade_tp"] = tp if tp > 0 else None
    symbol = context.user_data.get("trade_symbol", "?")
    qty = context.user_data.get("trade_qty", 0)
    sl = context.user_data.get("trade_sl")
    tp_val = context.user_data.get("trade_tp")
    text = (
        f"📋 <b>Order Preview</b>\n"
        f"Symbol: <code>{symbol}</code>\n"
        f"Qty: <code>{qty}</code>\n"
        f"SL: <code>{sl or 'N/A'}</code>\n"
        f"TP: <code>{tp_val or 'N/A'}</code>\n\n"
        f"Choose direction:"
    )
    await update.message.reply_html(text, reply_markup=kb.open_trade_menu())
    return ConversationHandler.END


async def trade_side_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Placing order...")
    side = update.callback_query.data.split("_")[-1]
    chat_id = update.effective_user.id
    symbol = context.user_data.pop("trade_symbol", "BTCUSDT")
    qty = context.user_data.pop("trade_qty", 0.0)
    sl = context.user_data.pop("trade_sl", None)
    tp = context.user_data.pop("trade_tp", None)
    await _safe_edit(update, _loading_text(f"Placing {side} order on Binance..."))
    result = await tm.open_trade(
        chat_id=chat_id, symbol=symbol, side=side,
        quantity=qty, stop_loss=sl, take_profit=tp,
    )
    await _safe_edit(update, result["message"], kb.trade_manager_menu([]))


async def trade_conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("trade_symbol", None)
    if update.callback_query:
        await update.callback_query.answer()
        await _safe_edit(update, "❌ Trade cancelled.", kb.trade_manager_menu([]))
    return ConversationHandler.END

# ── Statistics ─────────────────────────────────────────────────────────────────

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Loading stats...")
    chat_id = update.effective_user.id
    s = db.get_user_stats(chat_id)
    text = (
        f"📊 <b>My Statistics</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Total Trades:</b> {s['total']}\n"
        f"🏆 <b>Wins:</b> {s['wins']} | Losses: {s['losses']}\n"
        f"📈 <b>Win Rate:</b> <code>{s['win_rate']:.1f}%</code>\n"
        f"📐 <b>Avg R:R:</b> 1:{s['avg_rr']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Today's PnL:</b> {'🟢' if s['daily_pnl']>=0 else '🔴'} <code>{s['daily_pnl']:.4f} USDT</code>\n"
        f"📅 <b>Weekly PnL:</b> {'🟢' if s['weekly_pnl']>=0 else '🔴'} <code>{s['weekly_pnl']:.4f} USDT</code>\n"
        f"🗓 <b>Monthly PnL:</b> {'🟢' if s['monthly_pnl']>=0 else '🔴'} <code>{s['monthly_pnl']:.4f} USDT</code>\n"
        f"💎 <b>Total PnL:</b> {'🟢' if s['total_pnl']>=0 else '🔴'} <code>{s['total_pnl']:.4f} USDT</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Open Positions:</b> {s['open_count']}"
    )
    await _safe_edit(
        update, text,
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="my_stats")],
            [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
        ]),
    )

# ── Spot/Futures trading menus ─────────────────────────────────────────────────

async def spot_trading(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _safe_edit(
        update,
        "📈 <b>Spot Trading</b>\n<i>Manage your spot orders:</i>",
        kb.spot_trading_menu(),
    )


async def futures_trading(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _safe_edit(
        update,
        "📊 <b>Futures Trading</b>\n<i>Manage your USDT-M futures:</i>",
        kb.futures_trading_menu(),
    )


async def futures_positions_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Loading...")
    chat_id = update.effective_user.id
    try:
        info = await bc.get_account_info(chat_id)
        positions = info.get("open_positions", [])
        if not positions:
            await _safe_edit(update, "📊 <b>No open futures positions.</b>", kb.futures_trading_menu())
            return
        lines = ["📊 <b>Open Positions</b>\n━━━━━━━━━━━━━━━━━━"]
        for p in positions[:10]:
            pnl = float(p.get("unrealizedProfit", 0))
            lines.append(
                f"• <code>{p['symbol']}</code>\n"
                f"  Amt: {p['positionAmt']} | Entry: {p['entryPrice']}\n"
                f"  PnL: {'🟢' if pnl>=0 else '🔴'} <code>{pnl:.4f}</code>"
            )
        await _safe_edit(update, "\n".join(lines), kb.futures_trading_menu())
    except Exception as exc:
        await _safe_edit(update, f"❌ Error: {exc}", kb.futures_trading_menu())


async def funding_fees_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Loading...")
    chat_id = update.effective_user.id
    try:
        fees = await bc.get_income_history(chat_id, "FUNDING_FEE", 20)
        if not fees:
            await _safe_edit(update, "💸 <b>No funding fee records.</b>", kb.futures_trading_menu())
            return
        total = sum(float(f.get("income", 0)) for f in fees)
        lines = [f"💸 <b>Recent Funding Fees</b>\nTotal: <code>{total:.4f} USDT</code>\n━━━━━━━━━━━━━━━━━━"]
        for f in fees[:10]:
            inc = float(f.get("income", 0))
            lines.append(f"• {f.get('symbol','?')} {'🟢' if inc>=0 else '🔴'} <code>{inc:.6f}</code>")
        await _safe_edit(update, "\n".join(lines), kb.futures_trading_menu())
    except Exception as exc:
        await _safe_edit(update, f"❌ Error: {exc}", kb.futures_trading_menu())

# ── Settings ───────────────────────────────────────────────────────────────────

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    chat_id = update.effective_user.id
    user = db.get_user(chat_id)
    text = (
        f"⚙️ <b>Settings</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 Account Type: <b>{user['account_type'].upper()}</b>\n"
        f"🌐 Network: <b>{user['network'].upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await _safe_edit(update, text, kb.settings_menu())

# ── Help ───────────────────────────────────────────────────────────────────────

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    text = (
        "❓ <b>Help & Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<b>Getting Started:</b>\n"
        "1️⃣ Go to <b>Binance Account</b> and select Testnet or Real.\n"
        "2️⃣ Connect your API keys.\n"
        "3️⃣ Switch to Spot or Futures mode.\n"
        "4️⃣ Use <b>AI Scanner</b> to find opportunities.\n"
        "5️⃣ Use <b>AI Signals</b> to get high-confidence trades.\n"
        "6️⃣ Manage positions in <b>Trade Manager</b>.\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<b>Signals:</b> Only signals with ≥90% confidence are sent.\n"
        "<b>Risk:</b> All trades have SL and TP levels.\n"
        "<b>Security:</b> API keys encrypted with AES-256.\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>This bot is for educational purposes. Always trade responsibly.</i>"
    )
    await _safe_edit(update, text, kb.back_to_main())

# ── Conversation Handlers ──────────────────────────────────────────────────────

def api_key_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(connect_api_start, pattern="^connect_api$")],
        states={
            AWAIT_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_key)],
            AWAIT_SECRET_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_secret_key)],
        },
        fallbacks=[
            CallbackQueryHandler(api_conv_cancel, pattern="^binance_account$"),
            CommandHandler("start", start),
        ],
        per_user=True,
    )


def open_trade_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(open_trade_start, pattern="^open_trade$")],
        states={
            AWAIT_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_symbol)],
            AWAIT_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_quantity)],
            AWAIT_STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_stop_loss)],
            AWAIT_TAKE_PROFIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_take_profit_and_side)],
        },
        fallbacks=[
            CallbackQueryHandler(trade_conv_cancel, pattern="^trade_manager$"),
            CommandHandler("start", start),
        ],
        per_user=True,
    )
