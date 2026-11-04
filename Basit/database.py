"""MongoDB access layer; legacy WaifuXprime collections remain canonical."""
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from .config import load_settings

settings = load_settings()
client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=10000)
db = client[settings.mongo_db_name]

# Original collections are retained to preserve existing character/user data.
characters = db["anime_characters_lol"]
user_collection = db["user_collection_lmaoooo"]
user_totals = db["user_totals_lmaoooo"]
group_user_totals = db["group_user_totalsssssss"]
top_global_groups = db["top_global_groups"]
pm_users = db["total_pm_users"]

# New durable state collections.
groups = db["waifu_group_state"]
group_members = db["waifu_group_members"]
settings_collection = db["waifu_settings"]
transactions = db["waifu_transactions"]
marketplaces = db["waifu_marketplaces"]
auctions = db["waifu_auctions"]
pending_actions = db["waifu_pending_actions"]
codes = db["waifu_redeem_codes"]
audit_logs = db["waifu_audit_logs"]
sequences = db["waifu_sequences"]
mines_games = db["waifu_mines_games"]
claim_events = db["waifu_claim_events"]
user_claims = db["waifu_user_claims"]
legacy_migrations = db["waifu_legacy_migrations"]
chat_filters = db["waifu_chat_filters"]


async def initialize_database():
    await client.admin.command("ping")
    await characters.create_index("id", unique=True, sparse=True)
    await characters.create_index([("name", ASCENDING)])
    await characters.create_index([("anime", ASCENDING)])
    await characters.create_index("rarity")
    await user_collection.create_index("id", unique=True)
    await user_collection.create_index("characters.id")
    await group_members.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
    await auctions.create_index([("status", ASCENDING), ("ends_at", ASCENDING)])
    # Do not TTL-delete pending payments: expired reservations must be refunded.
    try:
        await pending_actions.drop_index("expires_at_1")
    except Exception:
        pass
    await pending_actions.create_index("expires_at")
    await transactions.create_index("transaction_id", unique=True)
    await codes.create_index("code", unique=True)
    await user_claims.create_index([("event_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
    await mines_games.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
    await chat_filters.create_index([("chat_id", ASCENDING), ("keyword", ASCENDING)], unique=True)


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
         "$push": {"characters": owned}, "$inc": {"total_collected": 1}},
        upsert=True,
    )


async def debit_balance(user_id, currency, amount):
    if int(amount) <= 0:
        return False
    field = "cash" if currency == "cash" else "diamonds"
    result = await user_collection.update_one(
        {"id": int(user_id), field: {"$gte": int(amount)}},
        {"$inc": {field: -int(amount)}},
    )
    return result.modified_count == 1


async def credit_balance(user_id, currency, amount):
    if int(amount) < 0:
        raise ValueError("Credit amount cannot be negative")
    field = "cash" if currency == "cash" else "diamonds"
    await user_collection.update_one(
        {"id": int(user_id)},
        {"$inc": {field: int(amount)},
         "$setOnInsert": {"id": int(user_id), "characters": [], "favorites": []}},
        upsert=True,
    )


async def adjust_balance(user_id, cash_delta=0, diamond_delta=0):
    """Compatibility helper; negative deltas cannot overdraw."""
    if cash_delta < 0 and not await debit_balance(user_id, "cash", -cash_delta):
        return False
    if diamond_delta < 0 and not await debit_balance(user_id, "diamonds", -diamond_delta):
        if cash_delta < 0:
            await credit_balance(user_id, "cash", -cash_delta)
        return False
    if cash_delta > 0:
        await credit_balance(user_id, "cash", cash_delta)
    if diamond_delta > 0:
        await credit_balance(user_id, "diamonds", diamond_delta)
    return True


async def credit_balance_once(user_id, currency, amount, transaction_id):
    """Atomically credit a wallet at most once for a transaction identifier."""
    amount = int(amount)
    if amount < 0:
        raise ValueError("Credit amount cannot be negative")
    field = "cash" if currency == "cash" else "diamonds"
    await get_user(user_id)
    result = await user_collection.update_one(
        {"id": int(user_id), "processed_transactions": {"$ne": str(transaction_id)}},
        {"$inc": {field: amount}, "$addToSet": {"processed_transactions": str(transaction_id)}},
    )
    return result.modified_count == 1
