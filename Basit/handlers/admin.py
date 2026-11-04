"""Owner/uploader permissions, waifu upload/delete, and staff gifting."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from pymongo import ReturnDocument
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..config import RARITIES, load_settings, normalize_rarity, rarity_meta
from ..database import (
    characters, user_collection, settings_collection, pending_actions,
    sequences, get_user, credit_balance, audit_logs,
)
from ..services.characters import by_id, image_of, details

settings = load_settings()


async def _roles():
    doc = await settings_collection.find_one({"_id": "roles"}) or {}
    sudo = set(settings.sudo_users) | {int(x) for x in doc.get("sudo", [])}
    uploaders = set(settings.uploader_users) | {int(x) for x in doc.get("uploaders", [])}
    return sudo, uploaders


async def is_staff(uid):
    sudo, uploaders = await _roles()
    return uid == settings.owner_id or uid in sudo or uid in uploaders


async def can_upload(uid):
    _, uploaders = await _roles()
    return uid == settings.owner_id or uid in uploaders


async def _next_waifu_id():
    while True:
        seq = await sequences.find_one_and_update(
            {"_id": "waifu_id"}, {"$inc": {"value": 1}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        candidate = str(1000 + ((int(seq["value"]) - 1) % 9000)).zfill(4)
        exists = await characters.find_one({"id": candidate}, {"_id": 1})
        if not exists:
            return candidate


def _upload_fields(context):
    raw = " ".join(context.args).strip()
    if "|" in raw:
        parts = [x.strip() for x in raw.split("|")]
    else:
        parts = list(context.args)
    if len(parts) < 3:
        return None
    name = parts[0].strip()
    rarity_key = normalize_rarity(parts[1])
    if rarity_key not in RARITIES:
        return None
    price = None
    anime_parts = parts[2:]
    if anime_parts and anime_parts[-1].replace(",", "").isdigit():
        price = int(anime_parts.pop().replace(",", ""))
    anime = " ".join(anime_parts).strip()
    if not name or not anime:
        return None
    low, high = RARITIES[rarity_key]["price"]
    if price is not None and not low <= price <= high:
        raise ValueError(f"Price must be between {low:,} and {high:,} for {RARITIES[rarity_key]['name']}.")
    return name, rarity_key, anime, price


async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    if not await can_upload(uid):
        return  # silent ignore for non-owner/non-uploader
    photo_message = msg if msg.photo else (msg.reply_to_message if msg.reply_to_message and msg.reply_to_message.photo else None)
    if not photo_message:
        return await msg.reply_text(
            "📥 Upload a waifu photo, then reply with:\n"
            "/upload Name | Rarity | Anime | OptionalPrice\n"
            "Example: /upload Son Goku | Ultimate | Dragon Ball Z | 58000"
        )
    try:
        parsed = _upload_fields(context)
    except ValueError as exc:
        return await msg.reply_text(f"❌ INVALID PRICE\n{exc}")
    if not parsed:
        return await msg.reply_text("❌ Missing or invalid fields. Use: /upload Name | Rarity | Anime | OptionalPrice")
    name, rarity_key, anime, price = parsed
    meta = RARITIES[rarity_key]
    low, high = meta["price"]
    price = price if price is not None else __import__("random").randint(low, high)
    file_id = photo_message.photo[-1].file_id
    wid = await _next_waifu_id()
    char = {
        "id": wid, "name": name, "anime": anime,
        "rarity": meta["name"], "rarity_key": rarity_key,
        "rarity_emoji": meta["emoji"], "img_url": file_id,
        "price": int(price), "uploader_id": uid, "uploaded_at": datetime.utcnow(),
    }
    channel_post = None
    try:
        if settings.character_channel_id:
            channel_post = await context.bot.send_photo(
                chat_id=settings.character_channel_id,
                photo=file_id,
                caption=details(char),
            )
            char["message_id"] = channel_post.message_id
        await characters.insert_one(char)
    except Exception:
        if channel_post:
            try:
                await context.bot.delete_message(settings.character_channel_id, channel_post.message_id)
            except Exception:
                pass
        LOGGER = __import__("logging").getLogger(__name__)
        LOGGER.exception("Upload failed")
        return await msg.reply_text("❌ Upload failed. No waifu was registered; please check the bot's upload-channel permissions.")
    reward = int(price * 0.03)
    await credit_balance(uid, "cash", reward)
    await audit_logs.insert_one({
        "action": "upload", "actor_id": uid, "waifu_id": wid,
        "reward": reward, "created_at": datetime.utcnow(),
    })
    await msg.reply_photo(
        file_id,
        caption=(
            details(char) + "\n🎉 Upload Successful!\n"
            "Added to the global waifu database.\n"
            f"💰 Uploader reward: +{reward:,} Cash (3%)"
        ),
    )


async def dupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_staff(uid):
        return
    msg = update.effective_message
    wid = context.args[0] if context.args else None
    if not wid and msg.reply_to_message:
        text = msg.reply_to_message.caption or msg.reply_to_message.text or ""
        match = re.search(r"(?:ID|🆔)\s*[:：]?\s*(\d+)", text, re.I)
        wid = match.group(1) if match else None
    if not wid:
        return await msg.reply_text("Usage: /dupload <waifu_id> or reply to a waifu post and use /dupload")
    char = await by_id(wid)
    if not char:
        return await msg.reply_text("❌ WAIFU NOT FOUND. It may already be deleted.")
    token = uuid.uuid4().hex
    await pending_actions.insert_one({
        "_id": token, "type": "delete_waifu", "status": "pending",
        "actor_id": uid, "waifu_id": str(wid),
        "expires_at": datetime.utcnow() + __import__("datetime").timedelta(minutes=2),
    })
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm Delete", callback_data=f"adm:delete:{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"adm:cancel:{token}"),
    ]])
    await msg.reply_photo(image_of(char), caption=details(char) + "\n\n⚠️ This deletes the waifu from the database and all users' collections. Continue?",
                          reply_markup=markup)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, token = q.data.split(":")
    pending = await pending_actions.find_one({"_id": token})
    if not pending or pending.get("status") != "pending":
        return await q.answer("This action has expired.", show_alert=True)
    if q.from_user.id != pending["actor_id"]:
        return await q.answer("This action is not yours.", show_alert=True)
    await q.answer()
    if action == "cancel":
        await pending_actions.update_one({"_id": token, "status": "pending"}, {"$set": {"status": "cancelled"}})
        return await q.message.delete()
    claimed = await pending_actions.find_one_and_update(
        {"_id": token, "status": "pending"},
        {"$set": {"status": "processing"}},
        return_document=ReturnDocument.BEFORE,
    )
    if not claimed:
        return await q.edit_message_caption(caption="This action has already been processed.")
    wid = claimed["waifu_id"]
    char = await characters.find_one_and_delete({"id": wid})
    if not char:
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.edit_message_caption(caption="Waifu not found; no changes were made.")
    await user_collection.update_many(
        {"characters.id": wid},
        {"$pull": {"characters": {"id": wid}, "favorites": wid}},
    )
    await audit_logs.insert_one({
        "action": "dupload", "actor_id": q.from_user.id, "waifu_id": wid,
        "created_at": datetime.utcnow(),
    })
    await pending_actions.update_one({"_id": token}, {"$set": {"status": "completed"}})
    await q.edit_message_caption(caption=f"✅ WAIFU DELETED SUCCESSFULLY\n{char.get('name')} | ID: {wid}\nRemoved from all user collections.")


async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_staff(uid):
        return
    msg = update.effective_message
    if not context.args:
        return await msg.reply_text("Usage: reply to a user and use /give <waifu_id>, or /give <waifu_id> @username")
    wid = context.args[0]
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    target_spec = context.args[1] if len(context.args) > 1 else None
    if target_spec and target_spec.startswith("@"):
        target_doc = await user_collection.find_one({"username": target_spec[1:]})
        if not target_doc:
            return await msg.reply_text("Target user not found. They must start the bot first.")
        target_id = int(target_doc["id"])
        target_name = target_doc.get("first_name") or target_spec
    elif target_spec and target_spec.isdigit():
        target_doc = await user_collection.find_one({"id": int(target_spec)})
        if not target_doc:
            return await msg.reply_text("Target user ID is not registered. They must start the bot first.")
        target_id = int(target_doc["id"])
        target_name = target_doc.get("first_name") or target_doc.get("username") or target_spec
    elif target:
        target_id, target_name = target.id, target.full_name
    else:
        return await msg.reply_text("Reply to a user or specify @username or numeric user ID.")
    char = await by_id(wid)
    if not char:
        return await msg.reply_text("⚠️ Invalid waifu ID.")
    if target_id == uid and uid != settings.owner_id:
        return await msg.reply_text("🚫 You cannot gift a waifu to yourself.")
    token = uuid.uuid4().hex
    await pending_actions.insert_one({
        "_id": token, "type": "staff_gift", "status": "pending",
        "actor_id": uid, "target_id": target_id, "waifu_id": str(wid),
        "expires_at": datetime.utcnow() + __import__("datetime").timedelta(minutes=2),
    })
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm Gift", callback_data=f"adm:gift:{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"adm:cancel:{token}"),
    ]])
    await msg.reply_photo(image_of(char), caption=f"🎁 CONFIRM WAIFU GIFT\n{details(char)}\nTo: {target_name}",
                          reply_markup=markup)


async def staff_gift_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, token = q.data.split(":")
    pending = await pending_actions.find_one({"_id": token, "type": "staff_gift", "status": "pending"})
    if not pending:
        return await q.answer("This gift has expired.", show_alert=True)
    if q.from_user.id != pending["actor_id"]:
        return await q.answer("This gift request is not yours.", show_alert=True)
    await q.answer()
    if action == "cancel":
        await pending_actions.update_one({"_id": token, "status": "pending"}, {"$set": {"status": "cancelled"}})
        return await q.edit_message_caption(caption="❌ Gift cancelled. No changes were made.")
    pending = await pending_actions.find_one_and_update(
        {"_id": token, "status": "pending"},
        {"$set": {"status": "processing"}},
        return_document=ReturnDocument.BEFORE,
    )
    if not pending:
        return await q.edit_message_caption(caption="This gift has already been processed.")
    char = await by_id(pending["waifu_id"])
    if not char:
        await pending_actions.update_one({"_id": token}, {"$set": {"status": "failed"}})
        return await q.edit_message_caption(caption="Waifu no longer exists.")
    from ..database import add_character
    await get_user(pending["target_id"])
    await add_character(pending["target_id"], char, "staff_gift")
    await audit_logs.insert_one({
        "action": "give", "actor_id": pending["actor_id"],
        "target_id": pending["target_id"], "waifu_id": pending["waifu_id"],
        "created_at": datetime.utcnow(),
    })
    await pending_actions.update_one({"_id": token}, {"$set": {"status": "completed"}})
    await q.edit_message_caption(caption=f"✅ WAIFU GIFTED SUCCESSFULLY!\nID: {pending['waifu_id']}\nAdded to recipient's /collection.")
    try:
        await context.bot.send_photo(
            chat_id=pending["target_id"], photo=image_of(char),
            caption="🎁 YOU RECEIVED A WAIFU!\n" + details(char) + "\nAdded to your /collection 💖",
        )
    except Exception:
        pass


async def role_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != settings.owner_id:
        return
    msg = update.effective_message
    command = msg.text.split()[0].split("@")[0].lower()
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if not target:
        return await msg.reply_text("Reply to a user and use this command.")
    sudo, uploaders = await _roles()
    if command == "/addsudo":
        sudo.add(target.id)
    elif command == "/rsudo":
        sudo.discard(target.id)
    elif command == "/adduploader":
        uploaders.add(target.id)
    elif command == "/ruploader":
        uploaders.discard(target.id)
    await settings_collection.update_one(
        {"_id": "roles"},
        {"$set": {"sudo": sorted(sudo), "uploaders": sorted(uploaders)}},
        upsert=True,
    )
    await msg.reply_text(f"✅ Updated permissions for {target.full_name}.")
    if command in {"/addsudo", "/adduploader"}:
        role_name = "SUDO" if command == "/addsudo" else "UPLOADER"
        callback = "SUDO_GUIDE" if command == "/addsudo" else "UPLOADER_GUIDE"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("COMMANDS 📜", callback_data=callback)
        ]])
        try:
            await context.bot.send_message(
                chat_id=target.id,
                text=f"🎉 Congratulations! You have been promoted to {role_name}.\\nTap below to view your commands.",
                reply_markup=keyboard,
            )
        except Exception:
            await msg.reply_text(
                f"⚠️ Role updated, but I could not DM {target.full_name}. They must start the bot privately first."
            )
    elif command in {"/rsudo", "/ruploader"}:
        try:
            await context.bot.send_message(
                chat_id=target.id,
                text="⚠️ Your staff access has been revoked by the owner."
            )
        except Exception:
            pass


async def role_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    sudo, uploaders = await _roles()
    text = "👑 SUDO USERS\n" + ("\n".join(map(str, sorted(sudo))) or "None")
    text += "\n\n📤 UPLOADERS\n" + ("\n".join(map(str, sorted(uploaders))) or "None")
    await update.effective_message.reply_text(text)



async def _role_guide_text(role_name):
    if role_name == "sudo":
        return (
            "🛡️ SUDO COMMAND PANEL\n\n"
            "/auction <waifu_id> <starting_bid> [hours]\n/auction users\n"
            "/changetime <minutes>\n/gen <amount> <quantity> [cash|diamonds]\n"
            "/dgen <waifu_id> <quantity>\n/dupload <waifu_id>\n"
            "/give <waifu_id> @username\n/dtops"
        )
    return (
        "📤 UPLOADER COMMAND PANEL\n\n"
        "/upload — Upload waifu photo\n/dupload <waifu_id>\n"
        "/give <waifu_id> @username\n/dgen <waifu_id> <quantity>\n"
        "/auction <waifu_id> <starting_bid> [hours]\n/auction users\n/dtops"
    )


async def sudolist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    sudo, _ = await _roles()
    if not sudo:
        return await update.effective_message.reply_text("No SUDO users configured.")
    rows = []
    for uid in sorted(sudo):
        try:
            user = await context.bot.get_chat(uid)
            name = user.full_name or "Unknown"
            username = f" (@{user.username})" if user.username else ""
        except Exception:
            name, username = "Unknown", ""
        rows.append(f"{name}{username} | ID: {uid}")
    await update.effective_message.reply_text("👑 SUDO USERS\n\n" + "\n".join(rows))


async def uploaderlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    _, uploaders = await _roles()
    if not uploaders:
        return await update.effective_message.reply_text("No uploaders configured.")
    rows = []
    for uid in sorted(uploaders):
        try:
            user = await context.bot.get_chat(uid)
            name = user.full_name or "Unknown"
            username = f" (@{user.username})" if user.username else ""
        except Exception:
            name, username = "Unknown", ""
        rows.append(f"{name}{username} | ID: {uid}")
    await update.effective_message.reply_text("📤 UPLOADER USERS\n\n" + "\n".join(rows))


async def sudocommands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sudo, _ = await _roles()
    if update.effective_user.id != settings.owner_id and update.effective_user.id not in sudo:
        return
    text = await _role_guide_text("sudo")
    if update.effective_message.chat.type != "private":
        try:
            await context.bot.send_message(chat_id=update.effective_user.id, text=text)
            return await update.effective_message.reply_text("✅ SUDO command guide sent in your DM.")
        except Exception:
            return await update.effective_message.reply_text("Start the bot in private first.")
    await update.effective_message.reply_text(text)


async def uploadercommands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, uploaders = await _roles()
    if update.effective_user.id != settings.owner_id and update.effective_user.id not in uploaders:
        return
    text = await _role_guide_text("uploader")
    if update.effective_message.chat.type != "private":
        try:
            await context.bot.send_message(chat_id=update.effective_user.id, text=text)
            return await update.effective_message.reply_text("✅ Uploader command guide sent in your DM.")
        except Exception:
            return await update.effective_message.reply_text("Start the bot in private first.")
    await update.effective_message.reply_text(text)


async def role_guide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    sudo, uploaders = await _roles()
    uid = q.from_user.id
    if q.data == "SUDO_GUIDE" and (uid == settings.owner_id or uid in sudo):
        text = await _role_guide_text("sudo")
    elif q.data == "UPLOADER_GUIDE" and (uid == settings.owner_id or uid in uploaders):
        text = await _role_guide_text("uploader")
    else:
        return await q.answer("This guide is not assigned to your account.", show_alert=True)
    await q.answer()
    await q.message.reply_text(text)


def register(app):
    app.add_handler(CommandHandler("upload", upload))
    app.add_handler(CommandHandler("dupload", dupload))
    app.add_handler(CommandHandler("give", give))
    for cmd in ("addsudo", "rsudo", "adduploader", "ruploader"):
        app.add_handler(CommandHandler(cmd, role_change))
    app.add_handler(CommandHandler("stafflist", role_list))
    app.add_handler(CommandHandler("sudolist", sudolist))
    app.add_handler(CommandHandler("uploaderlist", uploaderlist))
    app.add_handler(CommandHandler("sudocommands", sudocommands))
    app.add_handler(CommandHandler("uploadercommands", uploadercommands))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:delete:|^adm:cancel:"))
    app.add_handler(CallbackQueryHandler(staff_gift_callback, pattern=r"^adm:gift:"))
    app.add_handler(CallbackQueryHandler(role_guide_callback, pattern=r"^(SUDO_GUIDE|UPLOADER_GUIDE)$"))
