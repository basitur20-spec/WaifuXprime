from telegram.ext import Application

from config import TOKEN

from handlers import (
    start,
    help,
    hunt,
    waifu,
    collection,
    profile,
    economy,
    redeem,
    market,
    search,
    games,
    admin,
    upload,
    filters,
    leaderboard,
)

def main():
    app = Application.builder().token(TOKEN).build()

    start.register(app)
    help.register(app)
    hunt.register(app)
    waifu.register(app)
    collection.register(app)
    profile.register(app)
    economy.register(app)
    redeem.register(app)
    market.register(app)
    search.register(app)
    games.register(app)
    admin.register(app)
    upload.register(app)
    filters.register(app)
    leaderboard.register(app)

    print("✅ Bot Started Successfully...")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
