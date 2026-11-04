"""User-to-user waifu transfers and confirmed waifu/diamond selling."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..config import RARITIES, load_settings, normalize_rarity, rarity_meta
from ..database import (
    user_collection, pending_actions, settings_collection, audit_logs,
    get_user, add_character, debit_balance, credit_balance,
)
from ..services.characters import by_id, image_of

settings = load_settings()


def _date():
    return datetime.utcnow().strftime("%Y-%m-%d")


def _cash(value):
    return f"{int(value):,}"


def _remaining(next_at):
    seconds = max(0, int((next_at - datetime.utcnow()).total_seconds()))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


async def _remove_one(user_id: int, wid: str):
    """Atomically remove exactly one owned copy using a MongoDB aggregation update."""
    reducer = {
        "$reduce": {
            "input": "$characters",
            "initialValue": {"removed": False, "items": []},
            "in": {
                "$cond": [
                    {"$and": [{"$eq": ["$$this.id", str(wid)]}, {"$eq": ["$$value.removed", False]}]},
                    {"removed": True, "items": "$$value.items"},
                    {"removed": "$$value.removed", "items": {"$concatArrays": ["$$value.items", ["$$this"]]}},
                ]
            },
        }
    }
    result = await user_collection.update_one(
        {"id": int(user_id), "characters.id": str(wid)},
        [{"$set": {"characters": {"$let": {"vars": {"state": reducer}, "in": "$$state.items"}}}}],
    )
    if result.modified_count:
        await user_collection.update_one({"id": int(user_id)}, {"$pull": {"favorites": str(wid)}})
        return True
    return False


async def gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    sender = update.effective_user
    if not msg.reply_to_message:
        return await msg.reply_text("Reply to the receiver's message and use /gift <waifu_id>.")
    if len(context.args) != 1:
        return await msg.reply_text("Usage: reply to a user and type /gift <waifu_id>.")
    receiver = msg.reply_to_message.from_user
    if receiver.is_bot or receiver.id == sender.id:
        return await msg.reply_text("❌ Choose another real user to receive the waifu.")
    wid = str(context.args[0])
    sender_doc = await user_collection.find_one({"id": sender.id, "characters.id": wid})
    if not sender_doc:
        return await msg.reply_text("❌ You do not own this waifu. Check /collection.")
    char = next((x for x in sender_doc.get("characters", []) if str(x.get("id")) == wid), None)
    if not char:
        return await msg.reply_text("❌ Waifu was not found in your collection.")
    token = uuid.uuid4().hex
    await pending_actions.insert_one({
        "_id": token, "type": "user_gift", "status": "pending",
        "sender_id": sender.id, "receiver_id": receiver.id,
        "receiver_name": receiver.full_name, "waifu_id": wid, "character": char,
        "expires_at": datetime.utcnow() + timedelta(minutes=1),
    })
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"transfer:gift:{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"transfer:cancel:{token}"),
    ]])
    await msg.reply_text(
        f"🎁 GIFT CONFIRMATION\n\nDo you really want to gift this waifu?\n"
        f"🎭 Character ID: {wid}\n👤 Receiver: {receiver.full_name}\n"
        "⏳ This request expires in 1 minute.\n⚠️ This action cannot be undone.",
        reply_markup=markup,
    )


async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    sender = update.effective_user
    if not msg.reply_to_message:
        return await msg.reply_text("Reply to another user and use /trade <your_waifu_id> <their_waifu_id>.")
    if len(context.args) != 2:
        return await msg.reply_text("Usage: reply to a user and type /trade <your_waifu_id> <their_waifu_id>.")
    receiver = msg.reply_to_message.from_user
    if receiver.is_bot or receiver.id == sender.id:
        return await msg.reply_text("❌ Choose another real user to trade with.")
    own_id, other_id = map(str, context.args)
    sender_doc = await user_collection.find_one({"id": sender.id, "characters.id": own_id})
    receiver_doc = await user_collection.find_one({"id": receiver.id, "characters.id": other_id})
    if not sender_doc or not receiver_doc:
        return await msg.reply_text("❌ Both users must own the waifus specified in the trade.")
    own_char = next((x for x in sender_doc.get("characters", []) if str(x.get("id")) == own_id), None)
    other_char = next((x for x in receiver_doc.get("characters", []) if str(x.get("id")) == other_id), None)
    if not own_char or not other_char:
        return await msg.reply_text("❌ A waifu could not be found.")
    token = uuid.uuid4().hex
    await pending_actions.insert_one({
        "_id": token, "type": "user_trade", "status": "pending",
        "sender_id": sender.id, "receiver_id": receiver.id,
        "own_id": own_id, "other_id": other_id,
        "own_char": own_char, "other_char": other_char,
        "expires_at": datetime.utcnow() + timedelta(minutes=2),
    })
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm Trade", callback_data=f"transfer:trade:{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"transfer:cancel:{token}"),
    ]])
    await msg.reply_text(
        f"🔄 TRADE CONFIRMATION\n\nYou give: {own_char.get('name')} (ID {own_id})\n"
        f"You receive: {other_char.get('name')} (ID {other_id})\n"
        f"Trading with: {receiver.full_name}\n\nConfirm the exchange?",
        reply_markup=markup,
    )


async def transfer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, token = q.data.split(":")
    pending = await pending_actions.find_one({"_id": token, "status": "pending"})
    if not pending:
        return await q.answer("This request has expired or is already processed.", show_alert=True)
    if q.from_user.id != pending["sender_id"]:
        return await q.answer("Only the request sender can confirm this action.", show_alert=True)
    await q.answer()
    if action == "cancel":
        await pending_actions.update_one({"_id": token, "status": "pending"}, {"$set": {"status": "cancelled"}})
        return await q.edit_message_text("❌ Request cancelled. No changes were made.")
    locked = await pending_actions.find_one_and_update(
        {"_id": token, "status": "pending"},
        {"$set": {"status": "processing"}},
        return_document=ReturnDocument.BEFORE,
    )
    if not locked:
        return await q.edit_message_text("This request has already been processed.")
    sender_id, receiver_id = locked["sender_id"], locked["receiver_id"]
    if locked["type"] == "user_gift":
        removed = await _remove_one(sender_id, locked["waifu_id"])
        if not removed:
            await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
            return await q.edit_message_text("❌ Gift failed. The waifu is no longer in your collection.")
        try:
            await get_user(receiver_id)
            await add_character(receiver_id, locked["character"], "gift")
        except Exception:
            await add_character(sender_id, locked["character"], "gift_refund")
            await pending_actions.update_one({"_id": token}, {"$set": {"status": "refunded"}})
            return await q.edit_message_text("❌ Gift failed. The waifu has been returned to your collection.")
        await audit_logs.insert_one({
            "action": "gift", "sender_id": sender_id, "receiver_id": receiver_id,
            "waifu_id": locked["waifu_id"], "created_at": datetime.utcnow(),
        })
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "completed"}})
        await q.edit_message_text(
            f"🎉 GIFT SUCCESSFUL!\nCharacter ID: {locked['waifu_id']}\n"
            f"{locked['receiver_name']} received the waifu.\n💖 Enjoy sharing your collection!"
        )
        try:
            await context.bot.send_photo(
                chat_id=receiver_id, photo=image_of(locked["character"]),
                caption=f"🎁 YOU RECEIVED A WAIFU!\n{locked['character'].get('name')}\nAdded to your /collection 💖",
            )
        except Exception:
            pass
        return

    removed_own = await _remove_one(sender_id, locked["own_id"])
    if not removed_own:
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.edit_message_text("❌ Trade failed. Your waifu is no longer in your collection.")
    removed_other = await _remove_one(receiver_id, locked["other_id"])
    if not removed_other:
        await add_character(sender_id, locked["own_char"], "trade_refund")
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "refunded"}})
        return await q.edit_message_text("❌ Trade failed because the other waifu is no longer available. Your waifu was returned.")
    try:
        await add_character(sender_id, locked["other_char"], "trade")
        await add_character(receiver_id, locked["own_char"], "trade")
    except Exception:
        await add_character(sender_id, locked["own_char"], "trade_refund")
        await add_character(receiver_id, locked["other_char"], "trade_refund")
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "refunded"}})
        return await q.edit_message_text("❌ Trade failed. Both waifus were returned.")
    await audit_logs.insert_one({
        "action": "trade", "sender_id": sender_id, "receiver_id": receiver_id,
        "sender_waifu_id": locked["own_id"], "receiver_waifu_id": locked["other_id"],
        "created_at": datetime.utcnow(),
    })
    await pending_actions.update_one({"_id": token}, {"$set": {"status": "completed"}})
    await q.edit_message_text("✅ TRADE SUCCESSFUL! Both collections have been updated.")
    for target_id, char in [(sender_id, locked["other_char"]), (receiver_id, locked["own_char"])]:
        try:
            await context.bot.send_photo(
                chat_id=target_id, photo=image_of(char),
                caption=f"🔄 TRADE COMPLETED!\nReceived: {char.get('name')}\nAdded to your /collection.",
            )
        except Exception:
            pass


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    args = context.args
    if len(args) == 2 and uid == settings.owner_id:
        rarity = normalize_rarity(args[0])
        raw = args[1].rstrip("%")
        if rarity in RARITIES:
            if not raw.isdigit() or not 1 <= int(raw) <= 100:
                return await msg.reply_text("Usage: /sell <rarity> <percentage 1-100>")
            await settings_collection.update_one({"_id": "sell_rates"}, {"$set": {rarity: int(raw)}}, upsert=True)
            return await msg.reply_text(f"👑 SELL RATE UPDATED\nRarity: {RARITIES[rarity]['name']}\nNew Sell Rate: {raw}%")
    if not args:
        return await msg.reply_text("Usage: /sell <waifu_id> [other_ids...] or /sell diamonds <amount>")
    user = await get_user(uid, update.effective_user)
    next_at = user.get("sell_next_at")
    if next_at and next_at > datetime.utcnow():
        return await msg.reply_text(f"⏳ Cooldown active. Wait {_remaining(next_at)}.")
    today = _date()
    if args[0].casefold() in {"diamond", "diamonds", "dimond", "dimonds"}:
        if len(args) != 2 or not args[1].replace(",", "").isdigit():
            return await msg.reply_text("Usage: /sell diamonds <amount>")
        amount = int(args[1].replace(",", ""))
        if amount <= 0 or int(user.get("diamonds", 0)) < amount:
            return await msg.reply_text("❌ Not enough Diamonds 💎 to sell.")
        token = uuid.uuid4().hex
        cash = amount * 10000
        await pending_actions.insert_one({
            "_id": token, "type": "sell_diamonds", "status": "pending",
            "user_id": uid, "diamonds": amount, "cash": cash,
            "expires_at": datetime.utcnow() + timedelta(minutes=2),
        })
        await user_collection.update_one({"id": uid}, {"$set": {"sell_next_at": datetime.utcnow() + timedelta(minutes=1)}})
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data=f"sell:confirm:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"sell:cancel:{token}"),
        ]])
        return await msg.reply_text(
            f"⚠️ CONFIRM DIAMOND SELL\n💎 Diamonds: {amount:,}\n💰 Cash you'll receive: {cash:,}\nProceed?",
            reply_markup=markup,
        )
    if len(args) > 5 or any(not x.isdigit() for x in args):
        return await msg.reply_text("Sell up to 5 waifus at once: /sell 2424 2432 3244")
    current_count = int(user.get("sell_count", 0)) if user.get("sell_day") == today else 0
    if current_count + len(args) > 5:
        return await msg.reply_text("⛔ Daily sell limit reached. You can sell only 5 waifus per day.")
    items = []
    rates = await settings_collection.find_one({"_id": "sell_rates"}) or {}
    for wid in args:
        char = next((x for x in user.get("characters", []) if str(x.get("id")) == wid), None)
        if not char:
            return await msg.reply_text(f"❌ Waifu ID {wid} is not in your collection.")
        rarity = normalize_rarity(str(char.get("rarity", "")))
        meta = rarity_meta(str(char.get("rarity", "")))
        base = int(char.get("price") or __import__("random").randint(*meta["price"]))
        percent = int(rates.get(rarity, 50))
        payout = base * percent // 100
        items.append({
            "id": wid, "name": char.get("name"), "rarity": char.get("rarity"),
            "price": base, "percent": percent, "payout": payout,
        })
    token = uuid.uuid4().hex
    total_cash = sum(x["payout"] for x in items)
    await pending_actions.insert_one({
        "_id": token, "type": "sell_waifus", "status": "pending",
        "user_id": uid, "items": items, "cash": total_cash,
        "expires_at": datetime.utcnow() + timedelta(minutes=2),
    })
    await user_collection.update_one({"id": uid}, {"$set": {"sell_next_at": datetime.utcnow() + timedelta(minutes=1)}})
    text = "⚠️ CONFIRM WAIFU SELL\n\n"
    for item in items:
        text += (
            f'🧩 {item["name"]} | ID {item["id"]}\n🎖 {item["rarity"]} | Base: {_cash(item["price"])}\n'
            f'📉 Sell rate: {item["percent"]}% | You receive: {_cash(item["payout"])}\n\n'
        )
    text += f"💵 TOTAL: {_cash(total_cash)} Cash💸\nThis action is permanent."
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"sell:confirm:{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"sell:cancel:{token}"),
    ]])
    await msg.reply_text(text, reply_markup=markup)


async def sell_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, token = q.data.split(":")
    pending = await pending_actions.find_one({"_id": token, "status": "pending"})
    if not pending:
        return await q.answer("This sell request has expired.", show_alert=True)
    if q.from_user.id != pending["user_id"]:
        return await q.answer("This sell request is not yours.", show_alert=True)
    await q.answer()
    if action == "cancel":
        await pending_actions.update_one({"_id": token, "status": "pending"}, {"$set": {"status": "cancelled"}})
        return await q.edit_message_text("❎ SELL CANCELLED. No changes were made.")
    locked = await pending_actions.find_one_and_update(
        {"_id": token, "status": "pending"},
        {"$set": {"status": "processing"}},
        return_document=ReturnDocument.BEFORE,
    )
    if not locked:
        return await q.edit_message_text("This sell has already been processed.")
    uid = locked["user_id"]
    if locked["type"] == "sell_diamonds":
        if not await debit_balance(uid, "diamonds", int(locked["diamonds"])):
            await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
            return await q.edit_message_text("❌ Not enough Diamonds. No money was changed.")
        await credit_balance(uid, "cash", int(locked["cash"]))
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "completed"}})
        await q.edit_message_text(
            f"💎 DIAMONDS SOLD!\nDiamonds deducted: {locked['diamonds']:,}\nCash added: {_cash(locked['cash'])} Cash💸"
        )
        return
    today = _date()
    user = await user_collection.find_one({"id": uid}) or {}
    sold_count = int(user.get("sell_count", 0)) if user.get("sell_day") == today else 0
    if sold_count + len(locked["items"]) > 5:
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.edit_message_text("⛔ Daily sell limit reached.")
    removed = []
    for item in locked["items"]:
        if await _remove_one(uid, item["id"]):
            removed.append(item)
        else:
            for old in removed:
                char = await by_id(old["id"])
                if char:
                    await add_character(uid, char, "sell_refund")
            await pending_actions.update_one({"_id": token}, {"$set": {"status": "refunded"}})
            return await q.edit_message_text("❌ Sell cancelled because a waifu was no longer owned. Removed waifus were returned.")
    await credit_balance(uid, "cash", int(locked["cash"]))
    await user_collection.update_one(
        {"id": uid},
        [{"$set": {
            "sell_day": today,
            "sell_next_at": datetime.utcnow() + timedelta(minutes=1),
            "sell_count": {"$cond": [{"$eq": ["$sell_day", today]}, {"$add": [{"$ifNull": ["$sell_count", 0]}, len(locked["items"])]}, len(locked["items"])]},
        }}],
    )
    await audit_logs.insert_one({
        "action": "sell_waifus", "user_id": uid, "items": locked["items"],
        "cash": locked["cash"], "created_at": datetime.utcnow(),
    })
    await pending_actions.update_one({"_id": token}, {"$set": {"status": "completed"}})
    await q.edit_message_text(
        f"✅ WAIFU SOLD SUCCESSFULLY!\nItems sold: {len(removed)}\n"
        f"💰 Cash received: {_cash(locked['cash'])} Cash💸\nRemoved from your /collection."
    )


def register(app):
    app.add_handler(CommandHandler("gift", gift))
    app.add_handler(CommandHandler("trade", trade))
    app.add_handler(CommandHandler("sell", sell_command))
    app.add_handler(CallbackQueryHandler(transfer_callback, pattern=r"^transfer:"))
    app.add_handler(CallbackQueryHandler(sell_callback, pattern=r"^sell:"))
