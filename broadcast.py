"""
Broadcast module — send messages to all registered users.
Supports: Text, Photo, Video, Animation, Voice, Audio, Sticker, Document.
Shows live progress in the admin message.
"""

import asyncio
import logging
import time
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton, InputFile

import database as db
from config import ADMIN_CHAT_ID

logger = logging.getLogger(__name__)

# Conversation state for building a broadcast
AWAIT_BC_CONTENT = 100


async def do_broadcast(
    bot: Bot,
    admin_chat_id: int,
    content: dict,
    progress_message_id: int | None = None,
) -> dict:
    """
    Execute a broadcast to all non-banned users.
    content keys: type (text/photo/video/document/animation/voice/audio/sticker),
                  text, file_id, caption, reply_markup (optional InlineKeyboardMarkup).
    Returns {success, fail, total}.
    """
    users = db.get_all_users(include_banned=False)
    total = len(users)
    bc_id = db.create_broadcast(admin_chat_id, content.get("text"), content.get("type"), total)
    success_count = 0
    fail_count = 0
    batch_size = 25
    last_update = time.time()

    async def _send_one(chat_id: int) -> bool:
        try:
            ctype = content.get("type", "text")
            kwargs = {}
            if content.get("reply_markup"):
                kwargs["reply_markup"] = content["reply_markup"]
            if ctype == "text":
                await bot.send_message(chat_id, content.get("text", ""), parse_mode="HTML", **kwargs)
            elif ctype == "photo":
                await bot.send_photo(chat_id, content["file_id"], caption=content.get("caption"), parse_mode="HTML", **kwargs)
            elif ctype == "video":
                await bot.send_video(chat_id, content["file_id"], caption=content.get("caption"), parse_mode="HTML", **kwargs)
            elif ctype == "animation":
                await bot.send_animation(chat_id, content["file_id"], caption=content.get("caption"), parse_mode="HTML", **kwargs)
            elif ctype == "voice":
                await bot.send_voice(chat_id, content["file_id"], caption=content.get("caption"), **kwargs)
            elif ctype == "audio":
                await bot.send_audio(chat_id, content["file_id"], caption=content.get("caption"), **kwargs)
            elif ctype == "sticker":
                await bot.send_sticker(chat_id, content["file_id"], **kwargs)
            elif ctype == "document":
                await bot.send_document(chat_id, content["file_id"], caption=content.get("caption"), parse_mode="HTML", **kwargs)
            return True
        except Exception as exc:
            logger.debug("Broadcast send failed to %s: %s", chat_id, exc)
            return False

    for i in range(0, total, batch_size):
        batch = users[i: i + batch_size]
        results = await asyncio.gather(*[_send_one(u["chat_id"]) for u in batch])
        for r in results:
            if r:
                success_count += 1
            else:
                fail_count += 1

        db.update_broadcast_progress(bc_id, success_count, fail_count)

        # Update progress message every 2 seconds
        if progress_message_id and (time.time() - last_update) > 2:
            last_update = time.time()
            done = success_count + fail_count
            pct = int(done / total * 100) if total else 100
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            try:
                await bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=progress_message_id,
                    text=(
                        f"📣 <b>Broadcast in Progress...</b>\n"
                        f"<code>[{bar}]</code> {pct}%\n"
                        f"✅ {success_count} | ❌ {fail_count} | 📊 {done}/{total}"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # Rate limiting — Telegram allows ~30 messages/sec per bot
        await asyncio.sleep(0.05)

    db.finish_broadcast(bc_id)

    # Final summary
    if progress_message_id:
        try:
            await bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=progress_message_id,
                text=(
                    f"📣 <b>Broadcast Complete!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"✅ <b>Sent:</b> {success_count}\n"
                    f"❌ <b>Failed:</b> {fail_count}\n"
                    f"📊 <b>Total:</b> {total}"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]
                ]),
            )
        except Exception:
            pass

    db.log_event(
        "Broadcast Complete",
        f"success={success_count} fail={fail_count} total={total}",
        "INFO",
        admin_chat_id,
    )
    return {"success": success_count, "fail": fail_count, "total": total}


# ── Handler helpers (called from admin_handlers) ───────────────────────────────

async def broadcast_text(update, context) -> None:
    """Start text broadcast conversation."""
    await update.callback_query.answer()
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    await update.callback_query.edit_message_text(
        "📝 <b>Text Broadcast</b>\n\nSend the message text (HTML formatting supported):\n\n<i>Or /cancel to abort.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_broadcast")]]),
    )
    context.user_data["bc_type"] = "text"
    return AWAIT_BC_CONTENT


async def broadcast_photo(update, context) -> None:
    await update.callback_query.answer()
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    await update.callback_query.edit_message_text(
        "🖼 <b>Photo Broadcast</b>\n\nSend the photo with optional caption:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_broadcast")]]),
    )
    context.user_data["bc_type"] = "photo"
    return AWAIT_BC_CONTENT


async def receive_broadcast_content(update, context) -> int:
    """Receive broadcast content and execute."""
    from telegram.ext import ConversationHandler
    bc_type = context.user_data.pop("bc_type", "text")
    content: dict = {"type": bc_type}

    if bc_type == "text":
        content["text"] = update.message.text_html
    elif bc_type == "photo" and update.message.photo:
        content["file_id"] = update.message.photo[-1].file_id
        content["caption"] = update.message.caption_html or ""
    elif bc_type == "video" and update.message.video:
        content["file_id"] = update.message.video.file_id
        content["caption"] = update.message.caption_html or ""
    elif bc_type == "document" and update.message.document:
        content["file_id"] = update.message.document.file_id
        content["caption"] = update.message.caption_html or ""
    elif bc_type == "animation" and update.message.animation:
        content["file_id"] = update.message.animation.file_id
    elif bc_type == "voice" and update.message.voice:
        content["file_id"] = update.message.voice.file_id
    elif bc_type == "audio" and update.message.audio:
        content["file_id"] = update.message.audio.file_id
    elif bc_type == "sticker" and update.message.sticker:
        content["file_id"] = update.message.sticker.file_id
    else:
        await update.message.reply_html("❌ Unsupported content type. Broadcast cancelled.")
        return ConversationHandler.END

    progress_msg = await update.message.reply_html(
        f"📣 <b>Starting broadcast...</b>\n<code>[░░░░░░░░░░]</code> 0%"
    )
    asyncio.create_task(
        do_broadcast(context.bot, update.effective_user.id, content, progress_msg.message_id)
    )
    return ConversationHandler.END


async def bc_cancel(update, context) -> int:
    from telegram.ext import ConversationHandler
    context.user_data.pop("bc_type", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "❌ Broadcast cancelled.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]),
        )
    return ConversationHandler.END
