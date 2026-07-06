"""
All InlineKeyboard layouts for the bot.
Every menu returns an InlineKeyboardMarkup.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config


def main_menu(chat_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🏠 Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("🔑 Binance Account", callback_data="binance_account")],
        [
            InlineKeyboardButton("📈 Spot Trading", callback_data="spot_trading"),
            InlineKeyboardButton("📊 Futures Trading", callback_data="futures_trading"),
        ],
        [
            InlineKeyboardButton("🤖 AI Scanner", callback_data="ai_scanner"),
            InlineKeyboardButton("🎯 AI Signals", callback_data="ai_signals"),
        ],
        [InlineKeyboardButton("📉 Trade Manager", callback_data="trade_manager")],
        [
            InlineKeyboardButton("📊 My Statistics", callback_data="my_stats"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        ],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    if chat_id == config.ADMIN_CHAT_ID:
        buttons.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


def back_to_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")]])


def binance_account_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Connect API Keys", callback_data="connect_api")],
        [InlineKeyboardButton("🗑 Remove API Keys", callback_data="remove_api")],
        [InlineKeyboardButton("📊 Live Dashboard", callback_data="live_dashboard")],
        [
            InlineKeyboardButton("📈 Spot Account", callback_data="set_spot"),
            InlineKeyboardButton("📊 Futures Account", callback_data="set_futures"),
        ],
        [
            InlineKeyboardButton("🧪 Testnet", callback_data="set_testnet"),
            InlineKeyboardButton("🌍 Real Account", callback_data="set_real"),
        ],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
    ])


def api_network_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 Testnet", callback_data="net_testnet")],
        [InlineKeyboardButton("🌍 Real Account", callback_data="net_real")],
        [InlineKeyboardButton("🔙 Back", callback_data="binance_account")],
    ])


def trade_manager_menu(open_trade_ids: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("➕ Open New Trade", callback_data="open_trade")],
    ]
    if open_trade_ids:
        for tid in open_trade_ids[:5]:
            buttons.append([InlineKeyboardButton(f"📋 Manage Trade #{tid}", callback_data=f"manage_trade_{tid}")])
    buttons.append([InlineKeyboardButton("🚨 Emergency Close All", callback_data="emergency_close_all")])
    buttons.append([InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def manage_trade_menu(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Close Trade", callback_data=f"close_trade_{trade_id}")],
        [InlineKeyboardButton("📉 Close 50%", callback_data=f"close_partial_50_{trade_id}")],
        [InlineKeyboardButton("📉 Close 25%", callback_data=f"close_partial_25_{trade_id}")],
        [InlineKeyboardButton("🛡 Move SL to BE", callback_data=f"sl_be_{trade_id}")],
        [
            InlineKeyboardButton("✏️ Update SL", callback_data=f"update_sl_{trade_id}"),
            InlineKeyboardButton("✏️ Update TP", callback_data=f"update_tp_{trade_id}"),
        ],
        [InlineKeyboardButton("🔄 Trailing Stop 1%", callback_data=f"trail_1_{trade_id}")],
        [InlineKeyboardButton("🔄 Trailing Stop 2%", callback_data=f"trail_2_{trade_id}")],
        [InlineKeyboardButton("↩️ Reverse Position", callback_data=f"reverse_{trade_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="trade_manager")],
    ])


def open_trade_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 BUY", callback_data="trade_side_BUY"),
            InlineKeyboardButton("📉 SELL", callback_data="trade_side_SELL"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="trade_manager")],
    ])


def scanner_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Fast (1s)", callback_data="scanner_fast")],
        [InlineKeyboardButton("🐢 Normal (10s)", callback_data="scanner_slow")],
        [InlineKeyboardButton("📊 View Top Pairs", callback_data="scanner_top")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="ai_scanner")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
    ])


def signals_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Latest Signals", callback_data="latest_signals")],
        [InlineKeyboardButton("📊 Signal History", callback_data="signal_history")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="ai_signals")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
    ])


def spot_trading_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Place BUY Order", callback_data="spot_buy")],
        [InlineKeyboardButton("📉 Place SELL Order", callback_data="spot_sell")],
        [InlineKeyboardButton("📋 Open Orders", callback_data="spot_orders")],
        [InlineKeyboardButton("📜 Trade History", callback_data="spot_history")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
    ])


def futures_trading_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Long (BUY)", callback_data="futures_long")],
        [InlineKeyboardButton("📉 Short (SELL)", callback_data="futures_short")],
        [InlineKeyboardButton("📋 Open Positions", callback_data="futures_positions")],
        [InlineKeyboardButton("📊 Funding Fees", callback_data="funding_fees")],
        [InlineKeyboardButton("⚙️ Leverage Settings", callback_data="leverage_settings")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
    ])


def settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 API Keys", callback_data="binance_account")],
        [
            InlineKeyboardButton("📈 Switch to Spot", callback_data="set_spot"),
            InlineKeyboardButton("📊 Switch to Futures", callback_data="set_futures"),
        ],
        [
            InlineKeyboardButton("🧪 Testnet Mode", callback_data="set_testnet"),
            InlineKeyboardButton("🌍 Real Mode", callback_data="set_real"),
        ],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
    ])


# ── Admin keyboards ────────────────────────────────────────────────────────────

def admin_panel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("📊 Statistics", callback_data="admin_global_stats"),
        ],
        [InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📜 Live Logs", callback_data="admin_logs")],
        [
            InlineKeyboardButton("🤖 Scanner Status", callback_data="admin_scanner_status"),
            InlineKeyboardButton("🧠 Learning Status", callback_data="admin_learning_status"),
        ],
        [InlineKeyboardButton("⚙️ System Status", callback_data="admin_system_status")],
        [
            InlineKeyboardButton("📤 Export Logs", callback_data="admin_export_logs"),
            InlineKeyboardButton("📤 Export Users", callback_data="admin_export_users"),
        ],
        [
            InlineKeyboardButton("🔄 Restart Bot", callback_data="admin_restart"),
            InlineKeyboardButton("🛠 Maintenance", callback_data="admin_maintenance"),
        ],
        [InlineKeyboardButton("💾 DB Info", callback_data="admin_db_info")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")],
    ])


def admin_broadcast_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Send Text", callback_data="bc_text")],
        [InlineKeyboardButton("🖼 Send Photo", callback_data="bc_photo")],
        [InlineKeyboardButton("🎥 Send Video", callback_data="bc_video")],
        [InlineKeyboardButton("📄 Send Document", callback_data="bc_document")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ])


def admin_users_menu(users: list) -> InlineKeyboardMarkup:
    buttons = []
    for u in users[:10]:
        name = u["first_name"] or u["username"] or str(u["chat_id"])
        buttons.append([InlineKeyboardButton(
            f"{'🚫' if u['is_banned'] else '👤'} {name[:20]}",
            callback_data=f"admin_user_{u['chat_id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


def admin_user_detail_menu(chat_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    ban_text = "✅ Unban User" if is_banned else "🚫 Ban User"
    ban_cb = f"unban_user_{chat_id}" if is_banned else f"ban_user_{chat_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ban_text, callback_data=ban_cb)],
        [InlineKeyboardButton("📊 View Stats", callback_data=f"user_stats_{chat_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_users")],
    ])


def logs_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Logs", callback_data="admin_logs")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ])


def confirm_menu(confirm_cb: str, cancel_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=confirm_cb),
            InlineKeyboardButton("❌ Cancel", callback_data=cancel_cb),
        ],
    ])
