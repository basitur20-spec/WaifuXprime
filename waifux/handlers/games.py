from __future__ import annotations
import random
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from ..database import db, get_balance, adjust_balance
from ..utils import fmt

async def game(update:Update,context:ContextTypes.DEFAULT_TYPE):
    name=update.effective_message.text.split()[0].split('@')[0][1:]
    if not context.args: return await update.message.reply_text(f'Use /{name} <bet>.')
    try: bet=int(context.args[0].replace(',',''))
    except ValueError: return await update.message.reply_text('Bet must be a whole number.')
    if bet<1: return await update.message.reply_text('Bet must be positive.')
    uid=update.effective_user.id
    with db() as con:
        cash,_=get_balance(uid,con)
        if cash<bet: return await update.message.reply_text('Not enough cash.')
        adjust_balance(uid,cash=-bet,con=con)
        if name=='roll': won=random.random()<0.48
        elif name=='darts': won=random.random()<0.45
        elif name=='goal': won=random.random()<0.45
        else: won=random.random()<0.25
        prize=bet*2 if won and name!='mines' else bet*3 if won else 0
        if prize: adjust_balance(uid,cash=prize,con=con)
    await update.message.reply_text(('🎉 You won!' if won else '😢 You lost.')+f' Bet: {fmt(bet)} · payout: {fmt(prize)}')

async def dtops_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    with db() as con:
        rows=con.execute('SELECT user_id,diamonds FROM balance.user_balance ORDER BY diamonds DESC LIMIT 10').fetchall()
        text='💎 <b>Diamond leaderboard</b>\n'+('\n'.join(f'{i}. <code>{r["user_id"]}</code> — {fmt(r["diamonds"])}' for i,r in enumerate(rows,1)) or 'No users yet.')
    await update.message.reply_text(text,parse_mode='HTML')

def register_games(app):
    for name in ('roll','darts','goal','mines'):
        app.add_handler(CommandHandler(name,game))
    app.add_handler(CommandHandler('endmines',game))
    app.add_handler(CommandHandler('dtops',dtops_cmd))
