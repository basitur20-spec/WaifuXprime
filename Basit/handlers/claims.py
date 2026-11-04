"""One-time global claim events created by the bot owner."""
import uuid
from datetime import datetime
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from ..config import load_settings
from ..database import claim_events, user_claims, credit_balance, add_character, audit_logs
from ..services.characters import by_id

settings = load_settings()


async def gclaim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    args = context.args
    if args and args[0].casefold() == "off":
        await claim_events.update_one({"_id": "active"}, {"$set": {"active": False}})
        return await update.effective_message.reply_text("✅ Active claim event disabled.")
    if not args:
        return await update.effective_message.reply_text(
            "Usage:\n/gclaim <cash_amount> [quantity]\n"
            "/gclaim <diamond_amount> d [quantity]\n"
            "/gclaim waifu <waifu_id> [quantity]\n/gclaim off"
        )
    reward_type, reward, quantity = "cash", None, 1000000
    try:
        if args[0].casefold() == "waifu":
            if len(args) < 2:
                raise ValueError
            reward = str(args[1])
            if len(args) > 2:
                quantity = int(args[2])
            if not await by_id(reward):
                return await update.effective_message.reply_text("❌ Waifu ID not found.")
            reward_type = "waifu"
        elif len(args) > 1 and args[1].casefold() in {"d", "diamond", "diamonds"}:
            reward_type = "diamonds"
            reward = int(args[0].replace(",", ""))
            if len(args) > 2:
                quantity = int(args[2])
        else:
            reward = int(args[0].replace(",", ""))
            if len(args) > 1:
                quantity = int(args[1])
    except ValueError:
        return await update.effective_message.reply_text("❌ Invalid reward or quantity.")
    if not reward or quantity < 1 or quantity > 1_000_000:
        return await update.effective_message.reply_text("Reward and quantity must be positive.")
    event_id = uuid.uuid4().hex
    await claim_events.update_one(
        {"_id": "active"},
        {"$set": {
            "active": True, "event_id": event_id,
            "reward_type": reward_type, "reward": reward,
            "quantity": quantity, "created_by": update.effective_user.id,
            "created_at": datetime.utcnow(),
        }},
        upsert=True,
    )
    await audit_logs.insert_one({"action": "gclaim_create", "event_id": event_id, "actor_id": update.effective_user.id, "created_at": datetime.utcnow()})
    await update.effective_message.reply_text(
        f"✅ CLAIM EVENT CREATED\nType: {reward_type}\nReward: {reward}\n"
        f"Available claims: {quantity}\nUsers can now use /claim."
    )


async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    event = await claim_events.find_one({"_id": "active", "active": True, "quantity": {"$gt": 0}})
    if not event:
        return await update.effective_message.reply_text("😔 No rewards are available to claim right now.")
    claim_key = f"{event['event_id']}:{uid}"
    try:
        await user_claims.insert_one({
            "_id": claim_key, "event_id": event["event_id"],
            "user_id": uid, "claimed_at": datetime.utcnow(),
        })
    except Exception:
        return await update.effective_message.reply_text("❌ You have already claimed this event reward.")
    reserved = await claim_events.find_one_and_update(
        {"_id": "active", "active": True, "event_id": event["event_id"], "quantity": {"$gt": 0}},
        {"$inc": {"quantity": -1}},
        return_document=__import__("pymongo").ReturnDocument.BEFORE,
    )
    if not reserved:
        await user_claims.delete_one({"_id": claim_key})
        return await update.effective_message.reply_text("😔 This event's rewards have just run out.")
    try:
        if event["reward_type"] == "cash":
            await credit_balance(uid, "cash", int(event["reward"]))
            reward_text = f"💰 +{int(event['reward']):,} Cash"
        elif event["reward_type"] == "diamonds":
            await credit_balance(uid, "diamonds", int(event["reward"]))
            reward_text = f"💎 +{int(event['reward']):,} Diamonds"
        else:
            char = await by_id(str(event["reward"]))
            if not char:
                raise RuntimeError("Event reward waifu not found")
            await add_character(uid, char, "event_claim")
            reward_text = f"🎴 {char.get('name')} added to your /collection."
    except Exception:
        await claim_events.update_one({"_id": "active", "event_id": event["event_id"]}, {"$inc": {"quantity": 1}})
        await user_claims.delete_one({"_id": claim_key})
        return await update.effective_message.reply_text("❌ Reward could not be delivered. Please try again.")
    await audit_logs.insert_one({"action": "claim", "user_id": uid, "event_id": event["event_id"], "created_at": datetime.utcnow()})
    await update.effective_message.reply_text(f"🎉 CONGRATULATIONS!\nYou claimed your event reward.\n{reward_text}")


def register(app):
    app.add_handler(CommandHandler("gclaim", gclaim))
    app.add_handler(CommandHandler("claim", claim))
