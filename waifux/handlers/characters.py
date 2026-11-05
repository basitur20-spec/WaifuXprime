from __future__ import annotations
import json,random,re,time,uuid
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import CallbackQueryHandler,CommandHandler,ContextTypes,MessageHandler,filters
from ..config import RARITY_WEIGHTS,SPAWN_EVERY_MESSAGES,SPAWN_LIFETIME_SECONDS
from ..database import db,get_balance,adjust_balance,add_to_collection,get_character,list_characters,add_character,remove_character,user_state
from ..permissions import has_role,is_owner
from ..utils import RARITY_EMOJI,price_for_rarity,rarity_name,remaining,char_info

NO_SPAWN='❌ No character has spawned yet. Please wait for the next spawn.'

def enabled_rarities(con):
    off={r['rarity'] for r in con.execute('SELECT rarity FROM rarity_switch WHERE enabled=0')}
    return [r for r in RARITY_WEIGHTS if r not in off]

def choose_character(con,allowed=None):
    chars=list_characters(allowed,con)
    if not chars: return None
    available=list(dict.fromkeys(c['rarity'] for c in chars))
    rarity=random.choices(available,weights=[RARITY_WEIGHTS.get(r,1) for r in available],k=1)[0]
    return random.choice([c for c in chars if c['rarity']==rarity])

async def send_spawn(application,gid):
    now=int(time.time())
    with db() as con:
        active=con.execute('SELECT * FROM active_spawns WHERE group_id=?',(gid,)).fetchone()
        if active and active['expires_at']>now: return
        ch=choose_character(con,enabled_rarities(con))
        if not ch: return
        con.execute('INSERT OR REPLACE INTO active_spawns(group_id,character_id,expires_at,message_id) VALUES(?,?,?,0)',(gid,ch['id'],now+SPAWN_LIFETIME_SECONDS))
    try:
        sent=await application.bot.send_photo(gid,ch['image'],caption='✨ A new character appeared!\nUse /hunt <name> to claim it.',has_spoiler=True)
        with db() as con: con.execute('UPDATE active_spawns SET message_id=? WHERE group_id=?',(sent.message_id,gid))
        if application.job_queue:
            application.job_queue.run_once(expire_spawn,SPAWN_LIFETIME_SECONDS,data={'group_id':gid,'character_id':ch['id']})
    except Exception:
        with db() as con: con.execute('DELETE FROM active_spawns WHERE group_id=?',(gid,))
        raise

async def expire_spawn(context:ContextTypes.DEFAULT_TYPE):
    d=context.job.data
    with db() as con:
        row=con.execute('SELECT * FROM active_spawns WHERE group_id=? AND character_id=?',(d['group_id'],d['character_id'])).fetchone()
        if not row: return
        con.execute('DELETE FROM active_spawns WHERE group_id=?',(d['group_id'],))
    ch=get_character(d['character_id'])
    if ch:
        try:
            await context.bot.send_message(d['group_id'],f"❌ Too slow! {ch['name']} escaped.\nID: {ch['id']} · {ch['rarity']}")
        except Exception: pass

