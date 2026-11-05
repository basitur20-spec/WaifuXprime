from __future__ import annotations
import random,time
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import CallbackQueryHandler,CommandHandler,ContextTypes
from ..config import OWNER_ID
from ..database import db,get_balance,adjust_balance,user_state
from ..utils import fmt,remaining

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; user_state(u.id,u.username or '',u.first_name or '')
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('HELP ⚙️',callback_data='help:open')],[InlineKeyboardButton('Add me to group',url=f'https://t.me/{context.bot.username}?startgroup=true')]])
    await update.message.reply_text(f'Welcome {u.first_name} to WaifuX Prime!
Use /help to see bot commands.',reply_markup=kb)

async def help_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    text=('📘 <b>WaifuX Prime Commands</b>

'
          '<b>Characters:</b> /hunt /waifu /marriage /propose /collection /fav /dmode /f /find /dfind
'
          '<b>Economy:</b> /balance /bonus /explore /pay /sell /marketplace
'
          '<b>Games:</b> /roll /darts /goal /mines /endmines
'
          '<b>Staff:</b> /upload /dupload /gen /dgen /give /auction /spawn /changetime
'
          'Use /collection to view characters you own.')
    await update.message.reply_text(text,parse_mode='HTML')

async def balance_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    cash,diamonds=get_balance(update.effective_user.id)
    await update.message.reply_text(f'💰 Cash: {fmt(cash)}
💎 Diamonds: {fmt(diamonds)}')

async def explore_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; now=int(time.time())
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS explore_cooldown(user_id INTEGER PRIMARY KEY,until_at INTEGER NOT NULL)')
        row=con.execute('SELECT until_at FROM explore_cooldown WHERE user_id=?',(uid,)).fetchone()
        if row and row['until_at']>now:
            await update.message.reply_text(f'⏳ Wait {remaining(row["until_at"]-now)} before exploring again.'); return
        cash=random.randint(2300,6500); dia=random.choices([0,1,2],[85,10,5])[0]
        adjust_balance(uid,cash,dia,con)
        con.execute('INSERT INTO explore_cooldown(user_id,until_at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET until_at=excluded.until_at',(uid,now+60))
    await update.message.reply_text(f'🗺️ Adventure complete!
💰 +{fmt(cash)} Cash
💎 +{dia} Diamonds')

async def bonus_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('Daily bonus',callback_data='bonus:daily'),InlineKeyboardButton('Weekly bonus',callback_data='bonus:weekly')],[InlineKeyboardButton('Stats',callback_data='bonus:stats')]])
    await update.message.reply_text('🎁 Bonus center
Choose a reward:',reply_markup=kb)

async def bonus_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; now=int(time.time()); kind=q.data.split(':')[1]
    if kind=='stats':
        await q.edit_message_text('Daily and weekly rewards have separate cooldowns.'); return
    cooldown=86400 if kind=='daily' else 604800; reward=70000 if kind=='daily' else 300000; dia=10 if kind=='daily' else 30
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS bonus_claims(user_id INTEGER NOT NULL,kind TEXT NOT NULL,claimed_at INTEGER NOT NULL,PRIMARY KEY(user_id,kind))')
        row=con.execute('SELECT claimed_at FROM bonus_claims WHERE user_id=? AND kind=?',(uid,kind)).fetchone()
        if row and row['claimed_at']+cooldown>now:
            await q.edit_message_text(f'⏳ Try again in {remaining(row["claimed_at"]+cooldown-now)}.'); return
        adjust_balance(uid,reward,dia,con)
        con.execute('INSERT INTO bonus_claims(user_id,kind,claimed_at) VALUES(?,?,?) ON CONFLICT(user_id,kind) DO UPDATE SET claimed_at=excluded.claimed_at',(uid,kind,now))
    await q.edit_message_text(f'✅ {kind.title()} bonus claimed!
💰 +{fmt(reward)} Cash
💎 +{dia} Diamonds')

async def pay_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.message; uid=msg.from_user.id
    if not msg.reply_to_message:
        await msg.reply_text('Reply to a user and use /pay <amount>.'); return
    receiver=msg.reply_to_message.from_user
    if receiver.is_bot or receiver.id==uid:
        await msg.reply_text('You can pay another person only.'); return
    try: amount=int(context.args[0].replace(',',''))
    except (IndexError,ValueError):
        await msg.reply_text('Use /pay <positive amount>.'); return
    if amount<=0:
        await msg.reply_text('Amount must be greater than zero.'); return
    now=int(time.time()); cash,_=get_balance(uid)
    if cash<amount:
        await msg.reply_text('❌ You do not have enough Cash.'); return
    with db() as con:
        state=user_state(uid,msg.from_user.username or '',msg.from_user.first_name or '',con)
        if state['last_pay']+300>now and uid!=OWNER_ID:
            await msg.reply_text(f'⏳ Wait {remaining(state["last_pay"]+300-now)} before paying again.'); return
        import uuid,json
        tx=uuid.uuid4().hex
        payload=json.dumps({'receiver':receiver.id,'receiver_name':receiver.full_name,'amount':amount,'currency':'cash'})
        con.execute('INSERT INTO pending_actions(id,user_id,kind,payload,expires_at) VALUES(?,?,?,?,?)',(tx,uid,'pay',payload,now+120))
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirm',callback_data=f'pay:ok:{tx}'),InlineKeyboardButton('❌ Cancel',callback_data=f'pay:no:{tx}')]])
    await msg.reply_text(f'💸 Pay {fmt(amount)} Cash to {receiver.full_name}? Funds are checked and moved once on confirmation.',reply_markup=kb)

async def pay_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); _,action,tx=q.data.split(':',2); uid=q.from_user.id; now=int(time.time())
    with db() as con:
        row=con.execute('SELECT * FROM pending_actions WHERE id=? AND kind="pay"',(tx,)).fetchone()
        if not row or row['user_id']!=uid or row['expires_at']<now:
            await q.edit_message_text('❌ This payment request expired.'); return
        if action=='no':
            con.execute('DELETE FROM pending_actions WHERE id=?',(tx,)); await q.edit_message_text('Payment cancelled. No balance changed.'); return
        import json
        p=json.loads(row['payload']); amount=int(p['amount']); receiver=int(p['receiver'])
        cash,_=get_balance(uid,con)
        if cash<amount:
            con.execute('DELETE FROM pending_actions WHERE id=?',(tx,)); await q.edit_message_text('❌ Not enough Cash.'); return
        adjust_balance(uid,-amount,0,con); adjust_balance(receiver,amount,0,con)
        con.execute('UPDATE user_state SET last_pay=? WHERE user_id=?',(now,uid))
        con.execute('INSERT INTO payment_log(id,payer,receiver,amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?)',(tx,uid,receiver,amount,'cash','complete',now))
        con.execute('DELETE FROM pending_actions WHERE id=?',(tx,))
    await q.edit_message_text(f'✅ Payment successful. {fmt(amount)} Cash sent to {p["receiver_name"]}.')
    try: await context.bot.send_message(receiver,f'🎉 You received {fmt(amount)} Cash from {q.from_user.full_name}.')
    except Exception: pass

def register_core(app):
    app.add_handler(CommandHandler('start',start)); app.add_handler(CommandHandler('help',help_cmd))
    app.add_handler(CommandHandler('balance',balance_cmd)); app.add_handler(CommandHandler('explore',explore_cmd))
    app.add_handler(CommandHandler('bonus',bonus_cmd)); app.add_handler(CommandHandler('pay',pay_cmd)); app.add_handler(CommandHandler('payrefresh',pay_cmd))
    app.add_handler(CallbackQueryHandler(bonus_callback,pattern=r'^bonus:'))
    app.add_handler(CallbackQueryHandler(pay_callback,pattern=r'^pay:'))
