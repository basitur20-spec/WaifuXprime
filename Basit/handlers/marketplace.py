"""Daily marketplace and timed auctions with MongoDB-backed state."""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
)
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..config import RARITIES, load_settings, rarity_meta
from ..database import (
    marketplaces, auctions, pending_actions, user_collection, characters,
    get_user, debit_balance, credit_balance, audit_logs,
)
from ..services.characters import random_character, by_id, image_of, grant

settings = load_settings()
DISCOUNTS = {
    "chibiarc": (6, 11), "winterarc": (5, 10), "royalarc": (6, 15),
    "neonarc": (4, 8), "celestialarc": (6, 13), "mangaarc": (7, 17),
}
MARKET_HIGH = ["royalarc", "neonarc", "celestialarc", "mangaarc"]
MARKET_LOW = ["chibiarc", "winterarc"]


def _now():
    return datetime.utcnow()


def _cash(n):
    return f"{int(n):,}"


def _remaining(ends_at):
    seconds = max(0, int((ends_at - _now()).total_seconds()))
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}h {minutes}m {seconds}s"


def _discount_for(rarity):
    low, high = DISCOUNTS.get(str(rarity).casefold(), (6, 12))
    return random.randint(low, high)


async def _make_offer(previous_ids=None):
    previous_ids = set(previous_ids or [])
    if previous_ids and random.randint(1, 100) <= 5:
        old = await characters.find_one({"id": random.choice(list(previous_ids))})
        if old:
            char = old
        else:
            char = None
    else:
        pool = MARKET_LOW if random.randint(1, 100) <= 30 else MARKET_HIGH
        char = await random_character(pool)
    if not char:
        char = await random_character(MARKET_HIGH + MARKET_LOW)
    if not char:
        return None
    meta = rarity_meta(str(char.get("rarity", "")))
    base_price = int(char.get("price") or random.randint(*meta["price"]))
    discount = _discount_for(str(char.get("rarity", "")))
    sale_price = max(1, int(base_price * (100 - discount) / 100))
    return {
        "waifu_id": str(char.get("id")),
        "name": char.get("name", "Unknown"),
        "anime": char.get("anime", "Unknown"),
        "rarity": char.get("rarity", "Unknown"),
        "img_url": image_of(char),
        "original_price": base_price,
        "discount": discount,
        "sale_price": sale_price,
    }


async def _ensure_market(uid, telegram_user=None):
    await get_user(uid, telegram_user)
    day = _now().strftime("%Y-%m-%d")
    doc = await marketplaces.find_one({"_id": uid})
    if doc and doc.get("day") == day:
        return doc
    previous = doc or {}
    old_ids = [x.get("waifu_id") for x in previous.get("deals", []) if x.get("waifu_id")]
    deals = []
    for _ in range(2):
        offer = await _make_offer(old_ids)
        if offer:
            deals.append(offer)
    # Exclude duplicate waifu IDs within the same daily market.
    seen = set()
    deals = [x for x in deals if not (x["waifu_id"] in seen or seen.add(x["waifu_id"]))]
    await marketplaces.update_one(
        {"_id": uid},
        {"$set": {
            "day": day, "deals": deals, "bought": [],
            "purchases": 0, "refresh_count": 0, "updated_at": _now(),
        }},
        upsert=True,
    )
    return await marketplaces.find_one({"_id": uid}) or {"_id": uid, "day": day, "deals": []}


async def _owned_count(uid, wid):
    doc = await user_collection.find_one({"id": uid}) or {}
    return sum(1 for item in doc.get("characters", []) if str(item.get("id")) == str(wid))


