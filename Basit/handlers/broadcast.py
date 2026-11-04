"""Owner-only broadcast of a replied message to registered private users."""
import asyncio

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from ..config import load_settings
from ..database import pm_users

settings = load_settings()


async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != settings.owner_id:
        return
    if not msg.reply_to_message:
        return await msg.reply_text("Reply to the message you want to broadcast, then use /send.")
    users = await pm_users.find({}, {"_id": 1}).to_list(length=100000)
    if not users:
        return await msg.reply_text("No private users are registered yet.")
    sent = failed = 0
    status = await msg.reply_text(f"📣 Broadcast started for {len(users)} users.")
    for item in users:
        uid = item.get("_id")
        if not isinstance(uid, int):
            continue
        try:
            await msg.reply_to_message.copy(chat_id=uid)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    await status.edit_text(f"✅ Broadcast finished.\nSent: {sent}\nFailed: {failed}")


def register(app):
    app.add_handler(CommandHandler("send", send_broadcast))
