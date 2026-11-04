"""Character lookup, global ownership search, and name search pages."""
import re
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..config import RARITIES, normalize_rarity, rarity_meta
from ..database import characters, user_collection
from ..services.characters import by_id, image_of, details

_SEARCHES = {}
RARITY_ORDER = {key: index for index, key in enumerate(RARITIES.keys())}


async def _pick_count(wid):
    pipeline = [
        {"$unwind": "$characters"},
        {"$match": {"characters.id": str(wid)}},
        {"$count": "total"},
    ]
    rows = await user_collection.aggregate(pipeline).to_list(length=1)
    return int(rows[0]["total"]) if rows else 0


async def find_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /f <waifu_id>")
    char = await by_id(context.args[0])
    if not char:
        return await update.message.reply_text("THIS ID IS NOT FOUND IN BOT DATA BASE.")
    count = await _pick_count(str(char.get("id")))
    text = details(char) + f"\n✧ GLOBALLY PICKED: {count} times..."
    await update.message.reply_photo(image_of(char), caption=text)


async def find_owners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /find <waifu_id>")
    wid = context.args[0]
    char = await by_id(wid)
    if not char:
        return await update.message.reply_text("THE WAIFU ID IS NOT FOUND IN BOT DATA BASE.")
    pipeline = [
        {"$unwind": "$characters"},
        {"$match": {"characters.id": str(wid)}},
        {"$group": {
            "_id": "$id",
            "username": {"$first": "$username"},
            "first_name": {"$first": "$first_name"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1, "first_name": 1}},
    ]
    owners = await user_collection.aggregate(pipeline).to_list(length=10000)
    if not owners:
        return await update.message.reply_text("❌ NO CHARACTER FOUND WITH THIS 🆔.")
    token = uuid.uuid4().hex[:8]
    _SEARCHES[token] = {"owner": update.effective_user.id, "character": char, "owners": owners}
    await _render_find_page(update, context, token, 0, reply=True)


async def _render_find_page(update, context, token, page, reply=False):
    data = _SEARCHES.get(token)
    if not data:
        if reply:
            await update.effective_message.reply_text("This result has expired. Run /find again.")
        return
    char = data["character"]
    owners = data["owners"]
    total = len(owners)
    pages = max(1, (total + 9) // 10)
    page = max(0, min(page, pages - 1))
    part = owners[page * 10:(page + 1) * 10]
    text = details(char) + f"\n\n👥 USERS WHO HAVE THIS WAIFU ({total} total):\n"
    for i, item in enumerate(part, start=page * 10 + 1):
        username = f'@{item["username"]}' if item.get("username") else (
            item.get("first_name") or f'User {item["_id"]}'
        )
        text += f'{i}. {username} x{item["count"]}\n'
    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Back Page", callback_data=f"find:{token}:{page-1}"))
    nav.append(InlineKeyboardButton(f"📑 {page+1}/{pages}", callback_data="find:noop:0"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"find:{token}:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🗑 Discard", callback_data=f"find:discard:{token}")])
    markup = InlineKeyboardMarkup(rows)
    if reply:
        await update.effective_message.reply_photo(image_of(char), caption=text, reply_markup=markup)
    else:
        q = update.callback_query
        await q.edit_message_caption(caption=text, reply_markup=markup)


async def find_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    if parts[1] == "noop":
        return await q.answer()
    if parts[1] == "discard":
        token = parts[2]
        data = _SEARCHES.get(token)
        if not data or data["owner"] != q.from_user.id:
            return await q.answer("This search is not yours.", show_alert=True)
        _SEARCHES.pop(token, None)
        await q.answer()
        return await q.message.delete()
    token, page = parts[1], int(parts[2])
    data = _SEARCHES.get(token)
    if not data or data["owner"] != q.from_user.id:
        return await q.answer("This search session is not yours.", show_alert=True)
    await q.answer()
    await _render_find_page(update, context, token, page)


async def dfind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("⚠️ Missing character name. Example: /dfind goku")
    query = " ".join(context.args).strip()
    regex = re.compile(re.escape(query), re.IGNORECASE)
    results = await characters.find({"$or": [{"name": regex}, {"anime": regex}]}).to_list(length=5000)
    results.sort(key=lambda item: (
        RARITY_ORDER.get(normalize_rarity(str(item.get("rarity", ""))), 999),
        str(item.get("name", "")).casefold(),
    ))
    if not results:
        return await update.message.reply_text(f'😔 No waifus found for "{query}".')
    token = uuid.uuid4().hex[:8]
    _SEARCHES[token] = {"owner": update.effective_user.id, "query": query, "results": results}
    await _render_dfind_page(update, context, token, 0, reply=True)


async def _render_dfind_page(update, context, token, page, reply=False):
    data = _SEARCHES.get(token)
    if not data:
        return
    results = data["results"]
    total = len(results)
    pages = max(1, (total + 14) // 15)
    page = max(0, min(page, pages - 1))
    subset = results[page * 15:(page + 1) * 15]
    text = f'🔍 SEARCH RESULTS\n◈ QUERY: "{data["query"]}"\n◈ TOTAL FOUND: {total} WAIFUS\n\n'
    last_rarity = None
    numbers = {}
    for item in subset:
        rarity_key = normalize_rarity(str(item.get("rarity", "")))
        if rarity_key != last_rarity:
            meta = rarity_meta(rarity_key)
            text += f'━━━━━━━━━━━━━━━━\n{meta["emoji"]} {meta["name"]}\n━━━━━━━━━━━━━━━━\n'
            last_rarity = rarity_key
            numbers[rarity_key] = 0
        numbers[rarity_key] += 1
        text += (
            f'{numbers[rarity_key]}. {item.get("name", "Unknown")} ({item.get("anime", "Unknown")})\n'
            f'   ┗ ID: {item.get("id", "—")}\n'
        )
    text += "\n📌 Copy a Waifu ID and use it with /f."
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅ Back Page", callback_data=f"dfind:prev:{token}:{page-1}") if page > 0 else
        InlineKeyboardButton("📑 1st Page", callback_data="dfind:noop"),
        InlineKeyboardButton("Next Page ➡", callback_data=f"dfind:next:{token}:{page+1}") if page + 1 < pages else
        InlineKeyboardButton("Discard 🗑️", callback_data=f"dfind:discard:{token}"),
    ], [InlineKeyboardButton("Discard 🗑️", callback_data=f"dfind:discard:{token}")]] if pages > 1 else None)
    if reply:
        await update.effective_message.reply_text(text, reply_markup=markup)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=markup)


async def dfind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    if parts[1] == "noop":
        return await q.answer()
    if parts[1] == "discard":
        token = parts[2]
        data = _SEARCHES.get(token)
        if not data or data["owner"] != q.from_user.id:
            return await q.answer("This search session is not yours.", show_alert=True)
        _SEARCHES.pop(token, None)
        await q.answer()
        return await q.message.delete()
    action, token, page = parts[1], parts[2], int(parts[3])
    data = _SEARCHES.get(token)
    if not data or data["owner"] != q.from_user.id:
        return await q.answer("This search session is not yours.", show_alert=True)
    await q.answer()
    await _render_dfind_page(update, context, token, page)


def register(app):
    app.add_handler(CommandHandler("f", find_by_id))
    app.add_handler(CommandHandler("find", find_owners))
    app.add_handler(CommandHandler("dfind", dfind))
    app.add_handler(CallbackQueryHandler(find_callback, pattern=r"^find:"))
    app.add_handler(CallbackQueryHandler(dfind_callback, pattern=r"^dfind:"))
