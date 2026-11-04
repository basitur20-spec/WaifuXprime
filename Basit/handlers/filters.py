"""Group keyword filters managed by group administrators."""
from telegram import Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import CommandHandler, MessageHandler, ContextTypes, filters

from ..config import load_settings
from ..database import chat_filters

settings = load_settings()


async def _can_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False
    if user.id == settings.owner_id:
        return True
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


async def addfilter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await msg.reply_text("Use /addfilter inside a group.")
    if not await _can_manage(update, context):
        return await msg.reply_text("Only group admins can manage filters.")
    if not context.args:
        return await msg.reply_text("Usage: /addfilter <keyword> <reply text> (or reply to a message).")
    keyword = context.args[0].casefold().strip()
    response = " ".join(context.args[1:]).strip()
    if not response and msg.reply_to_message:
        response = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if not keyword or not response:
        return await msg.reply_text("Provide a keyword and reply text.")
    await chat_filters.update_one(
        {"chat_id": msg.chat_id, "keyword": keyword},
        {"$set": {"reply": response, "created_by": update.effective_user.id}},
        upsert=True,
    )
    await msg.reply_text(f"✅ Filter saved for keyword: {keyword}")


async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await chat_filters.find({"chat_id": update.effective_chat.id}).sort("keyword", 1).to_list(length=100)
    if not rows:
        return await update.effective_message.reply_text("No filters configured in this group.")
    text = "📋 GROUP FILTERS\n" + "\n".join(f"• {row['keyword']} → {row.get('reply', '')[:80]}" for row in rows)
    await update.effective_message.reply_text(text)


async def delfilter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await _can_manage(update, context):
        return await msg.reply_text("Only group admins can manage filters.")
    if len(context.args) != 1:
        return await msg.reply_text("Usage: /delfilter <keyword>")
    result = await chat_filters.delete_one({"chat_id": msg.chat_id, "keyword": context.args[0].casefold()})
    await msg.reply_text("✅ Filter deleted." if result.deleted_count else "No matching filter found.")


async def deall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await _can_manage(update, context):
        return await msg.reply_text("Only group admins can manage filters.")
    result = await chat_filters.delete_many({"chat_id": msg.chat_id})
    await msg.reply_text(f"🗑️ Deleted {result.deleted_count} group filters.")


async def filter_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.from_user or msg.from_user.is_bot:
        return
    value = (msg.text or msg.caption or "").casefold()
    if not value or value.startswith("/"):
        return
    rows = await chat_filters.find({"chat_id": msg.chat_id}).to_list(length=100)
    for row in rows:
        keyword = str(row.get("keyword", "")).casefold()
        if keyword and keyword in value:
            await msg.reply_text(row.get("reply", ""))
            return


def register(app):
    app.add_handler(CommandHandler("addfilter", addfilter))
    app.add_handler(CommandHandler("filter", addfilter))
    app.add_handler(CommandHandler("filters", list_filters))
    app.add_handler(CommandHandler("delfilter", delfilter))
    app.add_handler(CommandHandler("deall", deall))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, filter_message), group=2)
