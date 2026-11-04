"""MongoDB-backed balances, streak bonuses, exploration and confirmed payments."""
import random
import uuid
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..config import load_settings
from ..database import (
    user_collection, pending_actions, transactions, get_user,
    debit_balance, credit_balance_once,
)

settings = load_settings()


def _money(value):
    return f"{int(value):,}"


def _left(until):
    seconds = max(0, int((until - datetime.utcnow()).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _bonus_multiplier(streak):
    if streak >= 30:
        return 2.0
    if streak >= 14:
        return 1.0
    if streak >= 7:
        return 0.5
    if streak >= 3:
        return 0.2
    return 0.0


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
        location = random.choice([
            "Abandoned Village", "Old Anime Town", "Broken Dojo",
            "Small Forest Camp", "Deserted House", "Underground Alley", "Ruined Shrine",
        ])
    elif roll <= 95:
        cash, diamonds = random.randint(3500, 4700), random.randint(1, 2)
        location = random.choice([
            "Royal Palace", "Hidden Dungeon", "Ancient Dragon Cave",
            "Sky Island", "Legendary Battlefield", "Forbidden Capital", "Celestial Tower",
        ])
    else:
        cash, diamonds = random.randint(4750, 6500), random.randint(2, 3)
        location = random.choice(["Legendary Battlefield", "Celestial Tower", "Forbidden Capital"])
    claimed = await user_collection.update_one(
        {"id": uid, "$or": [{"explore_next_at": {"$exists": False}}, {"explore_next_at": {"$lte": now}}]},
        {"$set": {"explore_next_at": now + timedelta(minutes=1)}},
    )
    if claimed.modified_count != 1:
        current = await user_collection.find_one({"id": uid}) or {}
        return await update.effective_message.reply_text(f"⏳ Explore again in {_left(current['explore_next_at'])}.")
    reward_id = f"explore:{uid}:{now.isoformat()}"
    await credit_balance_once(uid, "cash", cash, reward_id + ":cash")
    if diamonds:
        await credit_balance_once(uid, "diamonds", diamonds, reward_id + ":diamonds")
    text = f"🗺️ ADVENTURE COMPLETE!\n📍 {location}\n💸 +{_money(cash)} Cash"
    if diamonds:
        text += f"\n💎 +{diamonds} Diamonds"
    await update.effective_message.reply_text(text + "\n✅ Check /balance")


def _bonus_keyboard(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Daily Bonus", callback_data=f"bonus:daily:{uid}")],
        [InlineKeyboardButton("💸 Weekly Bonus", callback_data=f"bonus:weekly:{uid}")],
        [InlineKeyboardButton("📊 Streak Stats", callback_data=f"bonus:stats:{uid}")],
        [InlineKeyboardButton("❌ Close", callback_data=f"bonus:close:{uid}")],
    ])


async def bonus_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await get_user(uid, update.effective_user)
    text = (
        f"🎮 BONUS SYSTEM\n👤 {update.effective_user.first_name}\n"
        f"💰 Cash: {_money(user.get('cash', 0))}\n💎 Diamonds: {user.get('diamonds', 0)}\n"
        f"🔥 Current Streak: {user.get('daily_streak', 0)} days\n"
        f"⭐ Highest Streak: {user.get('highest_streak', 0)} days"
    )
    await update.effective_message.reply_text(text, reply_markup=_bonus_keyboard(uid))


async def bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, uid_raw = q.data.split(":")
    uid = int(uid_raw)
    if q.from_user.id != uid:
        return await q.answer("This bonus menu is not yours.", show_alert=True)
    await q.answer()
    if action == "close":
        return await q.message.delete()
    doc = await get_user(uid, q.from_user)
    if action == "stats":
        return await q.edit_message_text(
            f"📊 BONUS HISTORY\nDaily next: {doc.get('daily_bonus_next_at', 'Ready')}\n"
            f"Weekly next: {doc.get('weekly_bonus_next_at', 'Ready')}\n"
            f"Current streak: {doc.get('daily_streak', 0)} days\n"
            f"Highest streak: {doc.get('highest_streak', 0)} days",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"bonus:back:{uid}")]])
        )
    if action == "back":
        doc = await get_user(uid, q.from_user)
        text = (
            f"🎮 BONUS SYSTEM\n💰 Cash: {_money(doc.get('cash', 0))}\n"
            f"💎 Diamonds: {doc.get('diamonds', 0)}\n"
            f"🔥 Current Streak: {doc.get('daily_streak', 0)} days"
        )
        return await q.edit_message_text(text, reply_markup=_bonus_keyboard(uid))
    if action not in {"daily", "weekly"}:
        return
    now = datetime.utcnow()
    weekly = action == "weekly"
    key = "weekly_bonus_next_at" if weekly else "daily_bonus_next_at"
    interval = timedelta(days=7) if weekly else timedelta(days=1)
    cash_base = 300000 if weekly else 70000
    diamonds = 30 if weekly else 10
    current = await user_collection.find_one({"id": uid}) or {}
    next_at = current.get(key)
    if next_at and next_at > now:
        return await q.edit_message_text(f"⏳ Next bonus in {_left(next_at)}.", reply_markup=_bonus_keyboard(uid))
    streak = int(current.get("daily_streak", 0))
    update_fields = {key: now + interval}
    if not weekly:
        last_claim = current.get("last_daily_claim_at")
        if last_claim and now - last_claim <= timedelta(hours=48):
            streak += 1
        else:
            streak = 1
        update_fields.update({
            "daily_streak": streak,
            "highest_streak": max(int(current.get("highest_streak", 0)), streak),
            "last_daily_claim_at": now,
        })
    claimed = await user_collection.find_one_and_update(
        {"id": uid, "$or": [{key: {"$exists": False}}, {key: {"$lte": now}}]},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        latest = await user_collection.find_one({"id": uid}) or {}
        return await q.edit_message_text(f"⏳ Next bonus in {_left(latest.get(key, now))}.")
    multiplier = _bonus_multiplier(streak)
    cash = int(cash_base * (1 + multiplier))
    reward_id = f"bonus:{action}:{uid}:{now.strftime('%Y-%m-%d')}"
    await credit_balance_once(uid, "cash", cash, reward_id + ":cash")
    await credit_balance_once(uid, "diamonds", diamonds, reward_id + ":diamonds")
    await q.edit_message_text(
        f"✅ {'WEEKLY' if weekly else 'DAILY'} BONUS CLAIMED!\n"
        f"💰 +{_money(cash)} Cash\n💎 +{diamonds} Diamonds\n"
        f"🔥 Streak: {streak} days\n📈 Multiplier: x{1 + multiplier:.1f}"
    )


async def bonusrefresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    target = update.effective_message.reply_to_message
    if not target:
        return await update.effective_message.reply_text("Reply to a user and use /bonusrefresh.")
    uid = target.from_user.id
    await get_user(uid, target.from_user)
    await user_collection.update_one(
        {"id": uid},
        {"$unset": {"daily_bonus_next_at": "", "weekly_bonus_next_at": ""}},
    )
    await update.effective_message.reply_text("✅ Daily and weekly cooldowns reset. Streak remains safe.")


async def _refund_payment(payment):
    await credit_balance_once(
        payment["payer_id"], payment.get("currency", "cash"), int(payment["amount"]),
        f"payment-refund:{payment['_id']}",
    )


async def _complete_payment(payment, bot=None):
    await transactions.update_one(
        {"transaction_id": payment["_id"]},
        {"$setOnInsert": {
            "transaction_id": payment["_id"], "type": "transfer",
            "payer_id": payment["payer_id"], "receiver_id": payment["receiver_id"],
            "currency": payment["currency"], "amount": payment["amount"],
            "created_at": datetime.utcnow(),
        }},
        upsert=True,
    )
    await credit_balance_once(
        payment["receiver_id"], payment["currency"], int(payment["amount"]),
        f"payment-credit:{payment['_id']}",
    )
    await pending_actions.update_one(
        {"_id": payment["_id"], "status": "processing"},
        {"$set": {"status": "confirmed"}},
    )
    if bot:
        try:
            label = "Cash💸" if payment["currency"] == "cash" else "Diamonds💎"
            await bot.send_message(
                chat_id=payment["receiver_id"],
                text=f"🎉 You received {_money(payment['amount'])} {label} from {payment['payer_name']}!\nCheck /balance",
            )
        except Exception:
            pass


async def _expire_payment(payment_id):
    expired = await pending_actions.find_one_and_update(
        {"_id": payment_id, "type": "payment", "status": {"$in": ["choosing", "pending"]}, "expires_at": {"$lte": datetime.utcnow()}},
        {"$set": {"status": "expired"}},
        return_document=ReturnDocument.BEFORE,
    )
    if expired and expired.get("status") == "pending":
        await _refund_payment(expired)


async def payment_expire(context: ContextTypes.DEFAULT_TYPE):
    await _expire_payment(context.job.data["payment_id"])


async def recover_pending_payments(application):
    now = datetime.utcnow()
    pending = await pending_actions.find({
        "type": "payment", "status": {"$in": ["choosing", "pending", "processing"]}
    }).to_list(length=5000)
    for item in pending:
        if item["status"] == "processing":
            await _complete_payment(item)
            continue
        seconds = max(0.1, (item["expires_at"] - now).total_seconds())
        if seconds <= 0:
            await _expire_payment(item["_id"])
        elif application.job_queue:
            application.job_queue.run_once(
                payment_expire, when=seconds, data={"payment_id": item["_id"]},
                name=f"payment-expire:{item['_id']}",
            )


async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    payer = update.effective_user
    if not msg.reply_to_message:
        return await msg.reply_text("❗ Reply to a user and use /pay <amount>.")
    receiver = msg.reply_to_message.from_user
    if receiver.is_bot:
        return await msg.reply_text("🤖 You cannot pay the bot.")
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
    payer_doc = await get_user(payer.id, payer)
    await get_user(receiver.id, receiver)
    next_at = payer_doc.get("pay_next_at")
    now = datetime.utcnow()
    if payer.id != settings.owner_id and next_at and next_at > now:
        return await msg.reply_text(f"⏳ Please wait {_left(next_at)} before using /pay again.")
    amount_types = []
    if int(payer_doc.get("cash", 0)) >= amount:
        amount_types.append("cash")
    if int(payer_doc.get("diamonds", 0)) >= amount:
        amount_types.append("diamonds")
    if not amount_types:
        return await msg.reply_text("❌ You do not have enough Cash or Diamonds for this payment.")
    payment_id = uuid.uuid4().hex
    await pending_actions.insert_one({
        "_id": payment_id, "type": "payment", "status": "choosing",
        "payer_id": payer.id, "receiver_id": receiver.id, "amount": amount,
        "payer_name": payer.full_name, "receiver_name": receiver.full_name,
        "expires_at": now + timedelta(minutes=5),
    })
    rows = []
    if "cash" in amount_types:
        rows.append([InlineKeyboardButton("💵 Pay with Cash", callback_data=f"pay:type:{payment_id}:cash")])
    if "diamonds" in amount_types:
        rows.append([InlineKeyboardButton("💎 Pay with Diamonds", callback_data=f"pay:type:{payment_id}:diamonds")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"pay:cancel:{payment_id}")])
    await msg.reply_text(
        f"💸 Choose payment type for {_money(amount)} to {receiver.full_name}.\n"
        "Funds will be reserved after you choose a currency.",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    if context.job_queue:
        context.job_queue.run_once(
            payment_expire, when=300, data={"payment_id": payment_id},
            name=f"payment-expire:{payment_id}",
        )


async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, payment_id = parts[1], parts[2]
    payment = await pending_actions.find_one({"_id": payment_id, "type": "payment"})
    if not payment:
        return await q.answer("This payment request has expired.", show_alert=True)
    if q.from_user.id != payment["payer_id"]:
        return await q.answer("Only the sender can manage this payment.", show_alert=True)
    if payment.get("expires_at") <= datetime.utcnow():
        await _expire_payment(payment_id)
        return await q.answer("This payment request has expired.", show_alert=True)
    await q.answer()
    if action == "cancel":
        cancelled = await pending_actions.find_one_and_update(
            {"_id": payment_id, "status": {"$in": ["choosing", "pending"]}},
            {"$set": {"status": "cancelled"}},
            return_document=ReturnDocument.BEFORE,
        )
        if cancelled and cancelled["status"] == "pending":
            await _refund_payment(cancelled)
        return await q.edit_message_text("❌ Payment cancelled. Any reserved funds have been returned.")
    if action == "type":
        currency = parts[3]
        if currency not in {"cash", "diamonds"} or payment["status"] != "choosing":
            return await q.answer("Choose a valid payment type.", show_alert=True)
        if not await debit_balance(payment["payer_id"], currency, int(payment["amount"])):
            return await q.answer(f"Insufficient {currency}.", show_alert=True)
        changed = await pending_actions.update_one(
            {"_id": payment_id, "status": "choosing"},
            {"$set": {"status": "pending", "currency": currency, "reserved_at": datetime.utcnow()}},
        )
        if changed.modified_count != 1:
            await credit_balance_once(
                payment["payer_id"], currency, int(payment["amount"]),
                f"payment-selection-refund:{payment_id}",
            )
            return await q.answer("This payment was already updated.", show_alert=True)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data=f"pay:confirm:{payment_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"pay:cancel:{payment_id}"),
        ]])
        label = "Cash💸" if currency == "cash" else "Diamonds💎"
        return await q.edit_message_text(
            f"⚠️ CONFIRM PAYMENT\nPay {_money(payment['amount'])} {label} to {payment['receiver_name']}?\n"
            "The selected amount is reserved until you confirm or cancel.",
            reply_markup=keyboard,
        )
    if action == "confirm":
        locked = await pending_actions.find_one_and_update(
            {"_id": payment_id, "status": "pending", "expires_at": {"$gt": datetime.utcnow()}},
            {"$set": {"status": "processing"}},
            return_document=ReturnDocument.BEFORE,
        )
        if not locked:
            return await q.answer("This payment has already been processed.", show_alert=True)
        try:
            await _complete_payment(locked, context.bot)
        except Exception:
            # Leave the payment in processing; startup recovery will finish it idempotently.
            return await q.edit_message_text("⏳ Payment is processing. The bot will safely finish it.")
        if payment["payer_id"] != settings.owner_id:
            await user_collection.update_one(
                {"id": payment["payer_id"]},
                {"$set": {"pay_next_at": datetime.utcnow() + timedelta(minutes=5)}},
            )
        label = "Cash💸" if locked["currency"] == "cash" else "Diamonds💎"
        return await q.edit_message_text(f"✅ Payment successful! You paid {_money(locked['amount'])} {label}.")
    return


async def payrefresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    target = update.effective_message.reply_to_message
    if not target:
        return await update.effective_message.reply_text("Reply to a user and use /payrefresh.")
    await get_user(target.from_user.id, target.from_user)
    await user_collection.update_one({"id": target.from_user.id}, {"$unset": {"pay_next_at": ""}})
    await update.effective_message.reply_text(f"✅ Pay cooldown reset for {target.from_user.full_name}.")


def register(app):
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("explore", explore))
    app.add_handler(CommandHandler("bonus", bonus_menu))
    app.add_handler(CommandHandler("bonusrefresh", bonusrefresh))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("payrefresh", payrefresh))
    app.add_handler(CallbackQueryHandler(bonus_callback, pattern=r"^bonus:"))
    app.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^pay:"))
