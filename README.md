# WaifuX Prime

Modular Telegram waifu collector bot.

## Runtime
- Python 3.11
- python-telegram-bot 21.6
- MongoDB (Motor 3.6 / PyMongo 4.9)
- Start: `python -m Basit`

## Configuration
Copy `.env.example` to `.env`, or set the same names in Replit Secrets / deployment environment variables. Required: `BOT_TOKEN`, `MONGO_URI`, and `OWNER_ID`. Configure `SUDO_USERS`, `UPLOADER_USERS`, `CHARA_CHANNEL_ID`, `BOT_USERNAME`, `SUPPORT_CHAT`, `UPDATE_CHAT`, and group spawn settings as needed.

Never commit `.env`, tokens, database URIs/passwords, or API secrets.

## Security action required
The old public `main` branch contained hard-coded Telegram and MongoDB credentials in `Basit/config.py`. Treat them as compromised: revoke/regenerate the bot token with BotFather, change the MongoDB database-user password, review/revoke the exposed Telegram API hash, and store replacements only in deployment secrets. This refactor branch sanitizes the config, but it does not erase secrets from old Git history or secure a deployment still using old credentials.

## Data migration
The previous prototype used MongoDB, SQLite, and JSON files. Back up MongoDB and the old `data/` directory before switching. Run `python -m Basit.migrate_legacy --dry-run` to preview; review before running `python -m Basit.migrate_legacy`. Original source files are never deleted. Migration must be run explicitly, not at startup.

## Implemented refactor modules
Start/help; group spawn/hunt/rarity control; daily waifu, marriage and propose; collection/favorite/display modes/inline; character lookup/search; balance, explore, bonus, payment; marketplace, auctions and bids; selling, gifting and trading; uploader/dupload/staff give; dice games and Mines; profile/setprofile; redeem codes and event claims; owner maintenance controls; group text filters and owner broadcast.

Files under `Basit/modules/` are retained as legacy reference and are not loaded by the new entry point. They contain imports tied to the old mismatched `shivu` package and should not be loaded alongside the new handlers.

## Operational notes
- Group spam mute needs permission to restrict members.
- `/upload` takes a photo plus `/upload Name | Rarity | Anime | OptionalPrice`.
- `/propose` media must be configured as direct image URLs or Telegram file IDs; a Telegram post URL is not itself a direct photo.
- Inline keyboards cannot contain a free-text field, so auction bids use `/bid <amount>` in reply to the bot's bid prompt.
- Rarity chance values from the supplied spec total more than 100; spawn selection treats them as relative weights and normalizes them.

## Run
`python -m pip install -r requirements.txt`
`python -m compileall -q Basit main.py`
`python -m Basit`

A live run requires valid environment variables and reachable MongoDB. Test the migration and command flows against a database backup before production deployment.
