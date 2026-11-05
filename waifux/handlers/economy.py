from __future__ import annotations
import json,random,time,uuid
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import CommandHandler,CallbackQueryHandler,ContextTypes
from ..database import db,get_balance,adjust_balance,get_character,add_to_collection,user_state,audit,remove_from_collection
from ..permissions import has_role,is_owner
from ..utils import fmt,username,day_key,rarity_name

async def code_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if not has_role(u.id,'owner','sudo'): return
    if len(context.args)<3: return await update.message.reply_text('Use /gen cash|diamonds|waifu <reward> <quantity>.')
    kind=context.args[0].lower()
    if kind not in ('cash','diamonds','waifu'): return await update.message.reply_text('Type cash, diamonds, or waifu.')
    reward=' '.join(context.args[1:-1])
    try: qty=int(context.args[-1])
    except ValueError: return await update.message.reply_text('Quantity must be a number.')
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
    msg=update.effective_message; user=update.effective_user
    target=msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None
    if not target or not context.args: return await msg.reply_text('Reply to a user and use /pay <cash amount> or /pay <diamonds amount>d.')
    raw=context.args[0].replace(',',''); currency='cash'
    if raw.lower().endswith('d'): currency='diamonds'; raw=raw[:-1]
    try: amount=int(raw)
    except ValueError: return await msg.reply_text('Amount must be a whole number.')
    if amount<=0 or target.id==user.id: return await msg.reply_text('Invalid amount or recipient.')
    with db() as con:
        cash,dia=get_balance(user.id,con)
        if (dia if currency=='diamonds' else cash)<amount: return await msg.reply_text('Insufficient balance.')
        tx=uuid.uuid4().hex
        payload={'receiver':target.id,'name':target.full_name,'amount':amount,'currency':currency}
        con.execute('INSERT INTO pending_actions(id,user_id,kind,payload,expires_at) VALUES(?,?,?,?,?)',(tx,user.id,'pay',json.dumps(payload),int(time.time())+120))
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirm',callback_data=f'pay:ok:{tx}'),InlineKeyboardButton('❌ Cancel',callback_data=f'pay:no:{tx}')]])
    await msg.reply_text(f'Confirm sending {fmt(amount)} {currency} to {username(target)}?',reply_markup=kb)

async def pay_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); _,action,tx=q.data.split(':',2); uid=q.from_user.id
    with db() as con:
        row=con.execute('SELECT * FROM pending_actions WHERE id=? AND kind="pay"',(tx,)).fetchone()
        if not row or row['user_id']!=uid or row['expires_at']<int(time.time()): return await q.answer('Expired or not yours.',show_alert=True)
        p=json.loads(row['payload']); con.execute('DELETE FROM pending_actions WHERE id=?',(tx,))
        if action=='ok':
            key=p['currency']; amount=int(p['amount']); cash,dia=get_balance(uid,con)
            if (dia if key=='diamonds' else cash)<amount: return await q.edit_message_text('❌ Balance changed; payment cancelled.')
            adjust_balance(uid,**{key:-amount},con=con); adjust_balance(p['receiver'],**{key:amount},con=con)
            con.execute('INSERT INTO payment_log(id,payer,receiver,amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?)',(tx,uid,p['receiver'],amount,key,'complete',int(time.time())))
            audit(uid,'pay',f'{p["receiver"]} {key} {amount}',con)
    await q.edit_message_text('✅ Payment sent.' if action=='ok' else 'Payment cancelled.')
    if action=='ok':
        try: await context.bot.send_message(p['receiver'],f'You received {fmt(p["amount"])} {p["currency"]}.')
        except Exception: pass

