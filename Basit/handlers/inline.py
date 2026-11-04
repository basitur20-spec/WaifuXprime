"""Telegram inline search for owned collections and favorite character variants."""
from html import escape

from telegram import Update, InlineQueryResultPhoto, InlineQueryResultCachedPhoto
from telegram.ext import InlineQueryHandler, ContextTypes

from ..database import user_collection, characters


def _image_result(item, caption, result_id):
    photo = item.get("img_url") or item.get("image_url") or item.get("file_id")
    if not photo:
        return None
    if isinstance(photo, str) and photo.startswith("AgAC"):
        return InlineQueryResultCachedPhoto(
            id=result_id, photo_file_id=photo, caption=caption, parse_mode="HTML"
        )
    return InlineQueryResultPhoto(
        id=result_id,
        photo_url=photo,
        thumbnail_url=photo,
        caption=caption,
        parse_mode="HTML",
    )


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    raw = (query.query or "").strip()
    offset = int(query.offset or 0)
    user_id = query.from_user.id
    results = []

    if raw.casefold().startswith("collection."):
        tail = raw[len("collection."):].strip()
        parts = tail.split(maxsplit=1)
        try:
            target_id = int(parts[0])
        except (ValueError, IndexError):
            return await query.answer([], cache_time=2, is_personal=True)
        # Privacy: inline collection is only available to the owner of that collection.
        if target_id != user_id:
            return await query.answer([], cache_time=2, is_personal=True)
        doc = await user_collection.find_one({"id": user_id}) or {}
        chars = list(doc.get("characters", []))
        if len(parts) > 1:
            term = parts[1].casefold()
            chars = [
                c for c in chars
                if term in str(c.get("name", "")).casefold()
                or term in str(c.get("anime", "")).casefold()
            ]
        # One result per owned unique character ID; collection count remains in caption.
        counts = {}
        unique = {}
        for char in chars:
            wid = str(char.get("id", ""))
            counts[wid] = counts.get(wid, 0) + 1
            unique.setdefault(wid, char)
        selected = list(unique.values())[offset:offset + 50]
        for index, char in enumerate(selected, start=offset):
            caption = (
                f"<b>{escape(str(char.get('name', 'Unknown')))}</b>\n"
                f"🎬 {escape(str(char.get('anime', 'Unknown')))}\n"
                f"✨ {escape(str(char.get('rarity', 'Unknown')))}\n"
                f"🆔 {escape(str(char.get('id', '—')))} ×{counts.get(str(char.get('id')), 1)}"
            )
            result = _image_result(char, caption, f"col_{user_id}_{index}_{char.get('id')}")
            if result:
                results.append(result)
        next_offset = str(offset + len(selected)) if offset + 50 < len(unique) else ""
        return await query.answer(results, next_offset=next_offset, cache_time=2, is_personal=True)

    if raw.casefold().startswith("fav."):
        wid = raw[4:].strip().split(maxsplit=1)[0] if raw[4:].strip() else ""
        if not wid:
            return await query.answer([], cache_time=2, is_personal=True)
        doc = await user_collection.find_one({"id": user_id}) or {}
        fav_ids = {str(x) for x in doc.get("favorites", [])}
        if wid not in fav_ids:
            return await query.answer([], cache_time=2, is_personal=True)
        favorite = next((c for c in doc.get("characters", []) if str(c.get("id")) == wid), None)
        if not favorite:
            return await query.answer([], cache_time=2, is_personal=True)
        canonical_name = str(favorite.get("name", ""))
        variants = await characters.find({"name": canonical_name}).to_list(length=500)
        selected = variants[offset:offset + 50]
        for index, char in enumerate(selected, start=offset):
            caption = (
                f"<b>{escape(str(char.get('name', 'Unknown')))}</b> ❤️\n"
                f"🎬 {escape(str(char.get('anime', 'Unknown')))}\n"
                f"✨ {escape(str(char.get('rarity', 'Unknown')))}\n"
                f"🆔 {escape(str(char.get('id', '—')))}"
            )
            result = _image_result(char, caption, f"fav_{user_id}_{index}_{char.get('id')}")
            if result:
                results.append(result)
        next_offset = str(offset + len(selected)) if offset + 50 < len(variants) else ""
        return await query.answer(results, next_offset=next_offset, cache_time=2, is_personal=True)

    # Normal database search.
    if raw:
        regex = {"$regex": __import__("re").escape(raw), "$options": "i"}
        chars = await characters.find({"$or": [{"name": regex}, {"anime": regex}]}).to_list(length=500)
    else:
        chars = await characters.find({}).limit(50).to_list(length=50)
    selected = chars[offset:offset + 50]
    for index, char in enumerate(selected, start=offset):
        caption = (
            f"<b>{escape(str(char.get('name', 'Unknown')))}</b>\n"
            f"🎬 {escape(str(char.get('anime', 'Unknown')))}\n"
            f"✨ {escape(str(char.get('rarity', 'Unknown')))}\n"
            f"🆔 {escape(str(char.get('id', '—')))}"
        )
        result = _image_result(char, caption, f"char_{index}_{char.get('id')}")
        if result:
            results.append(result)
    next_offset = str(offset + len(selected)) if len(chars) > offset + 50 else ""
    await query.answer(results, next_offset=next_offset, cache_time=5, is_personal=False)


def register(app):
    app.add_handler(InlineQueryHandler(inline_query))
