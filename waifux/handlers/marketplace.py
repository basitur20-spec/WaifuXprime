from __future__ import annotations
import time
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from ..database import db, get_balance, adjust_balance, add_to_collection, remove_from_collection, audit
from ..utils import fmt, day_key

def _ensure_market_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS market_listings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER NOT NULL,
        character_id TEXT NOT NULL,
        price INTEGER NOT NULL CHECK(price > 0),
        status TEXT NOT NULL DEFAULT 'open',
        created_at INTEGER NOT NULL,
        sold_to INTEGER
    )""")

async def market_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    args=context.args
    if args and args[0].lower() in ('add','sell','list'):
        if len(args)<3: return await update.message.reply_text('Use /market add <waifu_id> <price>.')
        cid=args[1]
        try: price=int(args[2].replace(',',''))
        except ValueError: return await update.message.reply_text('Price must be a number.')
        if price<1: return await update.message.reply_text('Price must be positive.')
        uid=update.effective_user.id
        with db() as con:
            _ensure_market_schema(con)
            owned=con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=? LIMIT 1',(uid,cid)).fetchone()
            if not owned: return await update.message.reply_text('You do not own that waifu.')
            if con.execute("SELECT 1 FROM market_listings WHERE seller_id=? AND character_id=? AND status='open'",(uid,cid)).fetchone():
                return await update.message.reply_text('That waifu is already listed.')
            if not remove_from_collection(uid,cid,con): return await update.message.reply_text('Could not reserve that waifu.')
            cur=con.execute('INSERT INTO market_listings(seller_id,character_id,price,status,created_at) VALUES(?,?,?,"open",?)',(uid,cid,price,int(time.time())))
            listing_id=cur.lastrowid
            audit(uid,'market_list',f'{listing_id} {cid} {price}',con)
        return await update.message.reply_text(f'✅ Listing #{listing_id} created for {fmt(price)} cash. The waifu is reserved until sale or cancellation.')
    if args and args[0].lower() in ('cancel','remove'):
        if len(args)<2: return await update.message.reply_text('Use /market cancel <listing_id>.')
        try: listing_id=int(args[1])
        except ValueError: return await update.message.reply_text('Listing ID must be a number.')
        uid=update.effective_user.id
        with db() as con:
            _ensure_market_schema(con)
            row=con.execute("SELECT * FROM market_listings WHERE id=? AND seller_id=? AND status='open'",(listing_id,uid)).fetchone()
            if not row: return await update.message.reply_text('Open listing not found.')
            add_to_collection(uid,row['character_id'],update.effective_user.username or '',con)
            con.execute("UPDATE market_listings SET status='cancelled' WHERE id=?",(listing_id,))
            audit(uid,'market_cancel',str(listing_id),con)
        return await update.message.reply_text('Listing cancelled and waifu returned to your collection.')
    with db() as con:
        _ensure_market_schema(con)
        rows=con.execute("""SELECT l.id,l.seller_id,l.character_id,l.price,c.name,c.rarity,c.anime
          FROM market_listings l LEFT JOIN characters c ON c.id=l.character_id
          WHERE l.status='open' ORDER BY l.created_at DESC LIMIT 20""").fetchall()
    if not rows: return await update.message.reply_text('🛒 Marketplace is empty. List with /market add <waifu_id> <price>.')
    lines=['🛒 MARKETPLACE']
    lines.extend(f'#{r["id"]} · {r["name"] or "Unknown"} ({r["rarity"] or "?"}) · {fmt(r["price"])} cash · /buy {r["id"]}' for r in rows)
    await update.message.reply_text('\n'.join(lines))

async def buy_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text('Use /buy <listing_id>.')
    try: listing_id=int(context.args[0])
    except ValueError: return await update.message.reply_text('Listing ID must be a number.')
    uid=update.effective_user.id
    with db() as con:
        _ensure_market_schema(con)
        row=con.execute("SELECT * FROM market_listings WHERE id=? AND status='open'",(listing_id,)).fetchone()
        if not row: return await update.message.reply_text('Listing not found.')
        if row['seller_id']==uid: return await update.message.reply_text('You cannot buy your own listing.')
        cash,_=get_balance(uid,con)
        if cash<row['price']: return await update.message.reply_text('Not enough cash.')
        adjust_balance(uid,cash=-row['price'],con=con)
        add_to_collection(uid,row['character_id'],update.effective_user.username or '',con)
        adjust_balance(row['seller_id'],cash=row['price'],con=con)
        con.execute("UPDATE market_listings SET status='sold',sold_to=? WHERE id=? AND status='open'",(uid,listing_id))
        audit(uid,'market_buy',f'{listing_id} {row["character_id"]} {row["price"]}',con)
    await update.message.reply_text(f'✅ Purchase complete: {row["character_id"]} added to your collection.')

async def auction_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2: return await update.message.reply_text('Use /auction <waifu_id> <starting_bid>.')
    cid=context.args[0]
    try: starting=int(context.args[1].replace(',',''))
    except ValueError: return await update.message.reply_text('Starting bid must be a number.')
    if starting<1: return await update.message.reply_text('Starting bid must be positive.')
    uid=update.effective_user.id; now=int(time.time())
    with db() as con:
        owned=con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=? LIMIT 1',(uid,cid)).fetchone()
        if not owned: return await update.message.reply_text('You do not own that waifu.')
        if con.execute('SELECT 1 FROM auctions WHERE character_id=? AND status="open"',(cid,)).fetchone(): return await update.message.reply_text('This character already has an open auction.')
        if not remove_from_collection(uid,cid,con): return await update.message.reply_text('Could not reserve that waifu.')
        cur=con.execute('INSERT INTO auctions(character_id,starting_bid,high_bid,high_bidder,ends_at,status,created_by,created_at) VALUES(?,?,?,NULL,?,"open",?,?)',(cid,starting,starting,now+86400,uid,now))
        aid=cur.lastrowid; audit(uid,'auction_open',f'{aid} {cid} {starting}',con)
    if context.job_queue: context.job_queue.run_once(settle_auction,86400,data={'auction_id':aid},name=f'auction:{aid}')
    await update.message.reply_text(f'🔨 Auction #{aid} opened for 24 hours. Use /bid {aid} <amount>.')

async def settle_auction(context:ContextTypes.DEFAULT_TYPE):
    aid=int(context.job.data['auction_id']); now=int(time.time())
    with db() as con:
        a=con.execute('SELECT * FROM auctions WHERE id=? AND status="open"',(aid,)).fetchone()
        if not a: return
        if a['ends_at']>now:
            if context.job_queue: context.job_queue.run_once(settle_auction,a['ends_at']-now,data={'auction_id':aid},name=f'auction:{aid}')
            return
        if a['high_bidder'] is None:
            add_to_collection(a['created_by'],a['character_id'],'',con)
            con.execute('UPDATE auctions SET status="unsold" WHERE id=?',(aid,))
            notice=f'Auction #{aid} ended without bids; character returned to seller.'
        else:
            add_to_collection(a['high_bidder'],a['character_id'],'',con)
            adjust_balance(a['created_by'],cash=a['high_bid'],con=con)
            con.execute('UPDATE auctions SET status="sold" WHERE id=?',(aid,))
            notice=f'Auction #{aid} sold for {fmt(a["high_bid"])} cash.'
        audit(a['created_by'],'auction_settle',notice,con)
    try: await context.bot.send_message(a['created_by'],notice)
    except Exception: pass

async def bid_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2: return await update.message.reply_text('Use /bid <auction_id> <amount>.')
    try: aid=int(context.args[0]); amount=int(context.args[1].replace(',',''))
    except ValueError: return await update.message.reply_text('Auction ID and bid must be numbers.')
    uid=update.effective_user.id; now=int(time.time())
    with db() as con:
        a=con.execute('SELECT * FROM auctions WHERE id=? AND status="open" AND ends_at>?',(aid,now)).fetchone()
        if not a: return await update.message.reply_text('Auction is closed or not found.')
        if uid==a['created_by']: return await update.message.reply_text('You cannot bid on your own auction.')
        if amount<=a['high_bid']: return await update.message.reply_text(f'Bid must exceed {fmt(a["high_bid"])}.')
        cash,_=get_balance(uid,con)
        if cash<amount: return await update.message.reply_text('Not enough cash.')
        adjust_balance(uid,cash=-amount,con=con)
        if a['high_bidder'] is not None: adjust_balance(a['high_bidder'],cash=a['high_bid'],con=con)
        con.execute('UPDATE auctions SET high_bid=?,high_bidder=? WHERE id=?',(amount,uid,aid))
        audit(uid,'auction_bid',f'{aid} {amount}',con)
    await update.message.reply_text(f'✅ Bid {fmt(amount)} recorded for auction #{aid}.')

async def cancel_auction(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text('Use /cancelauction <auction_id>.')
    try: aid=int(context.args[0])
    except ValueError: return await update.message.reply_text('Auction ID must be a number.')
    uid=update.effective_user.id
    with db() as con:
        a=con.execute('SELECT * FROM auctions WHERE id=? AND status="open" AND created_by=?',(aid,uid)).fetchone()
        if not a: return await update.message.reply_text('Open auction not found.')
        if a['high_bidder'] is not None: return await update.message.reply_text('Cannot cancel after a bid has been placed.')
        add_to_collection(uid,a['character_id'],update.effective_user.username or '',con)
        con.execute('UPDATE auctions SET status="cancelled" WHERE id=?',(aid,))
    await update.message.reply_text('Auction cancelled; character returned to your collection.')

def register_marketplace(app):
    app.add_handler(CommandHandler('market',market_cmd)); app.add_handler(CommandHandler('marketplace',market_cmd))
    app.add_handler(CommandHandler('buy',buy_cmd)); app.add_handler(CommandHandler('auction',auction_cmd))
    app.add_handler(CommandHandler('bid',bid_cmd)); app.add_handler(CommandHandler('cancelauction',cancel_auction))