async def sell_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; args=context.args; now=int(time.time())
    if len(args)==2:
        r=rarity_name(args[0])
        if r and args[1].rstrip('%').isdigit() and is_owner(user.id):
            rate=int(args[1].rstrip('%'))
            if not 1<=rate<=100: return await update.message.reply_text('Sell percentage must be between 1 and 100.')
            with db() as con:
                db_set='sell_rate:'+r
                con.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                con.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(db_set,str(rate)))
            return await update.message.reply_text(f'✅ {r} sell rate set to {rate}%.')
    if len(args)>=2 and args[0].lower() in ('diamond','diamonds','d'):
        try: amount=int(args[1].replace(',',''))
        except ValueError: return await update.message.reply_text('Use /sell diamonds <amount>.')
        if amount<=0: return await update.message.reply_text('Amount must be positive.')
        with db() as con:
            state=user_state(user.id,user.username or '',user.first_name or '',con)
            if state['last_sell']+60>now: return await update.message.reply_text(f'⏳ Wait {state["last_sell"]+60-now}s before selling again.')
            _,diamonds=get_balance(user.id,con)
            if diamonds<amount: return await update.message.reply_text('❌ Not enough diamonds.')
            tx=uuid.uuid4().hex; proceeds=amount*10000
            payload={'type':'diamonds','amount':amount,'cash':proceeds}
            con.execute('UPDATE user_state SET last_sell=? WHERE user_id=?',(now,user.id))
            con.execute('INSERT INTO pending_actions(id,user_id,kind,payload,expires_at) VALUES(?,?,?,?,?)',(tx,user.id,'sell',json.dumps(payload),now+300))
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirm',callback_data=f'sell:ok:{tx}'),InlineKeyboardButton('❌ Cancel',callback_data=f'sell:no:{tx}')]])
        return await update.message.reply_text(f'⚠️ Confirm selling {fmt(amount)} 💎 for {fmt(proceeds)} cash?',reply_markup=kb)
    if not args: return await update.message.reply_text('Use /sell <waifu ID...>, /sell diamonds <amount>, or owner: /sell <rarity> <percent>.')
    ids=args
    if len(ids)>5: return await update.message.reply_text('You can sell at most 5 waifus at a time.')
    with db() as con:
        state=user_state(user.id,user.username or '',user.first_name or '',con); today=day_key()
        if state['last_sell']+60>now: return await update.message.reply_text(f'⏳ Wait {state["last_sell"]+60-now}s before selling again.')
        sold_today=state['sell_count'] if state['sell_day']==today else 0
        if sold_today+len(ids)>5: return await update.message.reply_text('⛔ Daily limit reached: 5 waifus per day.')
        items=[]
        for cid in ids:
            row=con.execute('SELECT rowid AS item_rowid,* FROM collection.collections WHERE user_id=? AND waifu_id=? ORDER BY rowid LIMIT 1',(user.id,cid)).fetchone()
            if not row: return await update.message.reply_text(f'❌ Waifu {cid} is not in your collection.')
            char=get_character(cid,con); rarity=row['rarity'] or (char['rarity'] if char else 'Starter')
            base=int(char['price'] if char else 0)
            if base<=0:
                from ..utils import price_for_rarity
                base=price_for_rarity(rarity)
            rate=int(__import__('waifux.database',fromlist=['get_setting']).get_setting('sell_rate:'+rarity,'50',con))
            items.append({'id':cid,'name':row['name'] or (char['name'] if char else cid),'rarity':rarity,'base':base,'rate':rate,'cash':base*rate//100})
        tx=uuid.uuid4().hex
        con.execute('UPDATE user_state SET last_sell=? WHERE user_id=?',(now,user.id))
        payload={'type':'waifus','items':items,'day':today}
        con.execute('INSERT INTO pending_actions(id,user_id,kind,payload,expires_at) VALUES(?,?,?,?,?)',(tx,user.id,'sell',json.dumps(payload),now+300))
    summary='\\n'.join(f'• {x["name"]} ({x["rarity"]}) #{x["id"]}: {fmt(x["base"])} × {x["rate"]}% = {fmt(x["cash"])}' for x in items)
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirm',callback_data=f'sell:ok:{tx}'),InlineKeyboardButton('❌ Cancel',callback_data=f'sell:no:{tx}')]])
    await update.message.reply_text(f'⚠️ <b>CONFIRM WAIFU SELL</b>\\n{summary}\\n\\nTotal: {fmt(sum(x["cash"] for x in items))} cash.\\nConfirm? Cancel par bhi 60-second cooldown apply hoga.',parse_mode='HTML',reply_markup=kb)

async def sell_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); _,action,tx=q.data.split(':',2); uid=q.from_user.id
    with db() as con:
        row=con.execute('SELECT * FROM pending_actions WHERE id=? AND kind="sell"',(tx,)).fetchone()
        if not row or row['user_id']!=uid or row['expires_at']<int(time.time()): return await q.answer('Expired or not yours.',show_alert=True)
        p=json.loads(row['payload']); con.execute('DELETE FROM pending_actions WHERE id=?',(tx,))
        if action=='no':
            message='Sale cancelled. 60-second cooldown remains active.'
        elif p['type']=='diamonds':
            _,dia=get_balance(uid,con)
            if dia<p['amount']: message='Balance changed; sale cancelled.'
            else:
                adjust_balance(uid,diamonds=-p['amount'],cash=p['cash'],con=con)
                con.execute('CREATE TABLE IF NOT EXISTS sell_log(id TEXT,user_id INTEGER,kind TEXT,item TEXT,cash INTEGER,created_at INTEGER)')
                con.execute('INSERT INTO sell_log VALUES(?,?,?,?,?,?)',(tx,uid,'diamonds',str(p['amount']),p['cash'],int(time.time())))
                audit(uid,'sell_diamonds',f'{p["amount"]} -> {p["cash"]}',con); message=f'✅ Sold {fmt(p["amount"])} diamonds for {fmt(p["cash"])} cash.'
        else:
            state=user_state(uid,q.from_user.username or '',q.from_user.first_name or '',con); today=day_key()
            sold_today=state['sell_count'] if state['sell_day']==today else 0
            if sold_today+len(p['items'])>5: return await q.edit_message_text('Daily sell limit reached; sale cancelled.')
            for item in p['items']:
                if not remove_from_collection(uid,item['id'],con): return await q.edit_message_text(f'Waifu {item["id"]} is no longer in your collection; sale cancelled.')
            total=sum(int(x['cash']) for x in p['items']); adjust_balance(uid,cash=total,con=con)
            con.execute('UPDATE user_state SET sell_day=?,sell_count=?,last_sell=? WHERE user_id=?',(today,sold_today+len(p['items']),int(time.time()),uid))
            con.execute('CREATE TABLE IF NOT EXISTS sell_log(id TEXT,user_id INTEGER,kind TEXT,item TEXT,cash INTEGER,created_at INTEGER)')
            con.executemany('INSERT INTO sell_log VALUES(?,?,?,?,?,?)',[(uuid.uuid4().hex,uid,'waifu',x['id'],x['cash'],int(time.time())) for x in p['items']])
            audit(uid,'sell_waifu',json.dumps([x['id'] for x in p['items']]),con); message=f'✅ Sold {len(p["items"])} waifu(s) for {fmt(total)} cash.'
    await q.edit_message_text(message)

def register_economy(app):
    for name,fn in [('gen',code_cmd),('dgen',code_cmd),('redeem',redeem_cmd),('dredeem',redeem_cmd),('pay',pay_cmd),('sell',sell_cmd)]:
        app.add_handler(CommandHandler(name,fn))
    app.add_handler(CallbackQueryHandler(pay_callback,pattern=r'^pay:'))
    app.add_handler(CallbackQueryHandler(sell_callback,pattern=r'^sell:'))
