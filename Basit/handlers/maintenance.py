"""Owner command maintenance controls and early command guard."""
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, ContextTypes, ApplicationHandlerStop, filters

from ..config import load_settings
from ..database import settings_collection, audit_logs

settings = load_settings()


async def command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not message.text or not message.text.lstrip().startswith("/"):
        return
    command = message.text.split()[0].split("@")[0].lstrip("/").casefold()
    if command in {"stop", "run"} or user.id == settings.owner_id:
        return
    if message.chat.type in {"group", "supergroup"}:
        from .gameplay import is_muted
        if await is_muted(message.chat_id, user.id):
            raise ApplicationHandlerStop
    data = await settings_collection.find_one({"_id": "maintenance"}) or {}
    stopped = {str(x).casefold().lstrip("/") for x in data.get("stopped", [])}
    if command in stopped or "*" in stopped:
        await message.reply_text(
            "⚙️ This command is temporarily unavailable.\n"
            "We are doing some quick maintenance. Please try again shortly 🙂"
        )
        raise ApplicationHandlerStop


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    args = [x.casefold().lstrip("/") for x in context.args]
    if not args:
        return await update.effective_message.reply_text("Usage: /stop <command|all|list>")
    data = await settings_collection.find_one({"_id": "maintenance"}) or {}
    stopped = set(data.get("stopped", []))
    if args[0] == "list":
        text = "📋 STOPPED COMMANDS\n" + ("\n".join("/" + x for x in sorted(stopped)) if stopped else "None")
        return await update.effective_message.reply_text(text + "\n\nUse /run <command> or /run all.")
    if args[0] == "all":
        stopped = {"*"}
    else:
        command = args[0]
        if not command.isidentifier():
            return await update.effective_message.reply_text("Invalid command name.")
        stopped.add(command)
    await settings_collection.update_one({"_id": "maintenance"}, {"$set": {"stopped": sorted(stopped)}}, upsert=True)
    await audit_logs.insert_one({"action": "commands_stopped", "actor_id": update.effective_user.id, "commands": sorted(stopped)})
    await update.effective_message.reply_text(f"✅ Command stopped. Total stopped entries: {len(stopped)}")


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    args = [x.casefold().lstrip("/") for x in context.args]
    if not args:
        return await update.effective_message.reply_text("Usage: /run <command|all>")
    data = await settings_collection.find_one({"_id": "maintenance"}) or {}
    stopped = set(data.get("stopped", []))
    if args[0] == "all":
        stopped.clear()
    else:
        stopped.discard(args[0])
        stopped.discard("*")
    await settings_collection.update_one({"_id": "maintenance"}, {"$set": {"stopped": sorted(stopped)}}, upsert=True)
    await audit_logs.insert_one({"action": "commands_resumed", "actor_id": update.effective_user.id, "commands": sorted(stopped)})
    await update.effective_message.reply_text(
        "🚀 All commands are live again!" if args[0] == "all"
        else f"▶️ /{args[0]} is active again. Remaining stopped: {len(stopped)}"
    )


def register(app):
    app.add_handler(MessageHandler(filters.COMMAND, command_guard), group=-1)
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("run", run_command))
