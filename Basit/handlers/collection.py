"""Per-user collection, favorites, and display-mode controls."""
from collections import Counter, defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatType

from ..config import RARITIES, normalize_rarity, rarity_meta
from ..database import user_collection, characters
from ..services.characters import image_of, by_id, details

PAGE_SIZE = 10
RARITY_KEYS = list(RARITIES.keys())


def _name(user):
    return f"@{user.username}" if user.username else user.first_name or str(user.id)


def _filter_items(items, mode):
    mode = mode or {"kind": "default", "value": ""}
    kind, value = mode.get("kind", "default"), mode.get("value", "")
    if kind == "rarity":
        return [x for x in items if normalize_rarity(str(x.get("rarity", ""))) == value]
    if kind == "anime":
        return [x for x in items if str(x.get("anime", "")).casefold() == str(value).casefold()]
    if kind == "character":
        return [x for x in items if str(x.get("name", "")).casefold() == str(value).casefold()]
    return items


def _ordered(items, favorites):
    favs = {str(x) for x in favorites or []}
    return sorted(items, key=lambda x: (str(x.get("id")) not in favs,
                                        str(x.get("anime", "")).casefold(),
                                        str(x.get("name", "")).casefold()))


def _render(user, items, page, mode):
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    subset = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    mode_text = "DEFAULT" if mode.get("kind", "default") == "default" else (
        f'{mode["kind"].upper()}: {mode.get("value", "")}'
    )
    text = f"📚 {user.get('first_name', 'USER')}'S WAIFU COLLECTION\n"
    text += f"DISPLAY: {mode_text}\nPAGE {page + 1}/{total_pages}\n\n"
    previous_anime = None
    favs = {str(x) for x in user.get("favorites", [])}
    for item in subset:
        anime = item.get("anime", "Unknown")
        if anime != previous_anime:
            text += f"🎬 {anime}\n"
            previous_anime = anime
        marker = "❤️ " if str(item.get("id")) in favs else ""
        meta = rarity_meta(str(item.get("rarity", "")))
        text += f'{marker}🆔 {item.get("id")} | {meta["emoji"]} | {item.get("name", "Unknown")}\n'
    return text, page, total_pages


def _collection_keyboard(owner_id, target_id, page, total_pages, read_only=False):
    rows = [[InlineKeyboardButton(
        "✨ Waifus",
        callback_data=f"col:images:{owner_id}:{target_id}:{page}"
    )]]
    if not read_only:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"col:prev:{owner_id}:{target_id}:{page}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"col:next:{owner_id}:{target_id}:{page}"))
        if nav:
            rows.append(nav)
    rows.append([InlineKeyboardButton("❌ Close", callback_data=f"col:close:{owner_id}:{target_id}:{page}")])
    return InlineKeyboardMarkup(rows)


async def collection_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user
    target = caller
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    elif context.args and context.args[0].startswith("@"):
        doc = await user_collection.find_one({"username": context.args[0].lstrip("@")})
        if not doc:
            return await update.message.reply_text("⚠️ User not found. They may need to start the bot first.")
        target_id = int(doc["id"])
    else:
        target_id = caller.id

    if target.id != caller.id:
        target_id = target.id
        target_doc = await user_collection.find_one({"id": target_id})
        if not target_doc:
            return await update.message.reply_text("This user has not started the bot yet.")
    else:
        target_doc = await user_collection.find_one({"id": target_id}) or {
            "id": target_id, "characters": [], "favorites": [], "first_name": caller.first_name
        }

    mode = target_doc.get("collection_mode", {"kind": "default", "value": ""})
    items = _ordered(_filter_items(target_doc.get("characters", []), mode), target_doc.get("favorites", []))
    # Always show the favourite image above the text collection for the owner.
    if target_id == caller.id and target_doc.get("favorites"):
        fav = next((x for x in items if str(x.get("id")) == str(target_doc["favorites"][0])), None)
        if fav and image_of(fav):
            await update.message.reply_photo(image_of(fav), caption="❤️ YOUR FAVORITE WAIFU")
    text, page, total = _render(target_doc, items, 0, mode)
    await update.message.reply_text(
        text,
        reply_markup=_collection_keyboard(caller.id, target_id, page, total, read_only=(target_id != caller.id)),
    )


async def collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    _, action, owner_raw, target_raw, page_raw = parts
    owner_id, target_id, page = int(owner_raw), int(target_raw), int(page_raw)
    if q.from_user.id != owner_id:
        return await q.answer("This collection view is not yours.", show_alert=True)
    await q.answer()
    if action == "close":
        return await q.message.delete()
    doc = await user_collection.find_one({"id": target_id}) or {
        "id": target_id, "characters": [], "favorites": [], "first_name": "USER"
    }
    if action == "images":
        items = _ordered(doc.get("characters", []), doc.get("favorites", []))
        mode = doc.get("collection_mode", {"kind": "default", "value": ""})
        items = _filter_items(items, mode)
        subset = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        await q.message.reply_text(f"🖼️ WAIFUS | Page {page + 1}")
        for item in subset:
            if image_of(item):
                await q.message.reply_photo(image_of(item))
        return
    if target_id != owner_id:
        return await q.answer("Other users' collections are read-only.", show_alert=True)
    mode = doc.get("collection_mode", {"kind": "default", "value": ""})
    items = _ordered(_filter_items(doc.get("characters", []), mode), doc.get("favorites", []))
    if action == "next":
        page += 1
    elif action == "prev":
        page = max(0, page - 1)
    text, page, total = _render(doc, items, page, mode)
    await q.edit_message_text(text, reply_markup=_collection_keyboard(owner_id, target_id, page, total))