async def group_message(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message; user=update.effective_user
    if not msg or not user or user.is_bot or msg.chat.type not in ('group','supergroup'): return
    now=int(time.time()); gid=msg.chat_id; uid=user.id
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS spam_messages(chat_id INTEGER,user_id INTEGER,created_at INTEGER)')
        con.execute('DELETE FROM spam_messages WHERE chat_id=? AND user_id=? AND created_at<?',(gid,uid,now-5))
        con.execute('INSERT INTO spam_messages VALUES(?,?,?)',(gid,uid,now))
        n=con.execute('SELECT COUNT(*) n FROM spam_messages WHERE chat_id=? AND user_id=?',(gid,uid)).fetchone()['n']
        if n>=5:
            con.execute('INSERT OR REPLACE INTO user_mutes(chat_id,user_id,until_at) VALUES(?,?,?)',(gid,uid,now+300))
            spam=True; should=False
        else:
            spam=False
            con.execute('INSERT OR IGNORE INTO groups_state(group_id) VALUES(?)',(gid,))
            row=con.execute('SELECT * FROM groups_state WHERE group_id=?',(gid,)).fetchone()
            count=row['messages']+1
            should=bool(row['enabled'] and count>=max(SPAWN_EVERY_MESSAGES,row['interval_seconds']//2))
            con.execute('UPDATE groups_state SET messages=? WHERE group_id=?',(0 if should else count,gid))
            active=con.execute('SELECT expires_at FROM active_spawns WHERE group_id=?',(gid,)).fetchone()
            if active and active['expires_at']>now: should=False
    if spam:
        try:
            from telegram import ChatPermissions
            await context.bot.restrict_chat_member(gid,uid,permissions=ChatPermissions(can_send_messages=False),until_date=now+300)
        except Exception: pass
    elif should:
        try: await send_spawn(context.application,gid)
        except Exception: pass

async def hunt(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message; uid=update.effective_user.id; guess=' '.join(context.args).casefold()
    with db() as con:
        row=con.execute('SELECT * FROM active_spawns WHERE group_id=? AND expires_at>?',(msg.chat_id,int(time.time()))).fetchone()
        if not row: return await msg.reply_text(NO_SPAWN)
        ch=get_character(row['character_id'],con)
        if not ch: return await msg.reply_text(NO_SPAWN)
        names=[ch['name'],*json.loads(ch['aliases'] or '[]')]
        tokens={w.casefold() for name in names for w in re.findall(r'[\w]+',name)}
        if not any(w in tokens for w in re.findall(r'[\w]+',guess)): return await msg.reply_text('❌ Wrong name. Try again!')
        add_to_collection(uid,ch['id'],update.effective_user.username or '',con)
        con.execute('DELETE FROM active_spawns WHERE group_id=?',(msg.chat_id,))
    await msg.reply_photo(ch['image'],caption=f"🎉 {update.effective_user.first_name} caught {ch['name']}!\nAnime: {ch['anime']} · Rarity: {ch['rarity']} · ID: {ch['id']}")

async def claim_daily(update:Update,kind):
    uid=update.effective_user.id; now=int(time.time()); message=update.effective_message
    column='last_waifu' if kind=='waifu' else 'last_marriage'; cooldown=86400
    with db() as con:
        state=user_state(uid,update.effective_user.username or '',update.effective_user.first_name or '',con)
        if state[column]+cooldown>now: return await message.reply_text(f'⏳ Try again in {remaining(state[column]+cooldown-now)}.')
        if kind=='marriage' and random.random()>.35:
            con.execute(f'UPDATE user_state SET {column}=? WHERE user_id=?',(now,uid))
            return await message.reply_text('💔 Your marriage proposal was rejected.')
        rarities=['Shiny','Ultimate'] if kind=='marriage' else ['Starter','Rising','Shiny','Ultimate','RoyalArc']
        weights=[1,1] if kind=='marriage' else [50,50,50,50,5]
        chars=list_characters(rarities,con)
        available=[r for r in rarities if any(c['rarity']==r for c in chars)]
        if not available: return await message.reply_text('No characters available; ask an uploader to add some.')
        rarity=random.choices(available,weights=[weights[rarities.index(r)] for r in available])[0]
        ch=random.choice([c for c in chars if c['rarity']==rarity])
        add_to_collection(uid,ch['id'],update.effective_user.username or '',con)
        con.execute(f'UPDATE user_state SET {column}=? WHERE user_id=?',(now,uid))
    await message.reply_photo(ch['image'],caption=f"🌸 {ch['name']} · {ch['anime']}\nRarity: {ch['rarity']} · ID: {ch['id']}")

async def waifu_cmd(update,context): await claim_daily(update,'waifu')
async def marriage_cmd(update,context): await claim_daily(update,'marriage')

async def propose_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; now=int(time.time())
    with db() as con:
        state=user_state(uid,update.effective_user.username or '',update.effective_user.first_name or '',con)
        if state['last_propose']+300>now: return await update.message.reply_text(f'Cooldown: {remaining(state["last_propose"]+300-now)}')
        cash,_=get_balance(uid,con)
        if cash<30000: return await update.message.reply_text('You need 30,000 cash to propose.')
        adjust_balance(uid,cash=-30000,con=con)
        con.execute('UPDATE user_state SET last_propose=? WHERE user_id=?',(now,uid))
    await update.message.reply_text('💍 You made a proposal! The result is being decided...')
    await __import__('asyncio').sleep(random.randint(3,5))
    if random.random()<.4:
        choices=[c for c in list_characters() if c['rarity'] in ('ChristmasArc','RoyalArc','NeonArc','CelestialArc')]
        if choices:
            ch=random.choice(choices)
            with db() as con: add_to_collection(uid,ch['id'],update.effective_user.username or '',con)
            await update.effective_message.reply_photo(ch['image'],caption=f"💖 {ch['name']} accepted! · {ch['anime']} · {ch['rarity']}")
        else: await update.effective_message.reply_text('She accepted, but there are no eligible characters uploaded.')
    else: await update.effective_message.reply_text('🥲 You were friend-zoned.')

async def spawn_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if len(context.args)!=2 or context.args[1].lower() not in ('on','off'): return await update.message.reply_text('Use /spawn <rarity> <on|off>.')
    rarity=rarity_name(context.args[0])
    if not rarity: return await update.message.reply_text('Unknown rarity.')
    with db() as con: con.execute('INSERT INTO rarity_switch(rarity,enabled) VALUES(?,?) ON CONFLICT(rarity) DO UPDATE SET enabled=excluded.enabled',(rarity,int(context.args[1].lower()=='on')))
    await update.message.reply_text(f'{rarity} spawning {context.args[1]}.')

async def changetime_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo') or update.effective_chat.type=='private': return
    try: minutes=int(context.args[0])
    except (ValueError,IndexError): return await update.message.reply_text('Use /changetime <2–120 minutes>.')
    if not 2<=minutes<=120: return await update.message.reply_text('Choose 2–120 minutes.')
    with db() as con: con.execute('INSERT INTO groups_state(group_id,messages,interval_seconds,enabled) VALUES(?,0,?,1) ON CONFLICT(group_id) DO UPDATE SET interval_seconds=excluded.interval_seconds,enabled=1',(update.effective_chat.id,minutes*60))
    await update.message.reply_text(f'Spawn time changed to {minutes} minutes.')

async def upload_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; msg=update.effective_message
    if not has_role(uid,'owner','uploader'): return
    reply=msg.reply_to_message
    image=reply.photo[-1].file_id if reply and reply.photo else None
    if not image and reply and reply.document and reply.document.mime_type and reply.document.mime_type.startswith('image/'): image=reply.document.file_id
    args=context.args
    if len(args)<3 or not image: return await msg.reply_text('Reply to an image: /upload <rarity> <anime> | <character name> [| aliases].')
    rarity=rarity_name(args[0])
    if not rarity: return await msg.reply_text('Unknown rarity.')
    parts=[' '.join(args[1:]).split('|')]
    fields=[x.strip() for x in parts[0]]
    if len(fields)<2: return await msg.reply_text('Include anime and character name separated by |.')
    anime,name=fields[:2]; aliases=[x.strip() for x in fields[2].split(',') if x.strip()] if len(fields)>2 else []
    cid=uuid.uuid4().hex[:8]
    ch={'id':cid,'name':name,'anime':anime,'rarity':rarity,'image':image,'price':price_for_rarity(rarity),'aliases':aliases,'uploader_id':uid}
    try: add_character(ch)
    except Exception as e: return await msg.reply_text(f'Upload failed: {e}')
    await msg.reply_photo(image,caption=char_info(ch),parse_mode='HTML')

async def dupload_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','uploader'): return
    if not context.args: return await update.message.reply_text('Use /dupload <character_id>.')
    cid=context.args[0]
    if not get_character(cid): return await update.message.reply_text('Character not found.')
    tx=uuid.uuid4().hex
    with db() as con: con.execute('INSERT INTO pending_actions(id,user_id,kind,payload,expires_at) VALUES(?,?,?,?,?)',(tx,update.effective_user.id,'dupload',json.dumps({'id':cid}),int(time.time())+60))
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('Confirm delete',callback_data=f'del:ok:{tx}'),InlineKeyboardButton('Cancel',callback_data=f'del:no:{tx}')]])
    await update.message.reply_text(f'Delete character {cid} from all collections?',reply_markup=kb)

async def delete_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); _,action,tx=q.data.split(':',2)
    with db() as con:
        row=con.execute('SELECT * FROM pending_actions WHERE id=? AND kind="dupload"',(tx,)).fetchone()
        if not row or row['user_id']!=q.from_user.id or row['expires_at']<int(time.time()): return await q.answer('Expired or not yours.',show_alert=True)
        cid=json.loads(row['payload'])['id']; con.execute('DELETE FROM pending_actions WHERE id=?',(tx,))
    if action=='no': return await q.edit_message_text('Deletion cancelled.')
    remove_character(cid); await q.edit_message_text('Character removed from catalogue and collections.')

def register_characters(app):
    app.add_handler(CommandHandler('hunt',hunt)); app.add_handler(CommandHandler('waifu',waifu_cmd))
    app.add_handler(CommandHandler('marriage',marriage_cmd)); app.add_handler(CommandHandler('marry',marriage_cmd))
    app.add_handler(CommandHandler('propose',propose_cmd)); app.add_handler(CommandHandler('spawn',spawn_cmd))
    app.add_handler(CommandHandler('changetime',changetime_cmd)); app.add_handler(CommandHandler('upload',upload_cmd))
    app.add_handler(CommandHandler('dupload',dupload_cmd)); app.add_handler(CallbackQueryHandler(delete_callback,pattern=r'^del:'))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND,group_message),group=-1)
