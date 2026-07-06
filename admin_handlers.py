"""
Admin panel handlers — system status, logs, user management, DB export.
All handlers check that the caller is ADMIN_CHAT_ID.
"""

import asyncio
import csv
import io
import logging
import os
import platform
import time

import psutil
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
import keyboards as kb
import scanner as sc
import position_monitor as pm
from adaptive_learning import get_learning_summary
from config import ADMIN_CHAT_ID, BOT_VERSION

logger = logging.getLogger(__name__)
_BOT_START_TIME = time.time()


# ── Guard ──────────────────────────────────────────────────────────────────────

def _is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_CHAT_ID


async def _safe_edit(update: Update, text: str, markup=None) -> None:
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_html(text, reply_markup=markup)
    except Exception:
        try:
            if update.callback_query:
                await update.callback_query.message.reply_html(text, reply_markup=markup)
        except Exception:
            pass


# ── Admin Panel ────────────────────────────────────────────────────────────────

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()
    chat_id = update.effective_user.id
    if not _is_admin(chat_id):
        await _safe_edit(update, "❌ Access denied.")
        return
    stats = db.get_global_stats()
    text = (
        f"👑 <b>Admin Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Users:</b> {stats['users']}\n"
        f"📊 <b>Total Trades:</b> {stats['trades']}\n"
        f"📋 <b>Open Trades:</b> {stats['open_trades']}\n"
        f"💰 <b>Total PnL:</b> <code>{stats['total_pnl']:.4f} USDT</code>\n"
        f"🎯 <b>Signals:</b> {stats['signals']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ Bot v{BOT_VERSION}"
    )
    await _safe_edit(update, text, kb.admin_panel_menu())


# ── Users ──────────────────────────────────────────────────────────────────────

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    users = db.get_all_users(include_banned=True)
    text = f"👥 <b>Users ({len(users)})</b>\n━━━━━━━━━━━━━━━━━━"
    await _safe_edit(update, text, kb.admin_users_menu(users))


async def admin_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    target_id = int(update.callback_query.data.split("_")[-1])
    user = db.get_user(target_id)
    if not user:
        await _safe_edit(update, "❌ User not found.", kb.admin_panel_menu())
        return
    stats = db.get_user_stats(target_id)
    keys = db.get_api_keys(target_id)
    text = (
        f"👤 <b>User Details</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> <code>{user['chat_id']}</code>\n"
        f"👤 <b>Name:</b> {user['first_name'] or 'N/A'} {user['last_name'] or ''}\n"
        f"📛 <b>Username:</b> @{user['username'] or 'N/A'}\n"
        f"🔑 <b>API:</b> {'✅ Connected' if keys and keys['is_valid'] else '❌ Not connected'}\n"
        f"💎 <b>Mode:</b> {user['account_type'].upper()} | {user['network'].upper()}\n"
        f"📊 <b>Trades:</b> {stats['total']} | W:{stats['wins']} L:{stats['losses']}\n"
        f"💰 <b>Total PnL:</b> {stats['total_pnl']:.4f}\n"
        f"🚫 <b>Banned:</b> {'Yes' if user['is_banned'] else 'No'}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await _safe_edit(update, text, kb.admin_user_detail_menu(target_id, bool(user["is_banned"])))


async def ban_user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    target_id = int(update.callback_query.data.split("_")[-1])
    db.ban_user(target_id, True)
    db.log_event("User Banned", f"admin banned chat_id={target_id}", "WARNING")
    await _safe_edit(update, f"🚫 User <code>{target_id}</code> banned.", kb.admin_panel_menu())


async def unban_user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    target_id = int(update.callback_query.data.split("_")[-1])
    db.ban_user(target_id, False)
    db.log_event("User Unbanned", f"admin unbanned chat_id={target_id}", "INFO")
    await _safe_edit(update, f"✅ User <code>{target_id}</code> unbanned.", kb.admin_panel_menu())


# ── Global Stats ───────────────────────────────────────────────────────────────

async def admin_global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    stats = db.get_global_stats()
    uptime_secs = int(time.time() - _BOT_START_TIME)
    h, m, s = uptime_secs // 3600, (uptime_secs % 3600) // 60, uptime_secs % 60
    text = (
        f"📊 <b>Global Statistics</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Users:</b> {stats['users']}\n"
        f"📊 <b>Closed Trades:</b> {stats['trades']}\n"
        f"📋 <b>Open Trades:</b> {stats['open_trades']}\n"
        f"💰 <b>Global PnL:</b> <code>{stats['total_pnl']:.4f} USDT</code>\n"
        f"🎯 <b>Total Signals:</b> {stats['signals']}\n"
        f"⏱ <b>Uptime:</b> {h:02d}:{m:02d}:{s:02d}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await _safe_edit(update, text, InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_global_stats")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]))


