# WaifuX Prime

Professional modular Telegram waifu/character collector bot.

## Project layout
- Basit/main.py: application startup and handler registration.
- Basit/config.py: environment configuration and rarity catalogue.
- Basit/database.py: MongoDB access and wallet helpers.
- Basit/handlers/: Telegram commands and callback handlers grouped by feature.
- Basit/services/: character selection and matching helpers.
- Basit/migrate_legacy.py: optional import of legacy SQLite/JSON data.
- tests/: core unit checks; GitHub Actions runs compile and tests.

## Run
Use Python 3.11. Install requirements with `python -m pip install -r requirements.txt`, configure Replit Secrets, then run `python -m Basit` (or `python main.py`).

Required environment variables: BOT_TOKEN, MONGO_URI and OWNER_ID.
Optional variables: MONGO_DB_NAME, SUDO_USERS, UPLOADER_USERS, GROUP_ID, CHARA_CHANNEL_ID, BOT_USERNAME, SUPPORT_CHAT, UPDATE_CHAT, SPAWN_EVERY_MESSAGES, SPAWN_LIFETIME_SECONDS and PROPOSE_*_IMAGE.

Never commit .env, bot tokens or database credentials.

## Main features
Start/help; group spawn and hunt; daily waifu, marriage and proposal; favorites, collection, inline and lookup; Cash and Diamonds; streak bonuses and exploration; confirmed Cash/Diamond payments; marketplace, auctions and bids; sell, gift and trade; upload/delete; SUDO/uploader roles; code generation/redemption; event claims; dice and Mines; profiles; owner maintenance; group filters; owner broadcast; global leaderboards.

## Legacy data migration
The app retains the original MongoDB collection names. The supplied Replit source also used data/balance_system.db, data/collection.db and data/users.json. Back up MongoDB and the old data folder, copy legacy SQLite/JSON files to data/, run `python -m Basit.migrate_legacy --dry-run`, review the output, then run `python -m Basit.migrate_legacy` once. The importer does not delete source data.

## Security
The old public main branch contained hard-coded Telegram and MongoDB credentials in Basit/config.py. Treat them as compromised: regenerate the bot token and rotate the MongoDB database-user password. Use Replit Secrets only. Removing old files from the current tree does not erase credentials from Git history.

## Deployment notes
The bot needs permission to restrict group members for spam mutes and to post in CHARA_CHANNEL_ID when configured. Auction bids use /bid <amount> in reply to the bot's prompt. Validate commands in a test group and a database backup before production.