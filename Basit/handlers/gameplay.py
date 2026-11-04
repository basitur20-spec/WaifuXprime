"""Group spawning, hunt, daily waifu, marriage, propose, and rarity controls."""
from __future__ import annotations

import os
import random
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
)
from telegram.constants import ChatType, ChatMemberStatus
from telegram.ext import CommandHandler, MessageHandler, ContextTypes, filters

from ..config import RARITIES, load_settings, normalize_rarity, rarity_meta
from ..database import (
    groups, settings_collection, user_collection, get_user, debit_balance,
    adjust_balance,
)
from ..services.characters import (
    enabled_spawn_rarities, random_character, grant, image_of, name_matches,
    image_caption,
)

settings = load_settings()
SPAM_WINDOW = 5
SPAM_LIMIT = 5
MUTE_SECONDS = 300
SPAWN_LOCKS = defaultdict(__import__("asyncio").Lock)
SPAM_EVENTS = defaultdict(deque)
MUTED_UNTIL = {}


def _now():
    return datetime.utcnow()


def _duration(seconds):
    seconds = max(0, int(seconds))
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"


async def is_muted(chat_id, user_id):
    return MUTED_UNTIL.get((chat_id, user_id), 0) > time.time()


async def _apply_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    key = (message.chat_id, user.id)
    MUTED_UNTIL[key] = time.time() + MUTE_SECONDS
    try:
        await context.bot.restrict_chat_member(
            chat_id=message.chat_id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=_now() + timedelta(seconds=MUTE_SECONDS),
        )
    except Exception:
        # The in-process guard still suppresses this member's bot commands.
        pass
    await message.reply_text(
        f"{user.first_name}, your messages won't be counted for 5 minutes.\n"
        "You cannot spam to get characters to this Bot."
    )


async def group_message_counter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user or user.is_bot or message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if message.text and message.text.lstrip().startswith("/"):
        return

    key = (message.chat_id, user.id)
    now = time.time()
    if MUTED_UNTIL.get(key, 0) > now:
        return

    events = SPAM_EVENTS[key]
    while events and now - events[0] > SPAM_WINDOW:
        events.popleft()
    events.append(now)
    if len(events) >= SPAM_LIMIT:
        events.clear()
        await _apply_mute(update, context)
        return

    # Ordinary text, stickers, GIFs, photos, videos and other human messages count.
    valid = bool(
        message.text or message.sticker or message.animation or message.photo
        or message.video or message.document or message.voice or message.video_note
    )
    if not valid:
        return

    async with SPAWN_LOCKS[message.chat_id]:
        state = await groups.find_one({"_id": message.chat_id}) or {}
        active = state.get("active_spawn")
        if active and active.get("expires_at") and active["expires_at"] > _now():
            return
        if active:
            await groups.update_one({"_id": message.chat_id}, {"$unset": {"active_spawn": ""}})
            state["active_spawn"] = None

        count = int(state.get("message_count", 0)) + 1
        every = int(state.get("spawn_every_messages", settings.spawn_every_messages))
        last_spawn = state.get("last_spawn_at")
        minutes = int(state.get("spawn_interval_minutes", 0))
        interval_ready = not last_spawn or _now() >= last_spawn + timedelta(minutes=minutes)
        await groups.update_one(
            {"_id": message.chat_id},
            {"$set": {"message_count": count, "spawn_every_messages": every}},
            upsert=True,
        )
        if count < every or not interval_ready:
            return

        allowed = await enabled_spawn_rarities()
        character = await random_character(allowed)
        if not character:
            return
        spawn_key = uuid.uuid4().hex
        expires = _now() + timedelta(seconds=settings.spawn_lifetime_seconds)
        caption = (
            "✨ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀꜱ ᴀᴘᴘᴇᴀʀᴇᴅ! ✨\n"
            "ᴜꜱᴇ /hunt (ɴᴀᴍᴇ) ᴛᴏ ᴀᴅᴅ ɪᴛ ɪɴ ʏᴏᴜʀ COLLECTION."
        )
        sent = await context.bot.send_photo(
            chat_id=message.chat_id,
            photo=image_of(character),
            caption=caption,
            has_spoiler=True,
        )
        await groups.update_one(
            {"_id": message.chat_id},
            {"$set": {
                "message_count": 0,
                "last_spawn_at": _now(),
                "active_spawn": {
                    "spawn_key": spawn_key,
                    "character": {
                        "id": str(character.get("id")),
                        "name": character.get("name"),
                        "anime": character.get("anime"),
                        "rarity": character.get("rarity"),
                        "img_url": image_of(character),
                        "price": int(character.get("price", 0) or 0),
                    },
                    "message_id": sent.message_id,
                    "expires_at": expires,
                    "claimed_by": None,
                },
            }},
            upsert=True,
        )
        if context.job_queue:
            context.job_queue.run_once(
                expire_spawn, when=settings.spawn_lifetime_seconds,
                data={"chat_id": message.chat_id, "spawn_key": spawn_key},
                name=f"spawn-expire:{message.chat_id}:{spawn_key}",
            )


