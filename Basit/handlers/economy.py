"""MongoDB-backed balances, bonuses, exploration, and confirmed payments."""
import random
import time
import uuid
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..database import user_collection, pending_actions, transactions, get_user, debit_balance, credit_balance
from ..config import load_settings

settings = load_settings()


def _money(value):
    return f"{int(value):,}"


def _left(until):
    seconds = max(0, int((until - datetime.utcnow()).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id, update.effective_user)
    await update.effective_message.reply_text(
        f"💳 BALANCE INFO\n👤 {update.effective_user.first_name}\n"
        f"💰 Cash: {_money(user.get('cash', 0))}\n"
        f"💎 Diamonds: {_money(user.get('diamonds', 0))}"
    )


async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await get_user(uid, update.effective_user)
    now = datetime.utcnow()
    doc = await user_collection.find_one({"id": uid}) or {}
    next_at = doc.get("explore_next_at")
    if next_at and next_at > now:
        return await update.effective_message.reply_text(f"⏳ Explore again in {_left(next_at)}.")
    roll = random.randint(1, 100)
    if roll <= 85:
        cash, diamonds = random.randint(2300, 3500), 0
        location = random.choice(["Abandoned Village", "Old Anime Town", "Broken Dojo", "Small Forest Camp"])
    elif roll <= 95:
        cash, diamonds = random.randint(3500, 4700), random.randint(1, 2)
        location = random.choice(["Royal Palace", "Hidden Dungeon", "Ancient Dragon Cave"])
    else:
        cash, diamonds = random.randint(4750, 6500), random.randint(2, 3)
        location = random.choice(["Legendary Battlefield", "Celestial Tower", "Forbidden Capital"])
    await user_collection.update_one({"id": uid}, {"$set": {"explore_next_at": now + timedelta(minutes=1)}})
    await credit_balance(uid, "cash", cash)
    if diamonds:
        await credit_balance(uid, "diamonds", diamonds)
    text = f"🗺️ ADVENTURE COMPLETE!\n📍 {location}\n💸 +{_money(cash)} Cash"
    if diamonds:
        text += f"\n💎 +{diamonds} Diamonds"
    await update.effective_message.reply_text(text + "\n✅ Check /balance")


async def bonus_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await get_user(uid, update.effective_user)
    text = (
        f"🎮 BONUS SYSTEM\n👤 {update.effective_user.first_name}\n"
        f"💰 Cash: {_money(user.get('cash', 0))}\n💎 Diamonds: {user.get('diamonds', 0)}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Daily Bonus", callback_data=f"bonus:daily:{uid}")],
        [InlineKeyboardButton("💸 Weekly Bonus", callback_data=f"bonus:weekly:{uid}")],
        [InlineKeyboardButton("📊 Streak Stats", callback_data=f"bonus:stats:{uid}")],
        [InlineKeyboardButton("❌ Close", callback_data=f"bonus:close:{uid}")],
    ])
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, uid_raw = q.data.split(":")
    uid = int(uid_raw)
    if q.from_user.id != uid:
        return await q.answer("This bonus menu is not yours.", show_alert=True)
    await q.answer()
    if action == "close":
        return await q.message.delete()
    if action == "stats":
        doc = await user_collection.find_one({"id": uid}) or {}
        return await q.edit_message_text(
            f"📊 BONUS HISTORY\nDaily next: {doc.get('daily_bonus_next_at', 'Ready')}\n"
            f"Weekly next: {doc.get('weekly_bonus_next_at', 'Ready')}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"bonus:back:{uid}")]])
        )
    if action == "back":
        doc = await user_collection.find_one({"id": uid}) or {}
        return await q.edit_message_text(
            f"🎮 BONUS SYSTEM\n💰 Cash: {_money(doc.get('cash', 0))}\n💎 Diamonds: {doc.get('diamonds', 0)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 Daily Bonus", callback_data=f"bonus:daily:{uid}")],
                [InlineKeyboardButton("💸 Weekly Bonus", callback_data=f"bonus:weekly:{uid}")],
                [InlineKeyboardButton("📊 Streak Stats", callback_data=f"bonus:stats:{uid}")],
                [InlineKeyboardButton("❌ Close", callback_data=f"bonus:close:{uid}")],
            ])
        )
    now = datetime.utcnow()
    weekly = action == "weekly"
    key = "weekly_bonus_next_at" if weekly else "daily_bonus_next_at"
    interval = timedelta(days=7) if weekly else timedelta(days=1)
    cash = 300000 if weekly else 70000
    diamonds = 30 if weekly else 10
    await get_user(uid, q.from_user)
    claimed = await user_collection.find_one_and_update(
        {"id": uid, "$or": [{key: {"$exists": False}}, {key: {"$lte": now}}]},
        {"$set": {key: now + interval}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        current = await user_collection.find_one({"id": uid}) or {}
        next_at = current.get(key, now)
        return await q.edit_message_text(f"⏳ Next bonus in {_left(next_at)}.")
    await credit_balance(uid, "cash", cash)
    await credit_balance(uid, "diamonds", diamonds)
    await q.edit_message_text(
        f"✅ {'WEEKLY' if weekly else 'DAILY'} BONUS CLAIMED!\n"
        f"💰 +{_money(cash)} Cash\n💎 +{diamonds} Diamonds"
    )


async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    payer = update.effective_user
    if not msg.reply_to_message:
        return await msg.reply_text("❗ Reply to a user and use /pay <amount>.")
    receiver = msg.reply_to_message.from_user
    if receiver.is_bot:
        return await msg.reply_text("🤖 You cannot pay the bot. Send Cash to a real user.")
    if receiver.id == payer.id:
        return await msg.reply_text("❌ You cannot pay yourself.")
    if len(context.args) != 1:
        return await msg.reply_text("❗ Usage: reply to a user and type /pay <amount>.")
    try:
        amount = int(context.args[0].replace(",", ""))
    except ValueError:
        return await msg.reply_text("❗ Enter a valid amount.")
    if amount <= 0:
        return await msg.reply_text("❗ Amount must be greater than zero.")
    await get_user(payer.id, payer)
    await get_user(receiver.id, receiver)
    user_doc = await user_collection.find_one({"id": payer.id}) or {}
    next_at = user_doc.get("pay_next_at")
    now = datetime.utcnow()
    if next_at and next_at > now:
        return await msg.reply_text(f"⏳ Please wait {_left(next_at)} before using /pay again.")
    if not await debit_balance(payer.id, "cash", amount):
        return await msg.reply_text("❌ You do not have enough Cash💸.")
    payment_id = uuid.uuid4().hex
    try:
        await pending_actions.insert_one({
            "_id": payment_id, "type": "payment", "status": "pending",
            "payer_id": payer.id, "receiver_id": receiver.id, "amount": amount,
            "payer_name": payer.full_name, "receiver_name": receiver.full_name,
            "expires_at": now + timedelta(minutes=5),
        })
    except Exception:
        await credit_balance(payer.id, "cash", amount)
        raise
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"pay:confirm:{payment_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"pay:cancel:{payment_id}"),
    ]])
    await msg.reply_text(
        f"💸 You are about to pay {_money(amount)} Cash💸 to {receiver.full_name}.\n"
        "⚡️ Funds are reserved until you confirm or cancel.",
        reply_markup=keyboard,
    )
    if context.job_queue:
        context.job_queue.run_once(payment_expire, when=300, data={"payment_id": payment_id})


