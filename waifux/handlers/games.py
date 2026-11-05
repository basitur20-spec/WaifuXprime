from __future__ import annotations
import json,random,time,uuid
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import CallbackQueryHandler,CommandHandler,ContextTypes
from ..database import db,get_balance,adjust_balance
from ..utils import fmt

def _mines_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS mines_games(
        id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,bet INTEGER NOT NULL,mines TEXT NOT NULL,
        opened TEXT NOT NULL DEFAULT '[]',multiplier REAL NOT NULL DEFAULT 1.0,
        status TEXT NOT NULL DEFAULT 'active',created_at INTEGER NOT NULL
    )""")

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
        # Telegram-style instant games use a small, fixed house edge.
        chance={'roll':0.48,'darts':0.45,'goal':0.45}.get(name,0.45)
        won=random.random()<chance
        payout=bet*2 if won else 0
        if payout: adjust_balance(uid,cash=payout,con=con)
    await update.message.reply_text(('🎉 You won!' if won else '😢 You lost.')+f' Bet: {fmt(bet)} · payout: {fmt(payout)}')

def _mines_keyboard(game_id,uid,mines,opened,finished=False,hit=None):
    buttons=[]
    for i in range(25):
        if finished and i in mines: label='💣'
        elif i in opened: label='✅'
        elif finished: label='▫️'
        else: label='▫️'
        if finished or i in opened:
            buttons.append(InlineKeyboardButton(label,callback_data=f'mine:noop:{uid}:{game_id}'))
        else:
            buttons.append(InlineKeyboardButton(label,callback_data=f'mine:tile:{uid}:{game_id}:{i}'))
    rows=[buttons[i:i+5] for i in range(0,25,5)]
    if not finished:
        rows.append([InlineKeyboardButton('💰 Cash out',callback_data=f'mine:cash:{uid}:{game_id}')])
    return InlineKeyboardMarkup(rows)

async def mines_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text('Use /mines <bet>. A board has 25 tiles and 5 mines.')
    try: bet=int(context.args[0].replace(',',''))
    except ValueError: return await update.message.reply_text('Bet must be a whole number.')
    if bet<1: return await update.message.reply_text('Bet must be positive.')
    uid=update.effective_user.id; gid=uuid.uuid4().hex[:10]
    mines=random.sample(range(25),5)
    with db() as con:
        _mines_schema(con)
        if con.execute("SELECT 1 FROM mines_games WHERE user_id=? AND status='active'",(uid,)).fetchone():
            return await update.message.reply_text('You already have an active Mines game. Use /endmines to cash out.')
        cash,_=get_balance(uid,con)
        if cash<bet: return await update.message.reply_text('Not enough cash.')
        adjust_balance(uid,cash=-bet,con=con)
        con.execute('INSERT INTO mines_games(id,user_id,bet,mines,opened,multiplier,status,created_at) VALUES(?,?,?,?,?,1.0,"active",?)',(gid,uid,bet,json.dumps(mines),'[]',int(time.time())))
    await update.message.reply_text(f'💣 Mines started · Bet {fmt(bet)} cash · 5 mines. Safe tiles increase your cash-out.',reply_markup=_mines_keyboard(gid,uid,mines,[]))

async def mines_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    parts=q.data.split(':')
    _,action,owner,gid,*tail=parts
    if q.from_user.id!=int(owner): return await q.answer('This game belongs to another user.',show_alert=True)
    if action=='noop': return await q.answer()
    with db() as con:
        _mines_schema(con)
        game=con.execute("SELECT * FROM mines_games WHERE id=? AND user_id=? AND status='active'",(gid,q.from_user.id)).fetchone()
        if not game: return await q.answer('This game has ended.',show_alert=True)
        mines=set(json.loads(game['mines'])); opened=set(json.loads(game['opened']))
        if action=='tile':
            tile=int(tail[0])
            if tile<0 or tile>=25 or tile in opened: return await q.answer('Choose a covered tile.')
            if tile in mines:
                con.execute("UPDATE mines_games SET status='lost' WHERE id=?",(gid,))
                await q.edit_message_text(f'💥 You hit a mine and lost {fmt(game["bet"])} cash.',reply_markup=_mines_keyboard(gid,q.from_user.id,mines,opened,True,tile))
                return await q.answer()
            opened.add(tile)
            multiplier=1.0+0.18*len(opened)
            con.execute('UPDATE mines_games SET opened=?,multiplier=? WHERE id=?',(json.dumps(sorted(opened)),multiplier,gid))
            kb=_mines_keyboard(gid,q.from_user.id,mines,opened)
            await q.edit_message_text(f'✅ Safe tile! {len(opened)} safe · cash-out {fmt(int(game["bet"]*multiplier))} cash.',reply_markup=kb)
            return await q.answer()
        if action=='cash':
            if not opened: return await q.answer('Reveal at least one safe tile first.',show_alert=True)
            payout=int(game['bet']*game['multiplier'])
            adjust_balance(q.from_user.id,cash=payout,con=con)
            con.execute("UPDATE mines_games SET status='cashed_out' WHERE id=?",(gid,))
    await q.edit_message_text(f'💰 Cashed out {fmt(payout)} cash.')
    await q.answer()

async def endmines_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    with db() as con:
        _mines_schema(con)
        game=con.execute("SELECT * FROM mines_games WHERE user_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",(uid,)).fetchone()
        if not game: return await update.message.reply_text('No active Mines game.')
        opened=json.loads(game['opened'])
        if not opened: return await update.message.reply_text('Open one safe tile first before cashing out.')
        payout=int(game['bet']*game['multiplier'])
        adjust_balance(uid,cash=payout,con=con)
        con.execute("UPDATE mines_games SET status='cashed_out' WHERE id=?",(game['id'],))
    await update.message.reply_text(f'💰 Mines cashed out: {fmt(payout)} cash.')

async def dtops_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    with db() as con:
        rows=con.execute('SELECT user_id,diamonds FROM balance.user_balance ORDER BY diamonds DESC LIMIT 10').fetchall()
        text='💎 <b>Diamond leaderboard</b>\n'+('\n'.join(f'{i}. <code>{r["user_id"]}</code> — {fmt(r["diamonds"])}' for i,r in enumerate(rows,1)) or 'No users yet.')
    await update.message.reply_text(text,parse_mode='HTML')

def register_games(app):
    for name in ('roll','darts','goal'): app.add_handler(CommandHandler(name,game))
    app.add_handler(CommandHandler('mines',mines_cmd)); app.add_handler(CommandHandler('endmines',endmines_cmd))
    app.add_handler(CallbackQueryHandler(mines_callback,pattern=r'^mine:'))
    app.add_handler(CommandHandler('dtops',dtops_cmd))
