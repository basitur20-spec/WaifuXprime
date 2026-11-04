"""Cash dice games, persistent Mines, and global leaderboard."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pymongo import ReturnDocument

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from ..config import RARITIES
from ..database import (
    user_collection, group_user_totals, top_global_groups, user_totals,
    mines_games, get_user, debit_balance, credit_balance,
)


def _fmt(n):
    return f"{int(n):,}"


def _parse(raw):
    try:
        return int(raw.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


async def _dice_bet(update, context, kind):
    msg = update.effective_message
    uid = update.effective_user.id
    args = context.args
    if not args:
        example = f"/{kind} <amount> " + ("<odd|even>" if kind == "roll" else "")
        return await msg.reply_text(f"Usage: {example}\nMinimum bet: 5% of your Cash.")
    if (kind == "roll" and len(args) != 2) or (kind != "roll" and len(args) != 1):
        return await msg.reply_text("Invalid bet format.")
    bet = _parse(args[0])
    if bet <= 0:
        return await msg.reply_text("❌ Enter a valid positive amount.")
    choice = args[1].lower() if kind == "roll" else ""
    if kind == "roll" and choice not in {"odd", "even"}:
        return await msg.reply_text("Choose odd or even: /roll 25000 odd")
    user = await get_user(uid, update.effective_user)
    next_at = user.get(f"{kind}_next_at")
    now = datetime.utcnow()
    if next_at and next_at > now:
        return await msg.reply_text(f"⏳ Cooldown active. Wait {max(1, int((next_at-now).total_seconds()))} seconds.")
    cash = int(user.get("cash", 0))
    minimum = max(1, int(cash * 0.05))
    if cash <= 0:
        return await msg.reply_text("❌ You have no Cash to bet.")
    if bet < minimum:
        return await msg.reply_text(f"⚠️ Minimum bet is 5% of your balance: {_fmt(minimum)} Cash💸")
    if bet > cash:
        return await msg.reply_text(f"❌ Not enough Cash. Available: {_fmt(cash)}")
    if not await debit_balance(uid, "cash", bet):
        return await msg.reply_text("❌ Balance changed. Please try again.")
    try:
        emoji = {"roll": "🎲", "darts": "🎯", "goal": "⚽"}[kind]
        dice_message = await context.bot.send_dice(chat_id=msg.chat_id, emoji=emoji)
    except Exception:
        await credit_balance(uid, "cash", bet)
        return await msg.reply_text("❌ Dice service unavailable. Your bet was refunded.")
    await user_collection.update_one(
        {"id": uid}, {"$set": {f"{kind}_next_at": now + timedelta(seconds=60)}}
    )
    value = dice_message.dice.value
    if kind == "roll":
        won = (value % 2 == 1 and choice == "odd") or (value % 2 == 0 and choice == "even")
        multiplier = 2 if won else 0
        outcome = f"🎲 Dice: {value} ({'ODD' if value % 2 else 'EVEN'})\nYour choice: {choice.upper()}"
    elif kind == "darts":
        won = value == 6
        multiplier = 4 if won else 0
        outcome = f"🎯 Darts result: {value}"
    else:
        won = value >= 4
        multiplier = 2.5 if won else 0
        outcome = f"⚽ Football result: {value}"
    if won:
        payout = int(bet * multiplier)
        await credit_balance(uid, "cash", payout)
        updated = await user_collection.find_one({"id": uid}) or {}
        text = f"🔥 YOU WON!\n{outcome}\n💸 Bet: {_fmt(bet)}\n🏆 Total return: {_fmt(payout)} Cash💸\n💼 Balance: {_fmt(updated.get('cash', 0))}"
    else:
        updated = await user_collection.find_one({"id": uid}) or {}
        text = f"💔 YOU LOST\n{outcome}\n💸 Lost: {_fmt(bet)} Cash💸\n💼 Remaining balance: {_fmt(updated.get('cash', 0))}"
    await msg.reply_text(text)


async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dice_bet(update, context, "roll")


async def darts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dice_bet(update, context, "darts")


async def goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _dice_bet(update, context, "goal")


def _mines_keyboard(game):
    board = []
    mines = set(game.get("mines", []))
    opened = set(game.get("opened", []))
    ended = game.get("status") != "active"
    for row in range(5):
        buttons = []
        for col in range(5):
            idx = row * 5 + col
            if idx in opened:
                label = "💎"
            elif ended and idx in mines:
                label = "💣"
            else:
                label = "⬛"
            callback = f"mine:tile:{game['_id']}:{idx}" if not ended else "mine:locked"
            buttons.append(InlineKeyboardButton(label, callback_data=callback))
        board.append(buttons)
    if game.get("safe", 0) >= 2 and not ended:
        board.append([InlineKeyboardButton("💎 CASH OUT", callback_data=f"mine:cashout:{game['_id']}")])
    return InlineKeyboardMarkup(board)


def _multiplier(safe, mine_count):
    return round(1 + (mine_count * 0.15) + (safe * 0.12), 2)


async def mines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    if not context.args or len(context.args) > 2:
        return await msg.reply_text("Usage: /mines <diamonds> [mines]\nMin Bet: 20💎 | Max Bet: 5,000💎 | Mines: 3–24")
    bet = _parse(context.args[0])
    try:
        mine_count = int(context.args[1]) if len(context.args) == 2 else 5
    except ValueError:
        mine_count = 0
    if not 20 <= bet <= 5000:
        return await msg.reply_text("❌ Bet must be between 20💎 and 5,000💎.")
    if not 3 <= mine_count < 25:
        return await msg.reply_text("❌ Mines count must be between 3 and 24.")
    active = await mines_games.find_one({"user_id": uid, "status": "active"})
    if active:
        return await msg.reply_text("⛔ You already have an active Mines game. Use /endmines.")
    if not await debit_balance(uid, "diamonds", bet):
        return await msg.reply_text("❌ Not enough Diamonds 💎.")
    game = {
        "_id": __import__("uuid").uuid4().hex,
        "user_id": uid, "chat_id": msg.chat_id, "bet": bet,
        "mines": random.sample(range(25), mine_count),
        "opened": [], "safe": 0, "multiplier": 1.0,
        "mine_count": mine_count, "status": "active",
        "created_at": datetime.utcnow(),
    }
    try:
        await mines_games.insert_one(game)
    except Exception:
        await credit_balance(uid, "diamonds", bet)
        return await msg.reply_text("❌ Could not start Mines. Your Diamonds were returned.")
    await msg.reply_text(
        f"💣 MINES GAME STARTED\n\n💎 Bet: {bet}\n💥 Mines: {mine_count}\n"
        "📊 Opened: 0 / 25\n📈 Multiplier: 1.00x\nGood luck 😈",
        reply_markup=_mines_keyboard(game),
    )


async def mines_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]
    if action == "locked":
        return await q.answer("This game has ended.")
    game_id = parts[2]
    game = await mines_games.find_one({"_id": game_id, "user_id": q.from_user.id, "status": "active"})
    if not game:
        return await q.answer("This is not your active game.", show_alert=True)
    if action == "cashout":
        if int(game.get("safe", 0)) < 2:
            return await q.answer("Open at least 2 safe tiles before cashing out.", show_alert=True)
        closed = await mines_games.find_one_and_update(
            {"_id": game_id, "user_id": q.from_user.id, "status": "active", "safe": {"$gte": 2}},
            {"$set": {"status": "cashed", "closed_at": datetime.utcnow()}},
            return_document=ReturnDocument.BEFORE,
        )
        if not closed:
            return await q.answer("Game already ended.", show_alert=True)
        win = int(closed["bet"] * closed["multiplier"])
        await credit_balance(q.from_user.id, "diamonds", win)
        closed["status"] = "cashed"
        await q.answer()
        return await q.edit_message_text(
            f"🎉 MINES CASHED OUT!\n💎 Bet: {closed['bet']}\n"
            f"📈 Multiplier: {closed['multiplier']}x\n🏆 Returned: {win}💎",
            reply_markup=_mines_keyboard(closed),
        )
    try:
        index = int(parts[3])
    except (IndexError, ValueError):
        return await q.answer("Invalid tile.", show_alert=True)
    if not 0 <= index < 25:
        return await q.answer("Invalid tile.", show_alert=True)
    if index in game.get("opened", []):
        return await q.answer("This tile is already open.")
    if index in game.get("mines", []):
        lost = await mines_games.find_one_and_update(
            {"_id": game_id, "user_id": q.from_user.id, "status": "active", "opened": {"$ne": index}},
            {"$set": {"status": "lost", "closed_at": datetime.utcnow()}},
            return_document=ReturnDocument.BEFORE,
        )
        if not lost:
            return await q.answer("Game already ended.", show_alert=True)
        lost["status"] = "lost"
        await q.answer("💥 Mine hit!", show_alert=True)
        return await q.edit_message_text(
            f"💥 BOOM!\n💎 Bet lost: {lost['bet']}\nBetter luck next time.",
            reply_markup=_mines_keyboard(lost),
        )
    updated = await mines_games.find_one_and_update(
        {"_id": game_id, "user_id": q.from_user.id, "status": "active", "opened": {"$ne": index}},
        {"$addToSet": {"opened": index}, "$inc": {"safe": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        return await q.answer("This tile is already open or the game ended.")
    multiplier = _multiplier(updated["safe"], updated["mine_count"])
    await mines_games.update_one({"_id": game_id, "status": "active"}, {"$set": {"multiplier": multiplier}})
    updated["multiplier"] = multiplier
    await q.answer("💎 Safe tile!")
    potential = int(updated["bet"] * multiplier)
    await q.edit_message_text(
        f"🎯 SAFE TILE!\n📊 Opened: {updated['safe']} / 25\n"
        f"📈 Multiplier: {multiplier}x\n💰 Potential return: {potential}💎",
        reply_markup=_mines_keyboard(updated),
    )


async def endmines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    game = await mines_games.find_one_and_update(
        {"user_id": uid, "status": "active"},
        {"$set": {"status": "cancelled", "closed_at": datetime.utcnow()}},
        return_document=ReturnDocument.BEFORE,
    )
    if not game:
        return await update.effective_message.reply_text("❌ No active Mines game.")
    await credit_balance(uid, "diamonds", int(game["bet"]))
    await update.effective_message.reply_text(f"🛑 MINES CANCELLED\n💎 Returned: {game['bet']} Diamonds.")


async def dtops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Diamonds", callback_data="dt:diamonds"),
         InlineKeyboardButton("🦋 Waifus", callback_data="dt:waifus")],
        [InlineKeyboardButton("💰 Cash", callback_data="dt:cash"),
         InlineKeyboardButton("🥷 Hunters", callback_data="dt:hunters")],
        [InlineKeyboardButton("💞 Rarity Stats (Staff)", callback_data="dt:rarity")],
    ])
    await update.effective_message.reply_text("🌍 GLOBAL LEADERBOARDS\nChoose a category.", reply_markup=keyboard)


async def dtops_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action = q.data.split(":")[1]
    await q.answer()
    if action == "menu":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Diamonds", callback_data="dt:diamonds"), InlineKeyboardButton("🦋 Waifus", callback_data="dt:waifus")],
            [InlineKeyboardButton("💰 Cash", callback_data="dt:cash"), InlineKeyboardButton("🥷 Hunters", callback_data="dt:hunters")],
            [InlineKeyboardButton("💞 Rarity Stats (Staff)", callback_data="dt:rarity")],
        ])
        return await q.edit_message_text("🌍 GLOBAL LEADERBOARDS\\nChoose a category.", reply_markup=keyboard)
    if action in {"cash", "diamonds", "waifus"}:
        field = {"cash": "$cash", "diamonds": "$diamonds", "waifus": {"$size": {"$ifNull": ["$characters", []]}}}[action]
        pipeline = [
            {"$project": {"id": 1, "username": 1, "first_name": 1, "value": field}},
            {"$sort": {"value": -1}}, {"$limit": 10},
        ]
        rows = await user_collection.aggregate(pipeline).to_list(length=10)
        title = {"cash": "TOP CASH HOLDERS", "diamonds": "TOP DIAMOND HOLDERS", "waifus": "TOP WAIFU COLLECTORS"}[action]
        text = f"🌍 {title}\n\n"
        for index, row in enumerate(rows, 1):
            name = "@" + row["username"] if row.get("username") else row.get("first_name") or f"User {row.get('id')}"
            text += f"{index}. {name} — {_fmt(row.get('value', 0))}\n"
    elif action == "hunters":
        pipeline = [
            {"$group": {"_id": "$user_id", "count": {"$sum": "$count"}, "name": {"$first": "$first_name"}, "username": {"$first": "$username"}}},
            {"$sort": {"count": -1}}, {"$limit": 10},
        ]
        rows = await group_user_totals.aggregate(pipeline).to_list(length=10)
        text = "🥷 TOP HUNTERS\n\n"
        for index, row in enumerate(rows, 1):
            name = "@" + row["username"] if row.get("username") else row.get("name") or f"User {row['_id']}"
            text += f"{index}. {name} — {row.get('count', 0)} hunts\n"
    elif action == "rarity":
        from .admin import is_staff
        if not await is_staff(q.from_user.id):
            return await q.answer("Staff only.", show_alert=True)
        pipeline = [
            {"$unwind": "$characters"},
            {"$group": {"_id": "$characters.rarity", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        rows = await user_collection.aggregate(pipeline).to_list(length=50)
        text = "💞 WAIFU RARITY OVERVIEW\n\n" + "".join(f"{row['_id']}: {row['count']}\n" for row in rows)
    else:
        return
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅ Back", callback_data="dt:menu")]
    ]))


def register(app):
    app.add_handler(CommandHandler("roll", roll))
    app.add_handler(CommandHandler("darts", darts))
    app.add_handler(CommandHandler("goal", goal))
    app.add_handler(CommandHandler("mines", mines))
    app.add_handler(CommandHandler("endmines", endmines))
    app.add_handler(CommandHandler("dtops", dtops))
    app.add_handler(CallbackQueryHandler(mines_callback, pattern=r"^mine:"))
    app.add_handler(CallbackQueryHandler(dtops_callback, pattern=r"^dt:"))
