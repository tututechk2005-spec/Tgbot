"""
Entry point for the Telegram Binance Trading Bot.
Initialises the database, sets up all handlers, and starts the bot
along with background tasks (scanner, position monitor).
"""

import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

import database as db
import config
import bot_handlers as bh
import admin_handlers as ah
import broadcast as bc
from broadcast import AWAIT_BC_CONTENT
import scanner as scanner_module
import position_monitor as pm
import signals

# ── Logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── Signal callback: relay high-confidence signals to users ────────────────────

async def _on_signal(opportunity: dict) -> None:
    """Called by the scanner for each top opportunity."""
    signal = await signals.generate_signal(opportunity)
    if signal is None:
        return
    # Broadcast to all users who have valid API keys and are not banned
    users = db.get_all_users(include_banned=False)
    msg_text = signals.format_signal_message(signal)
    for user in users:
        keys = db.get_api_keys(user["chat_id"])
        if keys and keys["is_valid"]:
            try:
                await _app.bot.send_message(
                    user["chat_id"],
                    msg_text,
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.debug("Signal delivery failed to %s: %s", user["chat_id"], exc)

_app: Application = None  # type: ignore[assignment]


# ── Handler registration ────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", bh.start))

    # ── API Key conversation ──────────────────────────────────────────────────
    app.add_handler(bh.api_key_conversation())

    # ── Open trade conversation ───────────────────────────────────────────────
    app.add_handler(bh.open_trade_conversation())

    # ── Broadcast conversation ────────────────────────────────────────────────
    from telegram.ext import ConversationHandler, MessageHandler, filters as tg_filters
    bc_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(bc.broadcast_text, pattern="^bc_text$"),
            CallbackQueryHandler(bc.broadcast_photo, pattern="^bc_photo$"),
        ],
        states={
            AWAIT_BC_CONTENT: [
                MessageHandler(
                    tg_filters.TEXT | tg_filters.PHOTO | tg_filters.VIDEO |
                    tg_filters.ANIMATION | tg_filters.VOICE | tg_filters.AUDIO |
                    tg_filters.Sticker.ALL | tg_filters.Document.ALL,
                    bc.receive_broadcast_content,
                )
            ],
        },
        fallbacks=[
            CallbackQueryHandler(bc.bc_cancel, pattern="^admin_broadcast$"),
            CommandHandler("start", bh.start),
        ],
        per_user=True,
    )
    app.add_handler(bc_conv)

    # ── Main menu & navigation ────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(bh.main_menu_cb, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(bh.dashboard, pattern="^dashboard$"))
    app.add_handler(CallbackQueryHandler(bh.live_dashboard, pattern="^live_dashboard$"))

    # ── Binance account ───────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(bh.binance_account, pattern="^binance_account$"))
    app.add_handler(CallbackQueryHandler(bh.remove_api, pattern="^remove_api$"))
    app.add_handler(CallbackQueryHandler(bh.set_account_type, pattern="^set_spot$"))
    app.add_handler(CallbackQueryHandler(bh.set_account_type, pattern="^set_futures$"))
    app.add_handler(CallbackQueryHandler(bh.set_network, pattern="^set_testnet$"))
    app.add_handler(CallbackQueryHandler(bh.set_network, pattern="^set_real$"))
    app.add_handler(CallbackQueryHandler(bh.set_network, pattern="^net_testnet$"))
    app.add_handler(CallbackQueryHandler(bh.set_network, pattern="^net_real$"))

    # ── Scanner ───────────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(bh.ai_scanner, pattern="^ai_scanner$"))
    app.add_handler(CallbackQueryHandler(bh.scanner_set_interval, pattern="^scanner_fast$"))
    app.add_handler(CallbackQueryHandler(bh.scanner_set_interval, pattern="^scanner_slow$"))
    app.add_handler(CallbackQueryHandler(bh.scanner_top, pattern="^scanner_top$"))

    # ── Signals ───────────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(bh.ai_signals, pattern="^ai_signals$"))
    app.add_handler(CallbackQueryHandler(bh.latest_signals, pattern="^latest_signals$"))
    app.add_handler(CallbackQueryHandler(bh.latest_signals, pattern="^signal_history$"))

    # ── Spot / Futures trading ────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(bh.spot_trading, pattern="^spot_trading$"))
    app.add_handler(CallbackQueryHandler(bh.futures_trading, pattern="^futures_trading$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^futures_positions$"))
    app.add_handler(CallbackQueryHandler(bh.funding_fees_cb, pattern="^funding_fees$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^spot_orders$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^spot_history$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^leverage_settings$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^spot_buy$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^spot_sell$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^futures_long$"))
    app.add_handler(CallbackQueryHandler(bh.futures_positions_cb, pattern="^futures_short$"))

    # ── Trade manager ─────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(bh.trade_manager_menu, pattern="^trade_manager$"))
    app.add_handler(CallbackQueryHandler(bh.manage_specific_trade, pattern=r"^manage_trade_\d+$"))
    app.add_handler(CallbackQueryHandler(bh.close_trade_cb, pattern=r"^close_trade_\d+$"))
    app.add_handler(CallbackQueryHandler(bh.close_partial_cb, pattern=r"^close_partial_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(bh.sl_to_be_cb, pattern=r"^sl_be_\d+$"))
    app.add_handler(CallbackQueryHandler(bh.trail_stop_cb, pattern=r"^trail_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(bh.reverse_cb, pattern=r"^reverse_\d+$"))
    app.add_handler(CallbackQueryHandler(bh.emergency_close_all_cb, pattern="^emergency_close_all$"))
    app.add_handler(CallbackQueryHandler(bh.trade_side_cb, pattern=r"^trade_side_(BUY|SELL)$"))

    # ── Stats / Settings / Help ───────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(bh.my_stats, pattern="^my_stats$"))
    app.add_handler(CallbackQueryHandler(bh.settings_menu, pattern="^settings$"))
    app.add_handler(CallbackQueryHandler(bh.help_menu, pattern="^help$"))

    # ── Admin panel ───────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(ah.admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(ah.admin_users, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(ah.admin_user_detail, pattern=r"^admin_user_\d+$"))
    app.add_handler(CallbackQueryHandler(ah.ban_user_cb, pattern=r"^ban_user_\d+$"))
    app.add_handler(CallbackQueryHandler(ah.unban_user_cb, pattern=r"^unban_user_\d+$"))
    app.add_handler(CallbackQueryHandler(ah.admin_global_stats, pattern="^admin_global_stats$"))
    app.add_handler(CallbackQueryHandler(ah.admin_logs, pattern="^admin_logs$"))
    app.add_handler(CallbackQueryHandler(ah.admin_scanner_status, pattern="^admin_scanner_status$"))
    app.add_handler(CallbackQueryHandler(ah.admin_learning_status, pattern="^admin_learning_status$"))
    app.add_handler(CallbackQueryHandler(ah.admin_system_status, pattern="^admin_system_status$"))
    app.add_handler(CallbackQueryHandler(ah.admin_db_info, pattern="^admin_db_info$"))
    app.add_handler(CallbackQueryHandler(ah.admin_export_logs, pattern="^admin_export_logs$"))
    app.add_handler(CallbackQueryHandler(ah.admin_export_users, pattern="^admin_export_users$"))
    app.add_handler(CallbackQueryHandler(ah.admin_restart, pattern="^admin_restart$"))
    app.add_handler(CallbackQueryHandler(ah.confirm_restart, pattern="^confirm_restart$"))
    app.add_handler(CallbackQueryHandler(ah.admin_maintenance, pattern="^admin_maintenance$"))

    # ── Admin broadcast sub-menu ──────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(_admin_broadcast_menu, pattern="^admin_broadcast$"))
    app.add_handler(CallbackQueryHandler(_user_stats_cb, pattern=r"^user_stats_\d+$"))


async def _admin_broadcast_menu(update: Update, context) -> None:
    await update.callback_query.answer()
    import keyboards as kb
    await update.callback_query.edit_message_text(
        "📣 <b>Broadcast</b>\n\nChoose media type:",
        parse_mode="HTML",
        reply_markup=kb.admin_broadcast_menu(),
    )


async def _user_stats_cb(update: Update, context) -> None:
    await update.callback_query.answer()
    target_id = int(update.callback_query.data.split("_")[-1])
    s = db.get_user_stats(target_id)
    text = (
        f"📊 <b>Stats for User {target_id}</b>\n"
        f"Trades: {s['total']} | W:{s['wins']} L:{s['losses']}\n"
        f"Win Rate: {s['win_rate']:.1f}%\n"
        f"Total PnL: {s['total_pnl']:.4f} USDT"
    )
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    await update.callback_query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"admin_user_{target_id}")]]),
    )


# ── Background tasks ──────────────────────────────────────────────────────────

async def _start_background_tasks(app: Application) -> None:
    """Launch scanner and position monitor as background asyncio tasks."""
    logger.info("Starting background tasks...")
    app.create_task(scanner_module.run_scanner(signal_callback=_on_signal))
    app.create_task(pm.run_position_monitor(bot=app.bot))
    logger.info("Background tasks launched.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global _app
    logger.info("Initialising database...")
    db.init_db()
    logger.info("Database ready.")

    app = Application.builder().token(config.BOT_TOKEN).build()
    _app = app

    register_handlers(app)
    app.post_init = _start_background_tasks

    logger.info("Bot starting (polling mode)...")
    db.log_event("Bot Started", f"v{config.BOT_VERSION}", "INFO")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
