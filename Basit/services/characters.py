"""Character database helpers."""
import random
import re
from ..config import RARITIES, normalize_rarity, rarity_meta
from ..database import characters, user_collection, add_character


def image_of(char):
    return char.get("img_url") or char.get("image_url") or char.get("file_id")


def key(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()


def name_matches(name, query):
    parts = key(query).split()
    target = key(name)
    return bool(parts) and all(part in target for part in parts)


async def by_id(value):
    value = str(value).strip()
    item = await characters.find_one({"id": value})
    if item:
        return item
    return await characters.find_one({"id": int(value)}) if value.isdigit() else None


async def random_character(allowed=None):
    allowed = {normalize_rarity(x) for x in allowed} if allowed else set(RARITIES)
    all_chars = await characters.find({}).to_list(length=20000)
    pool = [c for c in all_chars if image_of(c) and normalize_rarity(str(c.get("rarity", ""))) in allowed]
    if not pool:
        return None
    rarity_keys = sorted({normalize_rarity(str(c.get("rarity", ""))) for c in pool})
    rarity = random.choices(rarity_keys, weights=[RARITIES.get(k, {}).get("weight", 1) for k in rarity_keys])[0]
    chosen = random.choice([c for c in pool if normalize_rarity(str(c.get("rarity", ""))) == rarity])
    if not chosen.get("price"):
        low, high = rarity_meta(rarity)["price"]
        chosen["price"] = random.randint(low, high)
        await characters.update_one({"_id": chosen["_id"]}, {"$set": {"price": chosen["price"]}})
    return chosen


async def enabled_spawn_rarities():
    from ..database import settings_collection
    doc = await settings_collection.find_one({"_id": "spawn_rarities"}) or {}
    enabled = doc.get("enabled", {})
    return [r for r in RARITIES if enabled.get(r, True)]


async def grant(user_id, character, source):
    await add_character(user_id, character, source)


def details(char):
    meta = rarity_meta(str(char.get("rarity", "")))
    return (
        "🌐 CHARACTER INFO\n┏━━━━━━━━━━━━━⧫\n"
        f'✧ NAME: {char.get("name", "Unknown")} [{meta["emoji"]}]\n'
        f'✧ RARITY: {meta["name"]} {meta["emoji"]}\n'
        f'✧ ANIME: {char.get("anime", "Unknown")}\n'
        f'✧ PRICE: {int(char.get("price", 0) or 0):,}\n'
        f'✧ ID: {char.get("id", "—")}\n┗━━━━━━━━━━━━━⧫'
    )


def image_caption(user, char, title="CONGRATULATIONS"):
    meta = rarity_meta(str(char.get("rarity", "")))
    return (
        f"{title} {user.first_name}! YOU WON 🔥\n"
        f'◈ NAME: {char.get("name", "Unknown")}\n'
        f'◈ RARITY: {meta["emoji"]} {meta["name"]}\n'
        f'◈ ANIME: {char.get("anime", "Unknown")}\n'
        f'◈ ID: {char.get("id", "—")}\n'
        "Check your /collection now."
    )
