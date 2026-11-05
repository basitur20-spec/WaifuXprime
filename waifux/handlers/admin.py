from __future__ import annotations
import time
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from ..database import db, adjust_balance, audit
from ..permissions import has_role, is_owner

async def role_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo'): return
    if len(context.args)<2: return await update.message.reply_text('Use /addsudo, /rsudo, /adduploader, /ruploader <user id>.')
    op=update.effective_message.text.split()[0].split('@')[0][1:]
    try: uid=int(context.args[0] if len(context.args)==1 else context.args[-1])
    except ValueError: return await update.message.reply_text('Provide a numeric user ID.')
    role='sudo' if 'sudo' in op else 'uploader'
    adding=op.startswith('add')
    if role=='owner' or (not is_owner(update.effective_user.id) and role=='sudo' and not has_role(update.effective_user.id,'owner')): return
    with db() as con:
        if adding: con.execute('INSERT OR IGNORE INTO roles(user_id,role) VALUES(?,?)',(uid,role))
        else: con.execute('DELETE FROM roles WHERE user_id=? AND role=?',(uid,role))
        audit(update.effective_user.id,op,str(uid),con)
    await update.message.reply_text(f'{role.title()} access {"added" if adding else "removed"} for {uid}.')

async def role_list(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo'): return
    role='sudo' if 'sudo' in update.effective_message.text else 'uploader'
    with db() as con: rows=con.execute('SELECT user_id FROM roles WHERE role=? ORDER BY user_id',(role,)).fetchall()
    await update.message.reply_text(role.title()+': '+(', '.join(str(r['user_id']) for r in rows) or 'none'))

async def command_switch(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo'): return
    args=context.args
    if not args: return await update.message.reply_text('Use /stop <command> or /run <command>.')
    cmd=args[0].lstrip('/').lower()
    stop=update.effective_message.text.split()[0].lstrip('/').lower()=='stop'
    with db() as con:
        if stop: con.execute('INSERT OR REPLACE INTO stopped_commands(command,stopped_at,reason) VALUES(?,?,?)',(cmd,int(time.time()),''))
        else: con.execute('DELETE FROM stopped_commands WHERE command=?',(cmd,))
        audit(update.effective_user.id,'stop' if stop else 'run',cmd,con)
    await update.message.reply_text(f'/{cmd} {"stopped" if stop else "enabled"}.')

async def give_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo'): return
    if len(context.args)<3: return await update.message.reply_text('Use /give <user_id> cash|diamonds <amount>.')
    try: uid=int(context.args[0]); amount=int(context.args[2])
    except ValueError: return await update.message.reply_text('IDs and amount must be numbers.')
    kind=context.args[1].lower()
    if amount<=0 or kind not in ('cash','diamonds'): return await update.message.reply_text('Invalid reward.')
    with db() as con: adjust_balance(uid,**{kind:amount},con=con); audit(update.effective_user.id,'give',f'{uid} {kind} {amount}',con)
    await update.message.reply_text('✅ Balance updated.')

async def claim_admin(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo'): return
    if len(context.args)<2: return await update.message.reply_text('Use /claim cash|diamonds|waifu <reward>.')
    kind=context.args[0].lower(); value=' '.join(context.args[1:])
    with db() as con: con.execute('INSERT INTO claim_events(reward_type,reward_value,active,created_at) VALUES(?,?,1,?)',(kind,value,int(time.time())))
    await update.message.reply_text('Claim event created.')

async def broadcast(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo'): return
    text=' '.join(context.args)
    if not text: return await update.message.reply_text('Use /send <message>.')
    with db() as con: rows=con.execute('SELECT user_id FROM user_state').fetchall()
    ok=0
    for r in rows:
        try: await context.bot.send_message(r['user_id'],text); ok+=1
        except Exception: pass
    await update.message.reply_text(f'Broadcast delivered to {ok} users.')

def register_admin(app):
    for cmd in ('addsudo','adduploader','rsudo','ruploader'): app.add_handler(CommandHandler(cmd,role_cmd))
    for cmd in ('sudolist','uploaderlist'): app.add_handler(CommandHandler(cmd,role_list))
    for cmd in ('stop','run'): app.add_handler(CommandHandler(cmd,command_switch))
    app.add_handler(CommandHandler('give',give_cmd)); app.add_handler(CommandHandler('claim',claim_admin)); app.add_handler(CommandHandler('send',broadcast))