async def fav_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /fav <waifu_id>")
    wid = context.args[0]
    doc = await user_collection.find_one({"id": user.id, "characters.id": wid})
    if not doc:
        return await update.message.reply_text("❌ You can favorite only a waifu already in your collection.")
    char = next((x for x in doc.get("characters", []) if str(x.get("id")) == wid), None)
    if not char or not image_of(char):
        return await update.message.reply_text("This waifu image is unavailable.")
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("INLINE", switch_inline_query_current_chat=f"fav.{wid}"),
        InlineKeyboardButton("COLLECTION", callback_data=f"fav:set:{user.id}:{wid}"),
        InlineKeyboardButton("CANCEL", callback_data=f"fav:cancel:{user.id}"),
    ]])
    await update.message.reply_photo(
        image_of(char),
        caption=f'Do you really want to set ({char.get("name")}) as your favorite waifu?',
        reply_markup=buttons,
    )


async def fav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, uid, *rest = q.data.split(":")
    uid = int(uid)
    if q.from_user.id != uid:
        return await q.answer("This is not your favorite menu.", show_alert=True)
    await q.answer()
    if action == "cancel":
        return await q.message.delete()
    wid = rest[0]
    doc = await user_collection.find_one({"id": uid, "characters.id": wid})
    if not doc:
        return await q.edit_message_caption("❌ This waifu is no longer in your collection.")
    await user_collection.update_one({"id": uid}, {"$set": {"favorites": [wid]}})
    await q.edit_message_caption("❤️ Favorite waifu saved! It will appear above your collection.")


def _dmode_menu(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("DEFAULT", callback_data=f"dm:default:{uid}:0"),
         InlineKeyboardButton("CHARACTERS", callback_data=f"dm:characters:{uid}:0")],
        [InlineKeyboardButton("RARITY", callback_data=f"dm:rarity:{uid}:0"),
         InlineKeyboardButton("ANIME", callback_data=f"dm:anime:{uid}:0")],
    ])


async def dmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    doc = await user_collection.find_one({"id": uid}) or {}
    mode = doc.get("collection_mode", {"kind": "default", "value": ""})
    await update.message.reply_text(
        f"𝐒𝐞𝐭 𝐘𝐨𝐮𝐫 Waifu's Mode\nCurrent: {mode.get('kind', 'default').upper()}",
        reply_markup=_dmode_menu(uid),
    )


async def dmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, uid = parts[1], int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    if q.from_user.id != uid:
        return await q.answer("This display menu is not yours.", show_alert=True)
    await q.answer()
    if action == "default":
        await user_collection.update_one({"id": uid}, {"$set": {"collection_mode": {"kind": "default", "value": ""}}})
        return await q.edit_message_text("✅ Collection mode reset to DEFAULT.")
    if action in {"rarity", "anime", "characters"}:
        if action == "rarity":
            choices = [(key, rarity_meta(key)["emoji"] + " " + rarity_meta(key)["name"]) for key in RARITY_KEYS]
        else:
            owned = await user_collection.find_one({"id": uid}) or {}
            field = "anime" if action == "anime" else "name"
            values = sorted({str(c.get(field, "")) for c in owned.get("characters", []) if c.get(field)})
            choices = [(str(i), value) for i, value in enumerate(values)]
            await user_collection.update_one({"id": uid}, {"$set": {"dmode_options": values}})
        start = page * 12
        chunk = choices[start:start + 12]
        rows = []
        for i in range(0, len(chunk), 4):
            row = []
            for key, label in chunk[i:i + 4]:
                encoded = key if action == "rarity" else key
                row.append(InlineKeyboardButton(label[:30], callback_data=f"dm:set:{uid}:{action}:{encoded}"))
            rows.append(row)
        nav = []
        if page:
            nav.append(InlineKeyboardButton("⬅ Back Page", callback_data=f"dm:{action}:{uid}:{page - 1}"))
        if start + 12 < len(choices):
            nav.append(InlineKeyboardButton("Next Page ➡", callback_data=f"dm:{action}:{uid}:{page + 1}"))
        nav.append(InlineKeyboardButton("BACK", callback_data=f"dm:menu:{uid}:0"))
        rows.append(nav)
        return await q.edit_message_text(
            f"Choose {action.upper()} filter | Page {page + 1}/{max(1, (len(choices) + 11) // 12)}",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    if action == "menu":
        return await q.edit_message_text("𝐒𝐞𝐭 𝐘𝐨𝐮𝐫 Waifu's Mode", reply_markup=_dmode_menu(uid))
    if action == "set":
        _, _, _, kind, value = parts
        if kind == "anime" or kind == "characters":
            opts = (await user_collection.find_one({"id": uid}) or {}).get("dmode_options", [])
            try:
                value = opts[int(value)]
            except (ValueError, IndexError):
                return await q.answer("That filter has expired. Open /dmode again.", show_alert=True)
            kind = "anime" if kind == "anime" else "character"
        await user_collection.update_one(
            {"id": uid},
            {"$set": {"collection_mode": {"kind": kind, "value": value}}},
        )
        label = rarity_meta(value)["name"] if kind == "rarity" else value
        return await q.edit_message_text(f"✅ Collection filter set to {label}. Use /collection to view it.")


def register(app):
    app.add_handler(CommandHandler("collection", collection_cmd))
    app.add_handler(CommandHandler("fav", fav_command))
    app.add_handler(CommandHandler("dmode", dmode_command))
    app.add_handler(CallbackQueryHandler(collection_callback, pattern=r"^col:"))
    app.add_handler(CallbackQueryHandler(fav_callback, pattern=r"^fav:"))
    app.add_handler(CallbackQueryHandler(dmode_callback, pattern=r"^dm:"))