def _market_caption(deal, index, total, owned, sold, purchases):
    meta = rarity_meta(str(deal.get("rarity", "")))
    status = "❎ SOLD" if sold else "🟢 AVAILABLE"
    return (
        "╔══════════════════════╗\n"
        f"       🏪✨ DAILY MARKETPLACE ({purchases}/2) ✨🏪\n"
        "╚══════════════════════╝\n"
        f'🎭 NAME ➤ {deal.get("name", "Unknown")}\n'
        f'📺 ANIME ➤ {deal.get("anime", "Unknown")}\n'
        f'🆔 WAIFU ID ➤ {deal.get("waifu_id")}\n'
        f'💫 RARITY ➤ {meta["emoji"]} {meta["name"]}\n'
        f'💎 ORIGINAL PRICE ➤ {_cash(deal.get("original_price", 0))} Cash💸\n'
        f'💸 SALE PRICE ➤ {_cash(deal.get("sale_price", 0))} Cash💸\n'
        f'📉 DISCOUNT ➤ ⬇️ {deal.get("discount", 0)}% OFF\n'
        f'🟢 STATUS ➤ {status}\n'
        f'🎴 OWNED ➤ {owned}\n'
        f'📑 DEAL ➤ {index + 1}/{total}'
    )


def _market_keyboard(uid, index, total, sold, auction_available):
    buy_label = "❎ SOLD" if sold else "🛒 BUY"
    rows = [
        [
            InlineKeyboardButton("⬅", callback_data=f"market:view:{uid}:{max(0, index-1)}"),
            InlineKeyboardButton(buy_label, callback_data=f"market:buy:{uid}:{index}" if not sold else "market:sold"),
            InlineKeyboardButton("➡", callback_data=f"market:view:{uid}:{min(total-1, index+1)}"),
        ],
        [InlineKeyboardButton("♛ AUCTION", callback_data=f"market:auction:{uid}")],
        [
            InlineKeyboardButton("🔄 Refresh 50,000 Cash", callback_data=f"market:refresh:{uid}"),
            InlineKeyboardButton("CLOSE", callback_data=f"market:close:{uid}"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def marketplace_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    doc = await _ensure_market(uid, update.effective_user)
    deals = doc.get("deals", [])
    if not deals:
        return await update.effective_message.reply_text("🏪 Marketplace has no waifus available today.")
    index = 0
    deal = deals[index]
    owned = await _owned_count(uid, deal["waifu_id"])
    sold = index in doc.get("bought", [])
    active = await auctions.count_documents({"status": "open", "ends_at": {"$gt": _now()}}) > 0
    caption = _market_caption(deal, index, len(deals), owned, sold, int(doc.get("purchases", 0)))
    await update.effective_message.reply_photo(
        deal["img_url"], caption=caption, has_spoiler=True,
        reply_markup=_market_keyboard(uid, index, len(deals), sold, active),
    )


async def _render_market(q, uid, index):
    doc = await _ensure_market(uid)
    deals = doc.get("deals", [])
    if not deals:
        return await q.edit_message_caption(caption="No deals are currently available.")
    index = max(0, min(index, len(deals)-1))
    deal = deals[index]
    owned = await _owned_count(uid, deal["waifu_id"])
    sold = index in doc.get("bought", [])
    caption = _market_caption(deal, index, len(deals), owned, sold, int(doc.get("purchases", 0)))
    active = await auctions.count_documents({"status": "open", "ends_at": {"$gt": _now()}}) > 0
    await q.edit_message_caption(caption=caption, reply_markup=_market_keyboard(uid, index, len(deals), sold, active))


async def marketplace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]
    uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else q.from_user.id
    if uid != q.from_user.id:
        return await q.answer("This marketplace belongs to another user.", show_alert=True)
    await q.answer()
    if action == "close":
        return await q.message.delete()
    if action == "sold":
        return
    if action == "auction":
        return await show_auction(q, context, edit=True)
    if action == "view":
        return await _render_market(q, uid, int(parts[3]))
    if action == "buy":
        index = int(parts[3])
        doc = await _ensure_market(uid)
        if index >= len(doc.get("deals", [])):
            return await q.answer("This deal is no longer available.", show_alert=True)
        deal = doc["deals"][index]
        locked = await marketplaces.find_one_and_update(
            {"_id": uid, "day": doc["day"], "purchases": {"$lt": 2}, "bought": {"$ne": index}},
            {"$addToSet": {"bought": index}, "$inc": {"purchases": 1}},
            return_document=ReturnDocument.BEFORE,
        )
        if not locked:
            return await q.answer("Daily purchase limit reached or this deal was already bought.", show_alert=True)
        if not await debit_balance(uid, "cash", int(deal["sale_price"])):
            await marketplaces.update_one(
                {"_id": uid}, {"$pull": {"bought": index}, "$inc": {"purchases": -1}}
            )
            return await q.answer("❌ Insufficient Cash. Earn more with /explore or /bonus.", show_alert=True)
        char = await by_id(deal["waifu_id"])
        if not char:
            await credit_balance(uid, "cash", int(deal["sale_price"]))
            await marketplaces.update_one(
                {"_id": uid}, {"$pull": {"bought": index}, "$inc": {"purchases": -1}}
            )
            return await q.answer("This waifu is no longer in the database.", show_alert=True)
        try:
            await grant(uid, char, "marketplace")
        except Exception:
            await credit_balance(uid, "cash", int(deal["sale_price"]))
            await marketplaces.update_one(
                {"_id": uid}, {"$pull": {"bought": index}, "$inc": {"purchases": -1}}
            )
            raise
        await audit_logs.insert_one({
            "action": "market_buy", "user_id": uid, "waifu_id": deal["waifu_id"],
            "price": deal["sale_price"], "created_at": _now(),
        })
        await q.answer("✅ Purchased! Added to your /collection.", show_alert=True)
        return await _render_market(q, uid, index)
    if action == "refresh":
        uid = int(parts[2])
        market = await _ensure_market(uid)
        locked = await marketplaces.find_one_and_update(
            {"_id": uid, "day": market["day"], "refresh_count": {"$lt": 2}},
            {"$inc": {"refresh_count": 1}},
            return_document=ReturnDocument.BEFORE,
        )
        if not locked:
            return await q.answer("Daily refresh limit reached (2).", show_alert=True)
        if not await debit_balance(uid, "cash", 50000):
            await marketplaces.update_one({"_id": uid}, {"$inc": {"refresh_count": -1}})
            return await q.answer("❌ You need 50,000 Cash to refresh.", show_alert=True)
        old_ids = [x.get("waifu_id") for x in market.get("deals", [])]
        deals = []
        for _ in range(2):
            offer = await _make_offer(old_ids)
            if offer:
                deals.append(offer)
        await marketplaces.update_one(
            {"_id": uid},
            {"$set": {"deals": deals, "bought": [], "updated_at": _now()}},
        )
        await q.answer("🔄 Marketplace refreshed for 50,000 Cash.", show_alert=True)
        return await _render_market(q, uid, 0)


async def add_auction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    from .admin import is_staff
    if not await is_staff(uid):
        return
    args = context.args
    if not args or args[0].casefold() == "users":
        active = await auctions.find({"status": "open"}).sort("highest_bid", -1).to_list(length=50)
        if not active:
            return await update.effective_message.reply_text("No active auctions.")
        text = "🔨 ACTIVE AUCTIONS\n"
        for item in active:
            text += f'ID {item["waifu_id"]} | {item["character"].get("name")} | {_cash(item["highest_bid"])} Cash | {_remaining(item["ends_at"])}\n'
        return await update.effective_message.reply_text(text)
    if len(args) not in (2, 3, 4) or not args[0].isdigit() or not args[1].replace(",", "").isdigit():
        return await update.effective_message.reply_text(
            "Usage:\n/auction <waifu_id> <starting_bid> [12 Hours|24 Hours]\n"
            "Example: /auction 2342 1000000"
        )
    wid, amount = args[0], int(args[1].replace(",", ""))
    if amount <= 0:
        return await update.effective_message.reply_text("Starting bid must be greater than zero.")
    duration_hours = 24
    if len(args) >= 3:
        token = args[2].lower().replace("hours", "").replace("hour", "").replace("h", "").strip()
        if token.isdigit():
            duration_hours = int(token)
    if len(args) == 4 and args[3].lower() in {"hours", "hour", "h"}:
        pass
    if not 1 <= duration_hours <= 168:
        return await update.effective_message.reply_text("Auction duration must be 1–168 hours.")
    char = await by_id(wid)
    if not char:
        return await update.effective_message.reply_text("THIS WAIFU IS NOT UPLOADED IN BOT DATABASE.")
    existing = await auctions.find_one({"waifu_id": wid})
    if existing:
        return await update.effective_message.reply_text("This waifu has already been auctioned or listed.")
    token = uuid.uuid4().hex
    ends = _now() + timedelta(hours=duration_hours)
    auction = {
        "_id": token, "waifu_id": wid, "character": {
            "id": str(char.get("id")), "name": char.get("name"), "anime": char.get("anime"),
            "rarity": char.get("rarity"), "img_url": image_of(char), "price": char.get("price", 0),
        },
        "starting_bid": amount, "highest_bid": amount, "highest_bidder": None,
        "bidders": [], "bidder_count": 0, "ends_at": ends,
        "duration_hours": duration_hours, "status": "open", "created_at": _now(),
        "created_by": uid,
    }
    await auctions.insert_one(auction)
    await audit_logs.insert_one({"action": "auction_create", "actor_id": uid, "auction_id": token, "waifu_id": wid, "created_at": _now()})
    await update.effective_message.reply_photo(
        image_of(char), caption=(
            "╔══════════════════════╗\n🔨 NEW AUCTION CREATED!\n╚══════════════════════╝\n"
            f'🎭 NAME ➤ {char.get("name")}\n📺 ANIME ➤ {char.get("anime")}\n'
            f'🆔 WAIFU ID ➤ {wid}\n💫 RARITY ➤ {char.get("rarity")}\n'
            f'💎 STARTING BID ➤ {_cash(amount)} Cash💸\n'
            f'💰 CURRENT BID ➤ {_cash(amount)} Cash💸\n'
            f'⌛ REMAINING ➤ {_remaining(ends)}\n👥 TOTAL BIDDERS ➤ 0\n'
            '🎯 HIGHEST BIDDER ➤ NONE\n\nView and bid now in /marketplace'
        ),
    )
    if context.job_queue:
        context.job_queue.run_once(
            finish_auction, when=duration_hours * 3600,
            data={"auction_id": token}, name=f"auction:{token}",
        )


async def show_auction(q, context, edit=False):
    auction = await auctions.find_one({"status": "open", "ends_at": {"$gt": _now()}})
    if not auction:
        if edit:
            return await q.answer("AUCTION IS NOT AVAILABLE.", show_alert=True)
        return await q.message.reply_text("AUCTION IS NOT AVAILABLE.")
    char = auction["character"]
    highest = auction.get("highest_bidder")
    name = "None"
    if highest:
        usr = await user_collection.find_one({"id": highest}) or {}
        name = "@" + usr["username"] if usr.get("username") else usr.get("first_name", "User")
    caption = (
        "🔨 LIVE WAIFU AUCTION\n"
        f'🎭 {char.get("name")} | {char.get("anime")}\n'
        f'🆔 ID: {char.get("id")}\n💫 Rarity: {char.get("rarity")}\n'
        f'💎 Starting bid: {_cash(auction["starting_bid"])}\n'
        f'💰 Current bid: {_cash(auction["highest_bid"])} Cash💸\n'
        f'🎯 Highest bidder: {name}\n👥 Participants: {auction.get("bidder_count", 0)}\n'
        f'⌛ Remaining: {_remaining(auction["ends_at"])}'
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("💰 PLACE BID", callback_data=f"auc:bid:{auction['_id']}"),
        InlineKeyboardButton("⬅ BACK", callback_data=f"auc:back:{q.from_user.id}"),
    ]])
    if edit:
        return await q.edit_message_caption(caption=caption, reply_markup=keyboard)
    if image_of(char):
        return await q.message.reply_photo(image_of(char), caption=caption, reply_markup=keyboard)
    return await q.message.reply_text(caption, reply_markup=keyboard)