# ── Live Logs ──────────────────────────────────────────────────────────────────

async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    logs = db.get_recent_logs(30)
    if not logs:
        await _safe_edit(update, "📜 <b>No logs yet.</b>", kb.logs_menu())
        return
    level_icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🔴", "DEBUG": "🔵"}
    lines = ["📜 <b>Live Logs (last 30)</b>\n━━━━━━━━━━━━━━━━━━"]
    for log in logs[:25]:
        icon = level_icons.get(log["level"], "📌")
        ts = time.strftime("%H:%M:%S", time.localtime(log["created_at"]))
        event_line = f"{icon} <code>[{ts}]</code> <b>{log['event']}</b>"
        if log["detail"]:
            event_line += f"\n   <i>{log['detail'][:80]}</i>"
        lines.append(event_line)
    await _safe_edit(update, "\n".join(lines), kb.logs_menu())


# ── Scanner Status ─────────────────────────────────────────────────────────────

async def admin_scanner_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    status = sc.get_scanner_status()
    last_scan = time.strftime("%H:%M:%S", time.localtime(status["last_scan"])) if status["last_scan"] else "Never"
    text = (
        f"🤖 <b>Scanner Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📡 <b>Running:</b> {'🟢 Yes' if status['running'] else '🔴 No'}\n"
        f"⏱ <b>Interval:</b> {status['interval']}s\n"
        f"📊 <b>Pairs Scanned:</b> {status['pairs_scanned']}\n"
        f"🕐 <b>Last Scan:</b> {last_scan}\n"
        f"⚠️ <b>Errors:</b> {status['errors']}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await _safe_edit(update, text, InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_scanner_status")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]))


# ── Learning Status ────────────────────────────────────────────────────────────

async def admin_learning_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    summary = get_learning_summary()
    lines = [
        f"🧠 <b>Adaptive Learning Status</b>\n"
        f"Total Records: {summary['total_records']}\n"
        f"━━━━━━━━━━━━━━━━━━"
    ]
    for s in summary["strategies"]:
        wr_bar = "█" * int(s["win_rate"] / 10) + "░" * (10 - int(s["win_rate"] / 10))
        lines.append(
            f"📊 <b>{s['strategy'].replace('_',' ').title()}</b>\n"
            f"  Weight: {s['weight']:.2f} | WR: {s['win_rate']:.1f}%\n"
            f"  <code>[{wr_bar}]</code> ({s['total_trades']} trades)"
        )
    await _safe_edit(update, "\n".join(lines), InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_learning_status")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]))


# ── System Status ──────────────────────────────────────────────────────────────

async def admin_system_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Reading system stats...")
    if not _is_admin(update.effective_user.id):
        return
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime_secs = int(time.time() - _BOT_START_TIME)
        h, m, s = uptime_secs // 3600, (uptime_secs % 3600) // 60, uptime_secs % 60
        scanner_st = sc.get_scanner_status()
        monitor_st = pm.get_monitor_status()
        db_size_kb = os.path.getsize("database.db") / 1024 if os.path.exists("database.db") else 0
        text = (
            f"⚙️ <b>System Status</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🖥 <b>CPU:</b> {cpu:.1f}%\n"
            f"💾 <b>RAM:</b> {mem.percent:.1f}% ({mem.used//1024//1024}/{mem.total//1024//1024} MB)\n"
            f"💿 <b>Disk:</b> {disk.percent:.1f}% ({disk.used//1024//1024//1024:.1f}/{disk.total//1024//1024//1024:.1f} GB)\n"
            f"🗄 <b>DB Size:</b> {db_size_kb:.1f} KB\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>Scanner:</b> {'🟢 Running' if scanner_st['running'] else '🔴 Stopped'}\n"
            f"📡 <b>Monitor:</b> {'🟢 Running' if monitor_st['running'] else '🔴 Stopped'}\n"
            f"  Monitoring: {monitor_st['trades_monitored']} trades\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <b>Bot Uptime:</b> {h:02d}:{m:02d}:{s:02d}\n"
            f"🐍 <b>Python:</b> {platform.python_version()}\n"
            f"🖥 <b>OS:</b> {platform.system()} {platform.release()}"
        )
    except Exception as exc:
        text = f"⚙️ <b>System Status</b>\n\n❌ Error reading stats: {exc}"
    await _safe_edit(update, text, InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_system_status")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]))


# ── DB Info ────────────────────────────────────────────────────────────────────

async def admin_db_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    try:
        with db.db_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        lines = ["💾 <b>Database Info</b>\n━━━━━━━━━━━━━━━━━━"]
        for t in tables:
            with db.db_conn() as conn:
                count = conn.execute(f"SELECT COUNT(*) FROM {t['name']}").fetchone()[0]
            lines.append(f"📋 <code>{t['name']}</code>: {count} rows")
        db_kb = os.path.getsize("database.db") / 1024 if os.path.exists("database.db") else 0
        lines.append(f"━━━━━━━━━━━━━━━━━━\n💿 Total size: {db_kb:.1f} KB")
        await _safe_edit(update, "\n".join(lines), InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
        ]))
    except Exception as exc:
        await _safe_edit(update, f"❌ DB error: {exc}", kb.admin_panel_menu())


