"""MongoDB access layer; legacy WaifuXprime collections remain canonical."""
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from .config import load_settings

settings = load_settings()
client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=10000)
db = client[settings.mongo_db_name]

# Existing collections from the original bot; do not rename or migrate them.
characters = db["anime_characters_lol"]
user_collection = db["user_collection_lmaoooo"]
user_totals = db["user_totals_lmaoooo"]
group_user_totals = db["group_user_totalsssssss"]
top_global_groups = db["top_global_groups"]
pm_users = db["total_pm_users"]

# Durable collections for new systems.
groups = db["waifu_group_state"]
settings_collection = db["waifu_settings"]
transactions = db["waifu_transactions"]
marketplaces = db["waifu_marketplaces"]
auctions = db["waifu_auctions"]
pending_actions = db["waifu_pending_actions"]
codes = db["waifu_redeem_codes"]
audit_logs = db["waifu_audit_logs"]


async def initialize_database():
    await client.admin.command("ping")
    await characters.create_index("id", unique=True, sparse=True)
    await characters.create_index([("name", ASCENDING)])
    await characters.create_index([("anime", ASCENDING)])
    await user_collection.create_index("id", unique=True)
    await user_collection.create_index("characters.id")
    await groups.create_index("_id", unique=True)
    await auctions.create_index([("status", ASCENDING), ("ends_at", ASCENDING)])
    await pending_actions.create_index("expires_at", expireAfterSeconds=0)
    await transactions.create_index("transaction_id", unique=True)
    await codes.create_index("code", unique=True)
    await audit_logs.create_index([("created_at", DESCENDING)])


async def close_database():
    client.close()


async def get_user(user_id, telegram_user=None):
    fields = {}
    if telegram_user is not None:
        fields = {"first_name": telegram_user.first_name or "User", "username": telegram_user.username}
    await user_collection.update_one(
        {"id": int(user_id)},
        {"$set": fields, "$setOnInsert": {
            "id": int(user_id), "characters": [], "favorites": [],
            "cash": 0, "diamonds": 0, "created_at": datetime.utcnow()
        }},
        upsert=True,
    )
    return await user_collection.find_one({"id": int(user_id)})


async def add_character(user_id, character, source):
    owned = {
        "id": str(character.get("id", "")),
        "name": character.get("name", "Unknown"),
        "anime": character.get("anime", "Unknown"),
        "rarity": character.get("rarity", "Unknown"),
        "img_url": character.get("img_url") or character.get("image_url") or character.get("file_id"),
        "price": int(character.get("price", 0) or 0),
        "source": source,
    }
    await user_collection.update_one(
        {"id": int(user_id)},
        {"$setOnInsert": {"id": int(user_id), "cash": 0, "diamonds": 0, "favorites": []},
         "$push": {"characters": owned}},
        upsert=True,
    )


async def debit_balance(user_id, currency, amount):
    field = "cash" if currency == "cash" else "diamonds"
    result = await user_collection.update_one(
        {"id": int(user_id), field: {"$gte": int(amount)}},
        {"$inc": {field: -int(amount)}},
    )
    return result.modified_count == 1


async def credit_balance(user_id, currency, amount):
    field = "cash" if currency == "cash" else "diamonds"
    await user_collection.update_one(
        {"id": int(user_id)},
        {"$inc": {field: int(amount)},
         "$setOnInsert": {"id": int(user_id), "characters": [], "favorites": []}},
        upsert=True,
    )