async def auction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, token = q.data.split(":")
    if action == "back":
        uid = int(token)
        if uid != q.from_user.id:
            return await q.answer("This marketplace is not yours.", show_alert=True)
        await q.answer()
        return await q.message.delete()
    if action == "bid":
        auction = await auctions.find_one({"_id": token, "status": "open", "ends_at": {"$gt": _now()}})
        if not auction:
            return await q.answer("AUCTION IS NOT AVAILABLE.", show_alert=True)
        prompt = await q.message.reply_text(
            f'Enter your bid above {_cash(auction["highest_bid"])} Cash using /bid <amount>.',
            reply_markup=ForceReply(selective=True),
        )
        await pending_actions.insert_one({
            "_id": uuid.uuid4().hex, "type": "auction_bid_input",
            "user_id": q.from_user.id, "auction_id": token,
            "prompt_message_id": prompt.message_id, "status": "pending",
            "expires_at": _now() + timedelta(minutes=5),
        })
        return await q.answer("Send /bid <amount> in reply to the prompt.")
    if action == "confirm":
        return await bid_confirm(update, context, token)
    if action == "cancel":
        return await bid_cancel(update, context, token)


async def bid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if len(context.args) != 1 or not context.args[0].replace(",", "").isdigit():
        return await msg.reply_text("Usage: /bid <amount> (reply to the auction bid prompt).")
    amount = int(context.args[0].replace(",", ""))
    if amount <= 0:
        return await msg.reply_text("Bid must be positive.")
    pending = None
    if msg.reply_to_message:
        pending = await pending_actions.find_one({
            "type": "auction_bid_input", "user_id": update.effective_user.id,
            "prompt_message_id": msg.reply_to_message.message_id, "status": "pending",
            "expires_at": {"$gt": _now()},
        })
    if not pending:
        pending = await pending_actions.find_one({
            "type": "auction_bid_input", "user_id": update.effective_user.id,
            "status": "pending", "expires_at": {"$gt": _now()},
        }, sort=[("expires_at", -1)])
    if not pending:
        return await msg.reply_text("No active bid request. Open /marketplace → Auction first.")
    auction = await auctions.find_one({"_id": pending["auction_id"], "status": "open", "ends_at": {"$gt": _now()}})
    if not auction:
        await pending_actions.update_one({"_id": pending["_id"]}, {"$set": {"status": "expired"}})
        return await msg.reply_text("AUCTION IS NOT AVAILABLE.")
    if amount <= int(auction["highest_bid"]):
        return await msg.reply_text(f"Your bid must be higher than {_cash(auction['highest_bid'])} Cash💸.")
    user_doc = await get_user(update.effective_user.id, update.effective_user)
    current_reserved = 0
    if auction.get("highest_bidder") == update.effective_user.id:
        current_reserved = int(auction["highest_bid"])
    difference = amount - current_reserved
    if int(user_doc.get("cash", 0)) < difference:
        return await msg.reply_text("❌ Insufficient Cash for this bid.")
    token = uuid.uuid4().hex
    await pending_actions.insert_one({
        "_id": token, "type": "auction_bid_confirm", "status": "pending",
        "user_id": update.effective_user.id, "auction_id": auction["_id"],
        "amount": amount, "difference": difference,
        "expires_at": _now() + timedelta(minutes=2),
    })
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm Bid", callback_data=f"auc:confirm:{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"auc:cancel:{token}"),
    ]])
    await msg.reply_text(
        f"Confirm your bid of {_cash(amount)} Cash💸?\n"
        f"Available balance: {_cash(user_doc.get('cash', 0))} Cash💸",
        reply_markup=markup,
    )