async def payment_expire(context: ContextTypes.DEFAULT_TYPE):
    payment_id = context.job.data["payment_id"]
    pending = await pending_actions.find_one_and_update(
        {"_id": payment_id, "status": "pending"},
        {"$set": {"status": "expired"}},
        return_document=ReturnDocument.BEFORE,
    )
    if pending:
        await credit_balance(pending["payer_id"], "cash", pending["amount"])


async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, payment_id = q.data.split(":")
    data = await pending_actions.find_one({"_id": payment_id})
    if not data:
        return await q.answer("This payment has expired.", show_alert=True)
    if q.from_user.id != data["payer_id"]:
        return await q.answer("Only the sender can confirm this payment.", show_alert=True)
    if data["status"] != "pending":
        return await q.answer("This payment has already been processed.", show_alert=True)
    await q.answer()
    next_status = "confirmed" if action == "confirm" else "cancelled"
    locked = await pending_actions.find_one_and_update(
        {"_id": payment_id, "status": "pending"},
        {"$set": {"status": next_status}},
        return_document=ReturnDocument.BEFORE,
    )
    if not locked:
        return await q.edit_message_text("This payment has already been processed.")
    if action == "cancel":
        await credit_balance(data["payer_id"], "cash", data["amount"])
        return await q.edit_message_text("❌ Payment cancelled. Your funds are safe.")
    try:
        await credit_balance(data["receiver_id"], "cash", data["amount"])
        await transactions.insert_one({
            "transaction_id": payment_id, "type": "cash_transfer",
            "payer_id": data["payer_id"], "receiver_id": data["receiver_id"],
            "amount": data["amount"], "created_at": datetime.utcnow(),
        })
        await user_collection.update_one(
            {"id": data["payer_id"]},
            {"$set": {"pay_next_at": datetime.utcnow() + timedelta(minutes=5)}},
        )
    except Exception:
        await credit_balance(data["payer_id"], "cash", data["amount"])
        await pending_actions.update_one({"_id": payment_id}, {"$set": {"status": "failed_refunded"}})
        return await q.edit_message_text("❌ Payment could not be completed. Reserved funds were returned.")
    await q.edit_message_text(f"✅ Payment successful! You paid {_money(data['amount'])} Cash💸.")
    try:
        await context.bot.send_message(
            chat_id=data["receiver_id"],
            text=f"🎉 You received {_money(data['amount'])} Cash💸 from {data['payer_name']}!\nCheck /balance",
        )
    except Exception:
        pass


def register(app):
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("explore", explore))
    app.add_handler(CommandHandler("bonus", bonus_menu))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CallbackQueryHandler(bonus_callback, pattern=r"^bonus:"))
    app.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^pay:"))