async def expire_spawn(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id, spawn_key = data["chat_id"], data["spawn_key"]
    state = await groups.find_one({"_id": chat_id, "active_spawn.spawn_key": spawn_key})
    if not state:
        return
    spawn = state.get("active_spawn", {})
    if spawn.get("claimed_by") is not None:
        return
    character = spawn.get("character", {})
    result = await groups.update_one(
        {"_id": chat_id, "active_spawn.spawn_key": spawn_key, "active_spawn.claimed_by": None},
        {"$unset": {"active_spawn": ""}, "$set": {"message_count": 0}},
    )
    if result.modified_count:
        meta = rarity_meta(str(character.get("rarity", "")))
        text = (
            "❌ ᴛᴏᴏ ꜱʟᴏᴡ! ᴛʜᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴇꜱᴄᴀᴘᴇᴅ! 🏃‍♀️✨\n\n"
            f'🌸 ɴᴀᴍᴇ: {character.get("name", "Unknown")}\n'
            f'💠 ᴀɴɪᴍᴇ: {character.get("anime", "Unknown")}\n'
            f'✨ ʀᴀʀɪᴛʏ: {meta["emoji"]} {meta["name"]}\n'
            f'🆔 ɪᴅ: {character.get("id", "—")}'
        )
        await context.bot.send_message(chat_id=chat_id, text=text)


async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await msg.reply_text("Use /hunt in a group when a character has spawned.")
    if await is_muted(msg.chat_id, user.id):
        return
    if not context.args:
        return await msg.reply_text("Usage: /hunt <character name>")
    state = await groups.find_one({"_id": msg.chat_id}) or {}
    spawn = state.get("active_spawn")
    now = _now()
    if not spawn or spawn.get("expires_at") <= now:
        return await msg.reply_text("❌ NO CHARACTER HAS SPAWNED YET. PLEASE WAIT FOR THE NEXT SPAWN.")
    character = spawn.get("character", {})
    query = " ".join(context.args)
    if not name_matches(str(character.get("name", "")), query):
        return await msg.reply_text("❌ NO CHARACTER HAS SPAWNED YET. PLEASE WAIT FOR THE NEXT SPAWN.")
    claimed = await groups.find_one_and_update(
        {"_id": msg.chat_id, "active_spawn.spawn_key": spawn["spawn_key"],
         "active_spawn.claimed_by": None, "active_spawn.expires_at": {"$gt": now}},
        {"$set": {"active_spawn.claimed_by": user.id}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        return await msg.reply_text("❌ This character has already been claimed or escaped.")
    full = await characters_lookup(character.get("id"))
    await grant(user.id, full or character, "hunt")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("COLLECTION 🔥", switch_inline_query_current_chat=f"collection.{user.id}")
    ]])
    await msg.reply_text(
        f"🎉 Congratulations {user.first_name}! You got a new waifu!\n"
        f'🌸 NAME: {character.get("name")}\n'
        f'🧩 ANIME: {character.get("anime")}\n'
        f'✨ RARITY: {rarity_meta(str(character.get("rarity", "")))["emoji"]} {character.get("rarity")}\n'
        f'🆔 ID: {character.get("id")}\n\nCheck your /collection now.',
        reply_markup=keyboard,
    )
    await groups.update_one({"_id": msg.chat_id, "active_spawn.spawn_key": spawn["spawn_key"]},
                            {"$unset": {"active_spawn": ""}, "$set": {"message_count": 0}})


async def characters_lookup(value):
    from ..services.characters import by_id
    return await by_id(str(value))


