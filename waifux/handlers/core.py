from __future__ import annotations
import random,time
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import CommandHandler,CallbackQueryHandler,ContextTypes
from ..database import db,get_balance,adjust_balance,user_state
from ..utils import fmt

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    with db() as con: user_state(u.id,u.username or '',u.first_name or '',con)
    await update.message.reply_text(f'Welcome {u.first_name} to WaifuX Prime!\nUse /help to see bot commands.')

async def help_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    text=('📘 <b>WaifuX Prime Commands</b>\n'
          '<b>Characters:</b> /hunt /waifu /marriage /propose /collection /fav /dmode /find /dfind\n'
          '<b>Economy:</b> /balance /bonus /explore /pay /sell /market\n'
          '<b>Games:</b> /roll /darts /goal /mines /dtops\n'
          '<b>Staff:</b> /upload /dupload /gen /dgen /give /auction /spawn /changetime')
    await update.message.reply_text(text,parse_mode='HTML')

async def balance_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    cash,dia=get_balance(update.effective_user.id)
    await update.message.reply_text(f'💰 Cash: {fmt(cash)}\n💎 Diamonds: {fmt(dia)}')

async def explore_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; now=int(time.time())
    with db() as con:
        s=user_state(uid,update.effective_user.username or '',update.effective_user.first_name or '',con)
        if now-s['last_waifu']<1800: return await update.message.reply_text('Explore is cooling down. Try again later.')
        cash=random.randint(100,500); dia=random.randint(1,5)
        adjust_balance(uid,cash=cash,diamonds=dia,con=con)
        con.execute('UPDATE user_state SET last_waifu=? WHERE user_id=?',(now,uid))
    await update.message.reply_text(f'🗺️ Adventure complete!\n💰 +{cash} Cash\n💎 +{dia} Diamonds')

async def bonus_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('Daily bonus',callback_data='bonus:daily'),InlineKeyboardButton('Weekly bonus',callback_data='bonus:weekly')]])
    await update.message.reply_text('🎁 Bonus center\nChoose a reward:',reply_markup=kb)

async def bonus_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); kind=q.data.split(':',1)[1]; uid=q.from_user.id; now=int(time.time())
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS bonus_state(user_id INTEGER PRIMARY KEY,daily_at INTEGER NOT NULL DEFAULT 0,weekly_at INTEGER NOT NULL DEFAULT 0)')
        con.execute('INSERT OR IGNORE INTO bonus_state(user_id) VALUES(?)',(uid,))
        row=con.execute('SELECT * FROM bonus_state WHERE user_id=?',(uid,)).fetchone(); key='daily_at' if kind=='daily' else 'weekly_at'
        wait=86400 if kind=='daily' else 604800
        if now-row[key]<wait: return await q.answer('Already claimed. Come back later.',show_alert=True)
        cash=random.randint(500,1500) if kind=='daily' else random.randint(3000,6000)
        adjust_balance(uid,cash=cash,con=con); con.execute(f'UPDATE bonus_state SET {key}=? WHERE user_id=?',(now,uid))
    await q.edit_message_text(f'✅ {kind.title()} bonus claimed!\n💰 +{cash} Cash')

async def pay_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message; target=msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None
    if not target or not context.args: return await msg.reply_text('Reply to a user and use /pay <amount>.')
    try: amount=int(context.args[0].replace(',',''))
    except ValueError: return await msg.reply_text('Enter a valid amount.')
    if amount<=0 or target.id==update.effective_user.id: return await msg.reply_text('Invalid transfer.')
    with db() as con:
        cash,_=get_balance(update.effective_user.id,con)
        if cash<amount: return await msg.reply_text('Not enough cash.')
        adjust_balance(update.effective_user.id,cash=-amount,con=con); adjust_balance(target.id,cash=amount,con=con)
    await msg.reply_text(f'✅ Sent {fmt(amount)} cash.')

def register_core(app):
    app.add_handler(CommandHandler('start',start)); app.add_handler(CommandHandler('help',help_cmd))
    app.add_handler(CommandHandler('balance',balance_cmd)); app.add_handler(CommandHandler('explore',explore_cmd))
    app.add_handler(CommandHandler('bonus',bonus_cmd)); app.add_handler(CommandHandler('pay',pay_cmd))
    app.add_handler(CallbackQueryHandler(bonus_callback,pattern=r'^bonus:'))
