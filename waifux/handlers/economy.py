from __future__ import annotations
import json, random, time, uuid
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from ..database import db, get_balance, adjust_balance, get_character, add_to_collection, collection_rows, user_state, audit
from ..permissions import has_role
from ..utils import fmt, username, day_key

async def code_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if not has_role(u.id,'owner','sudo'): return
    if len(context.args)<3: return await update.message.reply_text('Use /gen cash|diamonds|waifu <reward> <quantity>.')
    kind=context.args[0].lower()
    if kind not in ('cash','diamonds','waifu'): return await update.message.reply_text('Type cash, diamonds, or waifu.')
    reward=' '.join(context.args[1:-1]); qty=int(context.args[-1])
    if qty<1 or qty>1000: return await update.message.reply_text('Quantity must be 1–1000.')
    cid=uuid.uuid4().hex[:10].upper()
    with db() as con: con.execute('INSERT INTO codes(code,kind,reward,quantity,creator,created_at) VALUES(?,?,?,?,?,?)',(cid,kind,reward,qty,u.id,int(time.time())))
    await update.message.reply_text(f'Code: <code>{cid}</code> (uses: {qty})',parse_mode='HTML')

async def redeem_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text('Use /redeem <code>.')
    code=context.args[0].upper(); uid=update.effective_user.id
    with db() as con:
        row=con.execute('SELECT * FROM codes WHERE code=?',(code,)).fetchone()
        if not row: return await update.message.reply_text('Invalid code.')
        used=json.loads(row['used_by'])
        if uid in used or row['quantity']<=0: return await update.message.reply_text('This code was already used or expired.')
        try:
            if row['kind']=='cash': adjust_balance(uid,cash=int(row['reward']),con=con)
            elif row['kind']=='diamonds': adjust_balance(uid,diamonds=int(row['reward']),con=con)
            elif row['kind']=='waifu': add_to_collection(uid,row['reward'],update.effective_user.username or '',con)
            else: return await update.message.reply_text('Invalid code reward.')
        except (ValueError,KeyError): return await update.message.reply_text('The reward is no longer available.')
        used.append(uid); con.execute('UPDATE codes SET used_by=?,quantity=quantity-1 WHERE code=?',(json.dumps(used),code))
    await update.message.reply_text('✅ Code redeemed!')

async def pay_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; msg=update.effective_message
    target=msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None
    args=context.args
    if not target and args:
        ident=args[0].lstrip('@')
        with db() as con:
            targetrow=con.execute('SELECT user_id FROM user_state WHERE username=? COLLATE NOCASE',(ident,)).fetchone()
        if targetrow:
            class U: pass
            target=U(); target.id=targetrow['user_id']; target.username=ident; target.full_name=ident
    if not target or target.id==user.id: return await msg.reply_text('Reply to a user or use /pay @username <amount>.')
    amountarg=args[-1] if args else ''
    currency='cash'
    if amountarg.lower().endswith('d'): currency='diamonds'; amountarg=amountarg[:-1]
    try: amount=int(amountarg.replace(',',''))
    except ValueError: return await msg.reply_text('Enter an amount. Add d for diamonds, e.g. 20d.')
    if amount<=0: return await msg.reply_text('Amount must be positive.')
    with db() as con:
        state=user_state(user.id,user.username or '',user.first_name or '',con)
        if int(time.time())-state['last_pay']<5: return await msg.reply_text('Wait a few seconds before another transfer.')
        cash,dia=get_balance(user.id,con)
        if (dia if currency=='diamonds' else cash)<amount: return await msg.reply_text('Insufficient balance.')
        adjust_balance(user.id,**({'diamonds':-amount} if currency=='diamonds' else {'cash':-amount}),con=con)
        adjust_balance(target.id,**({'diamonds':amount} if currency=='diamonds' else {'cash':amount}),con=con)
        con.execute('UPDATE user_state SET last_pay=? WHERE user_id=?',(int(time.time()),user.id))
        con.execute('INSERT INTO payment_log(id,payer,receiver,amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,user.id,target.id,amount,currency,'completed',int(time.time())))
    await msg.reply_text(f'✅ Sent {fmt(amount)} {currency} to {username(target)}.')

async def sell_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    user=update.effective_user
    if len(context.args)<2: return await update.message.reply_text('Use /sell <waifu_id> <price>.')
    cid=context.args[0]
    try: price=int(context.args[1].replace(',',''))
    except ValueError: return await update.message.reply_text('Price must be a number.')
    if price<1: return await update.message.reply_text('Price must be positive.')
    with db() as con:
        row=con.execute('SELECT rowid FROM collection.collections WHERE user_id=? AND waifu_id=? ORDER BY rowid LIMIT 1',(user.id,cid)).fetchone()
        if not row: return await update.message.reply_text('You do not own that waifu.')
        con.execute('DELETE FROM collection.collections WHERE rowid=?',(row['rowid'],))
        con.execute('INSERT INTO marketplace(user_id,slot,day,character_id,price,discount,bought) VALUES(?,0,?,?,?,0,0)',(user.id,0,day_key(),cid,price))
        adjust_balance(user.id,cash=price,con=con)
        audit(user.id,'sell',f'{cid} price={price}',con)
    await update.message.reply_text(f'✅ Sold {cid} for {fmt(price)} cash.')

def register_economy(app):
    for name,fn in [('gen',code_cmd),('dgen',code_cmd),('redeem',redeem_cmd),('dredeem',redeem_cmd),('pay',pay_cmd),('sell',sell_cmd)]:
        app.add_handler(CommandHandler(name,fn))