# ── Export Logs ────────────────────────────────────────────────────────────────

async def admin_export_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Exporting logs...")
    if not _is_admin(update.effective_user.id):
        return
    logs = db.get_recent_logs(1000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "level", "event", "detail", "chat_id", "created_at"])
    for log in logs:
        writer.writerow([log["id"], log["level"], log["event"], log["detail"], log["chat_id"], log["created_at"]])
    buf.seek(0)
    await update.callback_query.message.reply_document(
        document=InputFile(io.BytesIO(buf.getvalue().encode()), filename="bot_logs.csv"),
        caption="📤 Bot logs export",
    )


async def admin_export_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("⏳ Exporting users...")
    if not _is_admin(update.effective_user.id):
        return
    users = db.get_all_users(include_banned=True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["chat_id", "username", "first_name", "last_name", "joined_at", "last_seen", "is_banned", "account_type", "network"])
    for u in users:
        writer.writerow([u["chat_id"], u["username"], u["first_name"], u["last_name"],
                         u["joined_at"], u["last_seen"], u["is_banned"], u["account_type"], u["network"]])
    buf.seek(0)
    await update.callback_query.message.reply_document(
        document=InputFile(io.BytesIO(buf.getvalue().encode()), filename="users_export.csv"),
        caption=f"📤 Users export ({len(users)} records)",
    )


# ── Restart / Maintenance ──────────────────────────────────────────────────────

async def admin_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    await _safe_edit(
        update,
        "🔄 <b>Restart Bot</b>\n\nAre you sure you want to restart?",
        kb.confirm_menu("confirm_restart", "admin_panel"),
    )


async def confirm_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("🔄 Restarting...")
    if not _is_admin(update.effective_user.id):
        return
    db.log_event("Bot Restart", "Admin triggered restart", "WARNING")
    await _safe_edit(update, "🔄 <b>Bot is restarting...</b>")
    await asyncio.sleep(1)
    os.execv(__import__("sys").executable, [__import__("sys").executable] + __import__("sys").argv)


async def admin_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    if not _is_admin(update.effective_user.id):
        return
    current = db.get_setting("maintenance_mode", "0")
    new_val = "0" if current == "1" else "1"
    db.set_setting("maintenance_mode", new_val)
    status = "🛠 ON" if new_val == "1" else "✅ OFF"
    await _safe_edit(update, f"🛠 <b>Maintenance Mode:</b> {status}", kb.admin_panel_menu())
