"""One-time legacy SQLite/JSON importer. It never deletes the source files."""
import argparse
import asyncio
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from .database import user_collection, characters, initialize_database, close_database


def legacy_bonus_fields(record):
    """Map the source Replit users.json bonus timestamps into live Mongo fields."""
    fields = {}
    legacy = {}
    for key in ("daily", "weekly", "streak", "highest_streak", "last_claim"):
        if key in record:
            legacy[key] = record[key]

    def unix_datetime(value):
        try:
            timestamp = float(value)
            if timestamp <= 0:
                return None
            return datetime.utcfromtimestamp(timestamp)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    daily_last = unix_datetime(record.get("daily"))
    weekly_last = unix_datetime(record.get("weekly"))
    last_claim = unix_datetime(record.get("last_claim"))

    if daily_last:
        fields["daily_bonus_next_at"] = daily_last + timedelta(days=1)
    if weekly_last:
        fields["weekly_bonus_next_at"] = weekly_last + timedelta(days=7)
    if last_claim:
        fields["last_daily_claim_at"] = last_claim

    try:
        fields["daily_streak"] = max(0, int(record.get("streak", 0) or 0))
    except (TypeError, ValueError):
        fields["daily_streak"] = 0
    try:
        fields["highest_streak"] = max(
            fields["daily_streak"], int(record.get("highest_streak", 0) or 0)
        )
    except (TypeError, ValueError):
        fields["highest_streak"] = fields["daily_streak"]

    if legacy:
        fields["legacy_bonus"] = legacy
    return fields


async def ensure_user(uid):
    await user_collection.update_one(
        {"id": int(uid)},
        {"$setOnInsert": {"id": int(uid), "characters": [], "favorites": [], "cash": 0, "diamonds": 0}},
        upsert=True,
    )


async def import_character(uid, key, item, favorite=False, dry_run=False):
    if dry_run:
        return
    await ensure_user(uid)
    existing = await user_collection.find_one({"id": uid, "characters.legacy_key": key}, {"_id": 1})
    if not existing:
        item = dict(item)
        item["legacy_key"] = key
        await user_collection.update_one({"id": uid}, {"$push": {"characters": item}})
    if favorite:
        await user_collection.update_one({"id": uid}, {"$addToSet": {"favorites": str(item.get("id"))}})


async def run(args):
    await initialize_database()
    try:
        balance_path = Path(args.balance_db)
        if balance_path.exists():
            try:
                conn = sqlite3.connect(str(balance_path))
                rows = conn.execute("SELECT user_id, cash, diamonds FROM user_balance").fetchall()
                conn.close()
            except sqlite3.Error as exc:
                print(f"Balance DB skipped: {exc}")
                rows = []
            for uid, cash, diamonds in rows:
                if not args.dry_run:
                    await ensure_user(uid)
                    await user_collection.update_one(
                        {"id": int(uid), "legacy_balance_migrated_at": {"$exists": False}},
                        {"$set": {
                            "cash": int(cash or 0),
                            "diamonds": int(diamonds or 0),
                            "legacy_balance_migrated_at": datetime.utcnow(),
                        }},
                    )
            print(f"Balance rows: {len(rows)}")
        else:
            print(f"No balance DB at {balance_path}")

        collection_path = Path(args.collection_db)
        if collection_path.exists():
            try:
                conn = sqlite3.connect(str(collection_path))
                rows = conn.execute(
                    "SELECT rowid, user_id, username, waifu_id, name, anime, rarity, image, favorite FROM collections"
                ).fetchall()
                conn.close()
            except sqlite3.Error as exc:
                print(f"Collection DB skipped: {exc}")
                rows = []
            for rowid, uid, username, wid, name, anime, rarity, image, favorite in rows:
                await import_character(int(uid), f"sqlite:{rowid}", {
                    "id": str(wid), "name": name or "Unknown", "anime": anime or "Unknown",
                    "rarity": rarity or "Unknown", "img_url": image, "price": 0,
                    "source": "legacy_sqlite",
                }, bool(favorite), args.dry_run)
                if username and not args.dry_run:
                    await user_collection.update_one(
                        {"id": int(uid), "username": {"$exists": False}},
                        {"$set": {"username": username}},
                    )
            print(f"SQLite collection rows: {len(rows)}")
        else:
            print(f"No collection DB at {collection_path}")

        json_path = Path(args.users_json)
        if json_path.exists():
            try:
                users = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"users.json skipped: {exc}")
                users = {}
            total = 0
            if isinstance(users, dict):
                for uid_raw, record in users.items():
                    try:
                        uid = int(uid_raw)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    for index, raw_id in enumerate(record.get("collection", []) if isinstance(record.get("collection", []), list) else []):
                        wid = str(raw_id.get("id")) if isinstance(raw_id, dict) else str(raw_id)
                        char = None
                        if not args.dry_run:
                            char = await characters.find_one({"id": wid})
                            if not char and wid.isdigit():
                                char = await characters.find_one({"id": int(wid)})
                        if char:
                            await import_character(uid, f"json:{uid}:{index}:{wid}", {
                                "id": str(char.get("id")), "name": char.get("name", "Unknown"),
                                "anime": char.get("anime", "Unknown"), "rarity": char.get("rarity", "Unknown"),
                                "img_url": char.get("img_url"), "price": int(char.get("price", 0) or 0),
                                "source": "legacy_users_json",
                            }, False, args.dry_run)
                        total += 1
                    if not args.dry_run:
                        await ensure_user(uid)
                        await ensure_user(uid)
                        meta = legacy_bonus_fields(record)
                        if meta:
                            # Preserve the source values for audit, while also mapping
                            # cooldowns/streaks into fields used by the live bonus handler.
                            await user_collection.update_one(
                                {"id": uid}, {"$set": meta}
                            )
            print(f"users.json collection references: {total}")
        else:
            print(f"No users.json at {json_path}")

        print("Dry-run finished." if args.dry_run else "Migration finished.")
        print("The old SQLite and JSON files were not changed or deleted.")
    finally:
        await close_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--balance-db", default=os.getenv("LEGACY_BALANCE_DB", "data/balance_system.db"))
    parser.add_argument("--collection-db", default=os.getenv("LEGACY_COLLECTION_DB", "data/collection.db"))
    parser.add_argument("--users-json", default=os.getenv("LEGACY_USERS_JSON", "data/users.json"))
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(run(parser.parse_args()))