async def spawn_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != settings.owner_id:
        return
    if len(context.args) != 2:
        return await update.effective_message.reply_text("Usage: /spawn <rarity> <on|off>")
    rarity = normalize_rarity(context.args[0])
    mode = context.args[1].lower()
    if rarity not in RARITIES or mode not in {"on", "off"}:
        return await update.effective_message.reply_text("Invalid rarity or status. Use /spawn CrossArc on")
    enabled = mode == "on"
    await settings_collection.update_one(
        {"_id": "spawn_rarities"},
        {"$set": {f"enabled.{rarity}": enabled}},
        upsert=True,
    )
    await update.effective_message.reply_text(
        f'{rarity_meta(rarity)["emoji"]} {rarity_meta(rarity)["name"]} spawning is now {mode.upper()}.'
    )


async def changetime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.effective_message
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await msg.reply_text("Use /changetime inside the group.")
    allowed = uid == settings.owner_id or uid in settings.sudo_users or uid in settings.uploader_users
    if not allowed:
        try:
            member = await context.bot.get_chat_member(msg.chat_id, uid)
            allowed = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        except Exception:
            allowed = False
    if not allowed:
        return await msg.reply_text("Only group admins, owner, SUDO or uploaders can change the spawn interval.")
    if len(context.args) != 1 or not context.args[0].isdigit():
        return await msg.reply_text("Usage: /changetime 5 (2–120 minutes)")
    minutes = int(context.args[0])
    if not 2 <= minutes <= 120:
        return await msg.reply_text("Spawn interval must be between 2 and 120 minutes.")
    await groups.update_one({"_id": msg.chat_id}, {"$set": {"spawn_interval_minutes": minutes}}, upsert=True)
    await msg.reply_text(f"⏱️ Spawn interval updated: every {minutes} minutes (and after the configured message count).")

