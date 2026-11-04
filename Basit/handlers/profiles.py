"""User profiles and profile-image selection."""
import uuid
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..database import user_collection, pending_actions, characters, get_user
from ..services.characters import image_of, by_id, rarity_meta


async def setprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    if msg.reply_to_message:
        replied = msg.reply_to_message
        if replied.photo:
            photo = replied.photo[-1].file_id
        elif replied.video:
            photo = replied.video.file_id
        elif replied.document and (replied.document.mime_type or "").startswith("image/"):
            photo = replied.document.file_id
        else:
            photo = None
        if photo:
            await get_user(uid, update.effective_user)
            await user_collection.update_one({"id": uid}, {"$set": {"profile_photo": photo}})
            return await msg.reply_text("📸 Profile photo updated successfully!")
    if len(context.args) != 1:
        return await msg.reply_text("Usage: /setprofile <owned_waifu_id> or reply to a photo/video and use /setprofile.")
    wid = context.args[0]
    doc = await user_collection.find_one({"id": uid, "characters.id": wid})
    if not doc:
        return await msg.reply_text("😔 You do not own this waifu. Choose one from your /collection.")
    char = next((x for x in doc.get("characters", []) if str(x.get("id")) == wid), None)
    if not char or not image_of(char):
        return await msg.reply_text("⚠️ This waifu does not have a usable image.")
    token = uuid.uuid4().hex
    await pending_actions.insert_one({
        "_id": token, "type": "set_profile", "status": "pending",
        "user_id": uid, "waifu_id": wid,
        "expires_at": datetime.utcnow() + timedelta(minutes=2),
    })
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"profile:set:{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"profile:cancel:{token}"),
    ]])
    await msg.reply_photo(
        image_of(char),
        caption=f"✨ Are you sure you want to set {char.get('name')} as your profile waifu?",
        reply_markup=keyboard,
    )


async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, token = q.data.split(":")
    pending = await pending_actions.find_one({"_id": token, "type": "set_profile", "status": "pending"})
    if not pending:
        return await q.answer("This selection has expired.", show_alert=True)
    if pending.get("expires_at") and pending["expires_at"] <= datetime.utcnow():
        await pending_actions.update_one({"_id": token, "status": "pending"}, {"$set": {"status": "expired"}})
        return await q.answer("This selection has expired.", show_alert=True)
    if q.from_user.id != pending["user_id"]:
        return await q.answer("This profile selection is not yours.", show_alert=True)
    await q.answer()
    if action == "cancel":
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "cancelled"}})
        return await q.edit_message_caption(caption="❌ Profile update cancelled.")
    char = await by_id(pending["waifu_id"])
    if not char:
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.edit_message_caption(caption="⚠️ This waifu no longer exists.")
    owned = await user_collection.count_documents({"id": pending["user_id"], "characters.id": pending["waifu_id"]})
    if not owned:
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.edit_message_caption(caption="😔 You no longer own this waifu.")
    await user_collection.update_one(
        {"id": pending["user_id"]},
        {"$set": {"profile_waifu": pending["waifu_id"], "profile_photo": image_of(char)}},
    )
    await pending_actions.update_one({"_id": token}, {"$set": {"status": "completed"}})
    await q.edit_message_caption(caption="💖 Profile Updated Successfully! Your waifu remains in your collection.")


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user
    target_id = caller.id
    if update.effective_message.reply_to_message:
        target_id = update.effective_message.reply_to_message.from_user.id
    elif context.args:
        username = context.args[0].lstrip("@")
        target = await user_collection.find_one({"username": username})
        if not target:
            return await update.effective_message.reply_text("⚠️ User not found. They must start the bot first.")
        target_id = int(target["id"])
    doc = await user_collection.find_one({"id": target_id})
    if not doc:
        return await update.effective_message.reply_text("⚠️ User not found. They must start the bot first.")
    name = doc.get("first_name") or f"User {target_id}"
    username = f'@{doc["username"]}' if doc.get("username") else "Not set"
    collection = doc.get("characters", [])
    total_waifus = await characters.count_documents({})
    profile_waifu = None
    if doc.get("profile_waifu"):
        profile_waifu = next((x for x in collection if str(x.get("id")) == str(doc["profile_waifu"])), None)
    image = doc.get("profile_photo") or (image_of(profile_waifu) if profile_waifu else None)
    text = (
        "👤 USER PROFILE\n\n"
        f"🆔 User ID: {target_id}\n👤 Name: {name}\n🔗 Username: {username}\n\n"
        f"💰 Cash: {int(doc.get('cash', 0)):,}\n"
        f"💎 Diamonds: {int(doc.get('diamonds', 0)):,}\n"
        f"💖 Waifus Collected: {len(collection)} / {total_waifus}"
    )
    if profile_waifu:
        meta = rarity_meta(str(profile_waifu.get("rarity", "")))
        text += (
            f'\n\n🌸 Profile Waifu: {profile_waifu.get("name", "Unknown")}\n'
            f'Rarity: {meta["emoji"]} {meta["name"]}\nAnime: {profile_waifu.get("anime", "Unknown")}'
        )
    if image:
        await update.effective_message.reply_photo(image, caption=text)
    else:
        await update.effective_message.reply_text(text + "\n\nℹ️ No profile image set yet.")


def register(app):
    app.add_handler(CommandHandler("setprofile", setprofile))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CallbackQueryHandler(profile_callback, pattern=r"^profile:"))