async def bid_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, token):
    q = update.callback_query
    pending = await pending_actions.find_one_and_update(
        {"_id": token, "type": "auction_bid_confirm", "status": "pending",
         "user_id": q.from_user.id, "expires_at": {"$gt": _now()}},
        {"$set": {"status": "processing"}},
        return_document=ReturnDocument.BEFORE,
    )
    if not pending:
        return await q.answer("Bid request expired or belongs to another user.", show_alert=True)
    auction = await auctions.find_one({"_id": pending["auction_id"], "status": "open", "ends_at": {"$gt": _now()}})
    if not auction or int(auction["highest_bid"]) >= int(pending["amount"]):
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.answer("Someone has already placed a higher bid.", show_alert=True)
    if not await debit_balance(q.from_user.id, "cash", int(pending["difference"])):
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.answer("Insufficient balance.", show_alert=True)
    previous_id = auction.get("highest_bidder")
    previous_amount = int(auction["highest_bid"])
    changed = await auctions.update_one(
        {"_id": auction["_id"], "status": "open", "highest_bid": previous_amount,
         "ends_at": {"$gt": _now()}},
        {"$set": {"highest_bid": int(pending["amount"]), "highest_bidder": q.from_user.id},
         "$addToSet": {"bidders": q.from_user.id}},
    )
    if changed.modified_count != 1:
        await credit_balance(q.from_user.id, "cash", int(pending["difference"]))
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.answer("A newer bid was placed. Please try again.", show_alert=True)
    if previous_id and previous_id != q.from_user.id:
        await credit_balance(previous_id, "cash", previous_amount)
    await auctions.update_one({"_id": auction["_id"]}, {"$set": {"bidder_count": len(set((auction.get("bidders") or []) + [q.from_user.id]))}})
    await pending_actions.update_one({"_id": token}, {"$set": {"status": "confirmed"}})
    await audit_logs.insert_one({
        "action": "auction_bid", "user_id": q.from_user.id,
        "auction_id": auction["_id"], "amount": int(pending["amount"]), "created_at": _now(),
    })
    await q.answer("✅ Bid placed successfully!", show_alert=True)
    try:
        await q.message.edit_text(f"✅ Your bid of {_cash(pending['amount'])} Cash💸 is now the highest bid.")
    except Exception:
        pass