async def _claim_with_cooldown(update, command_key, seconds, allowed_rarities):
    user = update.effective_user
    msg = update.effective_message
    await get_user(user.id, user)
    character = await random_character(allowed_rarities)
    if not character:
        return await msg.reply_text("No waifus are uploaded for this reward pool yet.")
    now = _now()
    result = await user_collection.find_one_and_update(
        {"id": user.id, "$or": [
            {f"{command_key}_next_at": {"$exists": False}},
            {f"{command_key}_next_at": {"$lte": now}},
        ]},
        {"$set": {f"{command_key}_next_at": now + timedelta(seconds=seconds)}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        current = await user_collection.find_one({"id": user.id}) or {}
        next_at = current.get(f"{command_key}_next_at", now)
        remaining = max(0, int((next_at - now).total_seconds()))
        return await msg.reply_text(f"⏳ Please wait {_duration(remaining)} before your next claim.")
    await grant(user.id, character, command_key)
    await msg.reply_photo(image_of(character), caption=image_caption(user, character))


async def waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _claim_with_cooldown(update, "waifu", 12 * 60 * 60,
                               ["Starter", "Rising", "Shiny", "Ultimate", "RoyalArc"])


async def marriage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    await get_user(user.id, user)
    now = _now()
    current = await user_collection.find_one({"id": user.id}) or {}
    next_at = current.get("marriage_next_at")
    if next_at and next_at > now:
        return await msg.reply_text(f"💍 Try again in {_duration((next_at-now).total_seconds())}.")
    await user_collection.update_one({"id": user.id}, {"$set": {"marriage_next_at": now + timedelta(hours=1)}})
    if random.random() >= 0.35:
        return await msg.reply_text(
            f"{user.first_name}, ʏᴏᴜʀ ᴍᴀʀʀɪᴀɢᴇ ᴘʀᴏᴘᴏꜱᴀʟ ᴡᴀꜱ ʀᴇᴊᴇᴄᴛᴇᴅ ᴀɴᴅ ꜱʜᴇ ʀᴀɴ ᴀᴡᴀʏ! 🤡"
        )
    character = await random_character(["Shiny", "Ultimate"])
    if not character:
        return await msg.reply_text("Marriage reward pool is empty. Please contact the bot owner.")
    await grant(user.id, character, "marriage")
    caption = (
        f"Congratulations {user.first_name}, you are now married! 💍\n"
        f'NAME: {character.get("name")}\n'
        f'RARITY: {rarity_meta(str(character.get("rarity", "")))["emoji"]} {character.get("rarity")}\n'
        f'ANIME: {character.get("anime")}\n\nAdded to your /collection.'
    )
    await msg.reply_photo(image_of(character), caption=caption)


async def propose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    await get_user(user.id, user)
    now = _now()
    data = await user_collection.find_one({"id": user.id}) or {}
    next_at = data.get("propose_next_at")
    if next_at and next_at > now:
        return await msg.reply_text(
            f"ᴄᴏᴏʟᴅᴏᴡɴ! ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ {_duration((next_at-now).total_seconds())} ʙᴇꜰᴏʀᴇ ᴘʀᴏᴘᴏꜱɪɴɢ ᴀɢᴀɪɴ."
        )
    if not await debit_balance(user.id, "cash", 30000):
        return await msg.reply_text("YOU NEED 30,000 CASH💸 TO PROPOSE.")
    await user_collection.update_one({"id": user.id}, {"$set": {"propose_next_at": now + timedelta(minutes=5)}})

    intro = os.getenv("PROPOSE_INTRO_IMAGE", "").strip()
    if intro:
        first = await msg.reply_photo(intro, caption=f"{user.first_name} 💫 ᴛʜᴇ ᴍᴏᴍᴇɴᴛ ʏᴏᴜ'ᴠᴇ ʙᴇᴇɴ ᴡᴀɪᴛɪɴɢ ꜰᴏʀ 💫")
    else:
        first = await msg.reply_text(f"{user.first_name} 💫 ᴛʜᴇ ᴍᴏᴍᴇɴᴛ ʏᴏᴜ'ᴠᴇ ʙᴇᴇɴ ᴡᴀɪᴛɪɴɢ ꜰᴏʀ 💫")
    second = await context.bot.send_message(
        chat_id=msg.chat_id, text="ᴘʀᴏᴘᴏꜱɪɴɢ ʜᴇʀ.... 💍", reply_to_message_id=first.message_id
    )
    threshold = random.randint(35, 45)
    accepted = random.randint(1, 100) <= threshold
    if accepted:
        character = await random_character(["ChristmasArc", "RoyalArc", "NeonArc", "CelestialArc"])
        if character:
            await grant(user.id, character, "propose")
            result = (
                f'{character.get("name")} has accepted your proposal! 😇\n'
                f'☘️ Name: {character.get("name")}\n'
                f'🵿 Rarity: {character.get("rarity")}\n'
                f'💠 Anime: {character.get("anime")}\n'
                "Saved to your /collection."
            )
            await context.bot.send_photo(
                chat_id=msg.chat_id, photo=image_of(character), caption=result,
                reply_to_message_id=second.message_id,
            )
            return
    rejection_image = random.choice([
        os.getenv("PROPOSE_REJECT_IMAGE_1", "").strip(),
        os.getenv("PROPOSE_REJECT_IMAGE_2", "").strip(),
    ])
    rejection_text = random.choice([
        "ʏᴏᴜ'ᴠᴇ ʙᴇᴇɴ ꜰʀɪᴇɴᴅ-ᴢᴏɴᴇᴅ ꜱᴏ ʜᴀʀᴅ, ʏᴏᴜ'ʀᴇ ɴᴏᴡ ᴛʜᴇ ᴍᴀʏᴏʀ ᴏꜰ ꜰʀɪᴇɴᴅ ᴢᴏɴᴇ ᴄɪᴛʏ! 🏙️",
        "ʙʀᴇᴀᴋɪɴɢ ɴᴇᴡꜱ: ʟᴏᴄᴀʟ ꜱɪᴍᴘ ɢᴇᴛꜱ ʀᴇᴊᴇᴄᴛᴇᴅ ꜱᴏ ʜᴀʀᴅ! 🥲",
    ])
    if rejection_image:
        await context.bot.send_photo(chat_id=msg.chat_id, photo=rejection_image, caption=rejection_text,
                                     reply_to_message_id=second.message_id)
    else:
        await context.bot.send_message(chat_id=msg.chat_id, text=rejection_text,
                                       reply_to_message_id=second.message_id)


def register(app):
    app.add_handler(CommandHandler("hunt", hunt))
    app.add_handler(CommandHandler("waifu", waifu))
    app.add_handler(CommandHandler("marriage", marriage))
    app.add_handler(CommandHandler("propose", propose))
    app.add_handler(CommandHandler("spawn", spawn_toggle))
    app.add_handler(CommandHandler("changetime", changetime))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, group_message_counter), group=1)
