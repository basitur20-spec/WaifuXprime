"""Application factory and lifecycle for WaifuX Prime."""
from __future__ import annotations

import logging
from telegram import BotCommand
from telegram.ext import Application, ContextTypes

from .config import load_settings
from .database import initialize_database, close_database

settings = load_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("waifuxprime")


async def post_init(application: Application):
    await initialize_database()
    commands = [
        ("start", "Start the bot"), ("hunt", "Claim a spawned waifu"),
        ("waifu", "Claim a waifu every 12 hours"), ("marriage", "Try your luck at marriage"),
        ("propose", "Propose to a waifu"), ("fav", "Set an owned waifu as favorite"),
        ("collection", "View your collection"), ("dmode", "Change collection display mode"),
        ("f", "View a waifu by ID"), ("find", "Find users who own a waifu"),
        ("dfind", "Search waifus by name"), ("balance", "Check Cash and Diamonds"),
        ("bonus", "Open daily and weekly bonuses"), ("explore", "Explore and earn rewards"),
        ("pay", "Transfer Cash to another user"), ("sell", "Sell waifus or diamonds"),
        ("marketplace", "Open the daily marketplace"), ("auction", "Create an auction (staff)"),
        ("bid", "Place a bid in an auction"), ("gift", "Gift one of your waifus"),
        ("trade", "Trade waifus with another user"), ("changetime", "Change group spawn interval"),
        ("spawn", "Toggle rarity spawns (owner)"), ("upload", "Upload a waifu (staff)"),
        ("dupload", "Delete an uploaded waifu (staff)"),
    ]
    await application.bot.set_my_commands([BotCommand(command, description) for command, description in commands])
    from .handlers.marketplace import schedule_open_auctions
    if application.job_queue:
        await schedule_open_auctions(application)
    LOGGER.info("Connected to MongoDB; bot identity: @%s", application.bot.username)


async def post_shutdown(application: Application):
    await close_database()
    LOGGER.info("Database connection closed.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    LOGGER.exception("Unhandled update error", exc_info=context.error)
    message = getattr(update, "effective_message", None)
    if message:
        try:
            await message.reply_text("⚠️ Something went wrong. Please try again.")
        except Exception:
            pass


def build_application() -> Application:
    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    from .handlers import start, gameplay, collection, lookup, economy, inline, admin, marketplace, transfers, games, codes, claims, profiles, maintenance
    maintenance.register(application)  # Early command guard runs in handler group -1.\n    start.register(application)
    gameplay.register(application)
    collection.register(application)
    lookup.register(application)
    economy.register(application)
    inline.register(application)
    admin.register(application)
    marketplace.register(application)
    transfers.register(application)
    games.register(application)
    codes.register(application)
    claims.register(application)
    profiles.register(application)
    application.add_error_handler(error_handler)
    return application


def main():
    LOGGER.info("Starting WaifuX Prime...")
    build_application().run_polling(
        allowed_updates=["message", "callback_query", "inline_query", "my_chat_member", "chat_member"],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