async def bid_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, token):
    q = update.callback_query
    result = await pending_actions.update_one(
        {"_id": token, "type": "auction_bid_confirm", "user_id": q.from_user.id, "status": "pending"},
        {"$set": {"status": "cancelled"}},
    )
    if not result.modified_count:
        return await q.answer("This bid can no longer be cancelled.", show_alert=True)
    await q.answer()
    await q.edit_message_text("❌ Bid cancelled. No funds were taken.")


async def finish_auction(context: ContextTypes.DEFAULT_TYPE):
    token = context.job.data["auction_id"]
    auction = await auctions.find_one_and_update(
        {"_id": token, "status": "open", "ends_at": {"$lte": _now()}},
        {"$set": {"status": "closed", "closed_at": _now()}},
        return_document=ReturnDocument.BEFORE,
    )
    if not auction:
        # Startup scheduler may run slightly earlier than the stored deadline.
        return
    winner = auction.get("highest_bidder")
    if winner:
        char = await by_id(auction["waifu_id"])
        if char:
            await grant(winner, char, "auction_win")
            await audit_logs.insert_one({
                "action": "auction_complete", "winner_id": winner, "auction_id": token,
                "waifu_id": auction["waifu_id"], "amount": auction["highest_bid"], "created_at": _now(),
            })
            try:
                await context.bot.send_photo(
                    chat_id=winner, photo=image_of(char),
                    caption=f"🏆 AUCTION WON!\n{char.get('name')}\n💸 Paid: {_cash(auction['highest_bid'])} Cash💸\nAdded to your /collection.",
                )
            except Exception:
                pass


async def schedule_open_auctions(application):
    cursor = auctions.find({"status": "open"})
    async for item in cursor:
        seconds = max(0, (item["ends_at"] - _now()).total_seconds())
        application.job_queue.run_once(
            finish_auction, when=seconds, data={"auction_id": item["_id"]},
            name=f"auction:{item['_id']}",
        )


def register(app):
    app.add_handler(CommandHandler("marketplace", marketplace_command))
    app.add_handler(CommandHandler("auction", add_auction))
    app.add_handler(CommandHandler("bid", bid_command))
    app.add_handler(CallbackQueryHandler(marketplace_callback, pattern=r"^market:"))
    app.add_handler(CallbackQueryHandler(auction_callback, pattern=r"^auc:"))
