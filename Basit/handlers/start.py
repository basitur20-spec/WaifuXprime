"""Telegram /start and help screens."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..config import load_settings
from ..database import get_user, pm_users

settings = load_settings()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await get_user(user.id, user)
    await pm_users.update_one({"_id": user.id}, {"$set": {"_id": user.id}}, upsert=True)
    text = (
        f"𝐇𝐄𝐋𝐋𝐎🕊️ {user.first_name},\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ Welcome to 𝑾𝒂𝒊𝒇𝒖𝑿_𝑷𝒓𝒊𝒎𝒆 ✨\n\n"
        "Your anime waifu collection journey starts here.\n"
        "Add me to a group to spawn characters and hunt them!\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    buttons = [
        [InlineKeyboardButton("UPDATES 💫", url="https://t.me/+4UJ1aAf4-ypiNThk"),
         InlineKeyboardButton("SUPPORT 🏪", url="https://t.me/+LfvrnvE8x3I3OGU0")],
        [InlineKeyboardButton("ADD TO GROUP ✨", url=f"https://t.me/{context.bot.username}?startgroup=true")],
        [InlineKeyboardButton("GROUP ☘️", url="https://t.me/movieandweb_s_group"),
         InlineKeyboardButton("HELP ⚙️", callback_data="help:home")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pages = {
        "help:home": ("📘 HELP CENTER\nChoose a command category.", [
            [InlineKeyboardButton("COMMANDS 📜", callback_data="help:commands"),
             InlineKeyboardButton("GAMES 🎮", callback_data="help:games")],
            [InlineKeyboardButton("CLOSE ❌", callback_data="help:close")],
        ]),
        "help:commands": (
            "CORE COMMANDS\n/hunt /waifu /fav /marriage /propose\n"
            "/collection /dmode /f /find /dfind\n"
            "/marketplace /sell /gift /trade\n/balance /bonus /explore /pay\n"
            "/upload /dupload /auction /spawn /changetime",
            [[InlineKeyboardButton("BACK ⬅", callback_data="help:home")]],
        ),
        "help:games": (
            "/roll — dice\n/darts — darts\n/goal — goal\n/mines — mines",
            [[InlineKeyboardButton("BACK ⬅", callback_data="help:home")]],
        ),
    }
    if q.data == "help:close":
        return await q.message.delete()
    text, keyboard = pages.get(q.data, pages["help:home"])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


def register(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(help_callback, pattern=r"^help:"))
