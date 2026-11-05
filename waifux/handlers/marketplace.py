from __future__ import annotations
import time
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from ..database import db, get_balance, adjust_balance, add_to_collection, remove_from_collection
from ..utils import fmt

async def market_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() in ('show','refresh'):
        uid=update.effective_user.id
        with db() as con:
            rows=con.execute('SELECT m.*,c.name,c.anime,c.rarity FROM marketplace m LEFT JOIN characters c ON c.id=m.character_id WHERE m.bought=0 ORDER BY m.price LIMIT 10').fetchall()
        text='🛒 <b>Marketplace</b>\n'+('\n'.join(f'{r["character_id"]} · {r["name"] or "Unknown"} — {fmt(r["price"])} (/buy {r["character_id"]})' for r in rows) or 'No listings.')
        return await update.message.reply_text(text,parse_mode='HTML')
    await update.message.reply_text('Commands: /market, /buy <character id>, /auction <character id> <starting bid>.')

async def buy_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text('Use /buy <character_id>.')
    cid=context.args[0]; uid=update.effective_user.id
    with db() as con:
        row=con.execute('SELECT * FROM marketplace WHERE character_id=? AND bought=0 ORDER BY price LIMIT 1',(cid,)).fetchone()
        if not row: return await update.message.reply_text('Listing not found.')
        cash,_=get_balance(uid,con)
        if cash<row['price']: return await update.message.reply_text('Not enough cash.')
        adjust_balance(uid,cash=-row['price'],con=con); add_to_collection(uid,cid,update.effective_user.username or '',con)
        con.execute('UPDATE marketplace SET bought=1 WHERE user_id=? AND slot=? AND day=?',(row['user_id'],row['slot'],row['day']))
        adjust_balance(row['user_id'],cash=row['price'],con=con)
    await update.message.reply_text('✅ Purchase complete.')

async def auction_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2: return await update.message.reply_text('Use /auction <waifu_id> <starting bid> (reply to your character entry).')
    try: bid=int(context.args[1])
    except ValueError: return await update.message.reply_text('Bid must be a number.')
    if bid<1: return await update.message.reply_text('Bid must be positive.')
    cid=context.args[0]; uid=update.effective_user.id
    with db() as con:
        own=con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=?',(uid,cid)).fetchone()
        if not own: return await update.message.reply_text('You do not own that waifu.')
        con.execute('INSERT INTO auctions(character_id,starting_bid,high_bid,ends_at,created_by,created_at) VALUES(?,?,?,?,?,?)',(cid,bid,bid,int(time.time())+86400,uid,int(time.time())))
    await update.message.reply_text('Auction opened for 24 hours. Use /bid <auction id> <amount> (bidding UI coming via text command).')

async def bid_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2: return await update.message.reply_text('Use /bid <auction_id> <amount>.')
    try: aid=int(context.args[0]); amount=int(context.args[1])
    except ValueError: return await update.message.reply_text('Invalid auction or amount.')
    uid=update.effective_user.id
    with db() as con:
        a=con.execute('SELECT * FROM auctions WHERE id=? AND status="open" AND ends_at>?',(aid,int(time.time()))).fetchone()
        if not a or amount<=a['high_bid'] or uid==a['created_by']: return await update.message.reply_text('Auction unavailable or bid is too low.')
        cash,_=get_balance(uid,con)
        if cash<amount: return await update.message.reply_text('Not enough cash.')
        adjust_balance(uid,cash=-amount,con=con)
        if a['high_bidder']: adjust_balance(a['high_bidder'],cash=a['high_bid'],con=con)
        con.execute('UPDATE auctions SET high_bid=?,high_bidder=? WHERE id=?',(amount,uid,aid))
    await update.message.reply_text('✅ Bid recorded.')

def register_marketplace(app):
    app.add_handler(CommandHandler('market',market_cmd)); app.add_handler(CommandHandler('buy',buy_cmd))
    app.add_handler(CommandHandler('auction',auction_cmd)); app.add_handler(CommandHandler('bid',bid_cmd))
