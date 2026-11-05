# WaifuXprime

Modular Telegram waifu collection bot built from the supplied main.py and feature notes.

## Run on Replit
1. Import this repository into the existing Replit project.
2. Add BOT_TOKEN in Replit Secrets. Set OWNER_ID to the Telegram numeric ID for the project owner.
3. Press Run. The configured command is python main.py.

## Local setup
Python 3.11 is recommended. Install requirements.txt, set BOT_TOKEN, then run python main.py.

## Data safety
The bot opens data/balance_system.db and data/collection.db in place and imports catalog data from data/bot_data.json. Existing collection and balance rows are retained. New tables live in data/waifuxprime.db. Back up data/ before deploying. Never commit .env or secrets.

## Modules
- waifux/handlers/ contains command groups for economy, characters, collection, games, admin, marketplace, profiles, and misc.
- waifux/database.py contains schema, compatibility, and data access.
- waifux/config.py contains environment configuration.

## Commands
Core: /start /help /balance /explore /bonus /pay
Characters: /spawn /hunt /waifu /marriage /propose /upload /dupload /changetime
Collection: /collection /fav /dmode
Economy: /gen /dgen /redeem /dredeem /sell
Games: /roll /darts /goal /mines /dtops
Admin: /addsudo /rsudo /sudolist /adduploader /ruploader /uploaderlist /give /stop /run /send /claim
Marketplace: /market /buy /auction /bid
Other: /find /dfind /gift /trade /gclaim /filter /stopfilter /profile /setprofile

Only set BOT_TOKEN in Replit Secrets. This project does not modify Replit Secrets through code.
