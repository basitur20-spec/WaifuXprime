"""MongoDB-backed redeem code generation and redemption."""
import secrets
import string
from datetime import datetime
from pymongo import ReturnDocument
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from ..config import load_settings
from ..database import codes, credit_balance, add_character, audit_logs
from ..services.characters import by_id
from .admin import _roles

settings = load_settings()


def new_code():
    alphabet = string.ascii_uppercase + string.digits
    return "WXP-" + "".join(secrets.choice(alphabet) for _ in range(10))


async def role_of(uid):
    sudo, uploaders = await _roles()
    if uid == settings.owner_id:
        return "owner"
    if uid in sudo:
        return "sudo"
    if uid in uploaders:
        return "uploader"
    return "user"


async def gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    role = await role_of(uid)
    args = context.args
    if len(args) not in (2, 3):
        return await update.effective_message.reply_text(
            "Usage: /gen <amount> <quantity> [cash|diamonds]\nExample: /gen 500000 10 cash"
        )
    try:
        amount = int(args[0].replace(",", ""))
        quantity = int(args[1])
    except ValueError:
        return await update.effective_message.reply_text("Amount and quantity must be whole numbers.")
    kind = args[2].casefold() if len(args) == 3 else "cash"
    if kind in {"diamond", "dimond", "dimonds"}:
        kind = "diamonds"
    if kind not in {"cash", "diamonds"} or amount <= 0 or not 1 <= quantity <= 10000:
        return await update.effective_message.reply_text("Invalid reward type, amount, or quantity.")
    if kind == "cash" and role not in {"owner", "sudo"}:
        return
    if kind == "diamonds" and role != "owner":
        return
    code = new_code()
    await codes.insert_one({
        "_id": code, "code": code, "type": kind, "amount": amount,
        "quantity": quantity, "used": [], "creator_id": uid,
        "creator_role": role, "created_at": datetime.utcnow(),
    })
    await audit_logs.insert_one({
        "action": "gen_code", "actor_id": uid, "code": code,
        "kind": kind, "created_at": datetime.utcnow(),
    })
    reward_name = "Cash💸" if kind == "cash" else "Diamonds💎"
    await update.effective_message.reply_text(
        f"🎟️ NEW {kind.upper()} CODE\n\n{code}\n"
        f"Reward: {amount:,} {reward_name}\nQuantity: {quantity}\n\nUse /redeem {code}"
    )


async def dgen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    role = await role_of(uid)
    if role not in {"owner", "sudo", "uploader"}:
        return
    if len(context.args) != 2:
        return await update.effective_message.reply_text("Usage: /dgen <waifu_id> <quantity>")
    wid = context.args[0]
    try:
        quantity = int(context.args[1])
    except ValueError:
        return await update.effective_message.reply_text("Quantity must be a whole number.")
    if not 1 <= quantity <= 10000:
        return await update.effective_message.reply_text("Quantity must be between 1 and 10,000.")
    char = await by_id(wid)
    if not char:
        return await update.effective_message.reply_text("❌ Waifu ID not found in bot database.")
    code = new_code()
    await codes.insert_one({
        "_id": code, "code": code, "type": "waifu", "waifu_id": str(wid),
        "quantity": quantity, "used": [], "creator_id": uid,
        "creator_role": role, "created_at": datetime.utcnow(),
    })
    await audit_logs.insert_one({
        "action": "dgen_code", "actor_id": uid, "code": code,
        "waifu_id": str(wid), "created_at": datetime.utcnow(),
    })
    await update.effective_message.reply_text(
        f"🎟️ WAIFU CODE GENERATED\n\n{code}\n"
        f"🧩 Waifu ID: {wid}\n👥 Quantity: {quantity}\n\nUse /dredeem {code}"
    )


async def _redeem(update, code, kind):
    uid = update.effective_user.id
    doc = await codes.find_one({"_id": code, "type": kind})
    if not doc:
        return await update.effective_message.reply_text("❌ Invalid or expired code.")
    if int(doc.get("quantity", 0)) <= 0:
        return await update.effective_message.reply_text("❌ This code has no uses remaining.")
    if uid in doc.get("used", []):
        return await update.effective_message.reply_text("❌ You have already redeemed this code.")
    claimed = await codes.find_one_and_update(
        {"_id": code, "quantity": {"$gt": 0}, "used": {"$ne": uid}},
        {"$inc": {"quantity": -1}, "$addToSet": {"used": uid}},
        return_document=ReturnDocument.BEFORE,
    )
    if not claimed:
        return await update.effective_message.reply_text("❌ Code already used or exhausted.")
    try:
        if kind == "cash":
            await credit_balance(uid, "cash", int(claimed["amount"]))
            result = f"💰 +{int(claimed['amount']):,} Cash added to your balance."
        elif kind == "diamonds":
            await credit_balance(uid, "diamonds", int(claimed["amount"]))
            result = f"💎 +{int(claimed['amount']):,} Diamonds added to your balance."
        else:
            char = await by_id(claimed["waifu_id"])
            if not char:
                raise RuntimeError("Waifu not found")
            await add_character(uid, char, "dredeem")
            result = f"🎴 {char.get('name')} added to your /collection."
    except Exception:
        await codes.update_one(
            {"_id": code}, {"$inc": {"quantity": 1}, "$pull": {"used": uid}}
        )
        return await update.effective_message.reply_text("❌ Reward could not be delivered. The code was restored.")
    await audit_logs.insert_one({
        "action": "redeem", "user_id": uid, "code": code,
        "reward_type": kind, "created_at": datetime.utcnow(),
    })
    await update.effective_message.reply_text(
        f"🎉 CODE REDEEMED SUCCESSFULLY!\n{result}\nRemaining uses: {claimed['quantity'] - 1}"
    )


async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.effective_message.reply_text("Usage: /redeem <code>")
    code = context.args[0].upper()
    doc = await codes.find_one({"_id": code, "type": {"$in": ["cash", "diamonds"]}})
    if not doc:
        return await update.effective_message.reply_text("❌ Invalid Cash/Diamond code. Use /dredeem for waifu codes.")
    await _redeem(update, code, doc["type"])


async def dredeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.effective_message.reply_text("Usage: /dredeem <code>")
    await _redeem(update, context.args[0].upper(), "waifu")


def register(app):
    app.add_handler(CommandHandler("gen", gen))
    app.add_handler(CommandHandler("dgen", dgen))
    app.add_handler(CommandHandler("redeem", redeem))
    app.add_handler(CommandHandler("dredeem", dredeem))
