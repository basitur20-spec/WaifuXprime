from __future__ import annotations
import asyncio,json,random,re,time
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import CallbackQueryHandler,CommandHandler,ContextTypes,MessageHandler,filters
from ..config import BOT_DATA_JSON,OWNER_ID,RARITY_WEIGHTS,SPAWN_EVERY_MESSAGES,SPAWN_LIFETIME_SECONDS,UPLOAD_CHANNEL
from ..database import db,get_balance,adjust_balance,add_to_collection,get_character,list_characters,add_character,remove_character,user_state
from ..permissions import has_role,is_owner
from ..utils import RARITY_EMOJI,price_for_rarity,rarity_name,remaining,char_caption,char_info

NO_SPAWN='❌ No character has spawned yet. Please wait for the next spawn.'

def enabled_rarities(con):
    con.execute('CREATE TABLE IF NOT EXISTS rarity_switch(rarity TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1)')
    off={r['rarity'] for r in con.execute('SELECT rarity FROM rarity_switch WHERE enabled=0')}
    return [r for r in RARITY_WEIGHTS if r not in off]

def pick_character(rarities=None):
    chars=list_characters(rarities)
    if not chars: return None
    return random.choice(chars)

def rarity_pool_weights(chars,weights):
    buckets={r:[] for r in weights}
    for c in chars:
        if c['rarity'] in buckets: buckets[c['rarity']].append(c)
    rarities=[r for r,v in buckets.items() if v]
    if not rarities: return random.choice(chars)
    rarity=random.choices(rarities,weights=[weights[r] for r in rarities],k=1)[0]
    return random.choice(buckets[rarity])

async def send_spawn(application,group_id):
    now=int(time.time())
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS groups_state(group_id INTEGER PRIMARY KEY,messages INTEGER NOT NULL DEFAULT 0,interval_seconds INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1)')
        con.execute('CREATE TABLE IF NOT EXISTS active_spawns(group_id INTEGER PRIMARY KEY,character_id TEXT NOT NULL,expires_at INTEGER NOT NULL,message_id INTEGER NOT NULL DEFAULT 0)')
        active=con.execute('SELECT * FROM active_spawns WHERE group_id=?',(group_id,)).fetchone()
        if active and active['expires_at']>now: return
        if active: con.execute('DELETE FROM active_spawns WHERE group_id=?',(group_id,))
        eligible=enabled_rarities(con)
        chars=list_characters(eligible,con)
        if not chars: return
        ch=rarity_pool_weights(chars,{r:RARITY_WEIGHTS[r] for r in eligible})
        con.execute('INSERT OR REPLACE INTO active_spawns(group_id,character_id,expires_at,message_id) VALUES(?,?,?,0)',(group_id,str(ch['id']),now+SPAWN_LIFETIME_SECONDS))
    cap='✨ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀꜱ ᴀᴘᴘᴇᴀʀᴇᴅ! ✨\nᴜꜱᴇ /hunt (ɴᴀᴍᴇ) ᴛᴏ ᴀᴅᴅ ɪᴛ ɪɴ ʏᴏᴜʀ COLLECTION.'
    try:
        msg=await application.bot.send_photo(group_id,ch['image'],caption=cap,has_spoiler=True)
        with db() as con: con.execute('UPDATE active_spawns SET message_id=? WHERE group_id=?',(msg.message_id,group_id))
        application.job_queue.run_once(expire_spawn,SPAWN_LIFETIME_SECONDS,data={'group_id':group_id,'character_id':str(ch['id'])},name=f'expire:{group_id}')
    except Exception:
        with db() as con: con.execute('DELETE FROM active_spawns WHERE group_id=?',(group_id,))
        raise

async def expire_spawn(context:ContextTypes.DEFAULT_TYPE):
    data=context.job.data; gid=int(data['group_id']); cid=str(data['character_id'])
    with db() as con:
        row=con.execute('SELECT * FROM active_spawns WHERE group_id=? AND character_id=?',(gid,cid)).fetchone()
        if not row: return
        con.execute('DELETE FROM active_spawns WHERE group_id=?',(gid,))
    ch=get_character(cid)
    if ch:
        try: await context.bot.send_message(gid,f"❌ Too slow! The character escaped!
🌸 Name: {ch['name']}
🎬 Anime: {ch['anime']}
✨ Rarity: {RARITY_EMOJI.get(ch['rarity'],'')} {ch['rarity']}
🆔 ID: {cid}")
        except Exception: pass

async def group_message(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message; user=update.effective_user
    if not msg or not user or user.is_bot or msg.chat.type not in ('group','supergroup'): return
    now=int(time.time()); uid=user.id; gid=msg.chat_id
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS spam_messages(chat_id INTEGER NOT NULL,user_id INTEGER NOT NULL,created_at INTEGER NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS user_mutes(chat_id INTEGER NOT NULL,user_id INTEGER NOT NULL,until_at INTEGER NOT NULL,PRIMARY KEY(chat_id,user_id))')
        muted=con.execute('SELECT until_at FROM user_mutes WHERE chat_id=? AND user_id=?',(gid,uid)).fetchone()
        if muted and muted['until_at']>now: return
        con.execute('DELETE FROM spam_messages WHERE chat_id=? AND user_id=? AND created_at<?',(gid,uid,now-5))
        con.execute('INSERT INTO spam_messages(chat_id,user_id,created_at) VALUES(?,?,?)',(gid,uid,now))
        count=con.execute('SELECT COUNT(*) n FROM spam_messages WHERE chat_id=? AND user_id=?',(gid,uid)).fetchone()['n']
        if count>=5:
            con.execute('INSERT OR REPLACE INTO user_mutes(chat_id,user_id,until_at) VALUES(?,?,?)',(gid,uid,now+300))
            spam=True
        else: spam=False
        con.execute('CREATE TABLE IF NOT EXISTS groups_state(group_id INTEGER PRIMARY KEY,messages INTEGER NOT NULL DEFAULT 0,interval_seconds INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1)')
        con.execute('INSERT OR IGNORE INTO groups_state(group_id,messages,interval_seconds,enabled) VALUES(?,0,0,1)',(gid,))
        state=con.execute('SELECT * FROM groups_state WHERE group_id=?',(gid,)).fetchone()
        if not spam:
            n=state['messages']+1; con.execute('UPDATE groups_state SET messages=? WHERE group_id=?',(n,gid))
            active=con.execute('SELECT expires_at FROM active_spawns WHERE group_id=?',(gid,)).fetchone()
            should=state['enabled'] and n>=max(SPAWN_EVERY_MESSAGES,state['interval_seconds']//2) and (not active or active['expires_at']<=now)
            if should: con.execute('UPDATE groups_state SET messages=0 WHERE group_id=?',(gid,))
        else: should=False
    if spam:
        try:
            await context.bot.restrict_chat_member(gid,uid,permissions=__import__('telegram').ChatPermissions(can_send_messages=False),until_date=now+300)
            await msg.reply_text(f'{user.first_name}, your messages will not be counted for 5 minutes.\nYou cannot spam to get characters.')
        except Exception: pass
        return
    if should:
        try: await send_spawn(context.application,gid)
        except Exception: pass

async def hunt(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.message; gid=msg.chat_id; uid=update.effective_user.id; guess=' '.join(context.args).strip().casefold()
    now=int(time.time())
    with db() as con:
        row=con.execute('SELECT * FROM active_spawns WHERE group_id=?',(gid,)).fetchone()
        if not row or row['expires_at']<=now:
            if row: con.execute('DELETE FROM active_spawns WHERE group_id=?',(gid,))
            await msg.reply_text(NO_SPAWN); return
        ch=get_character(row['character_id'],con)
        if not ch: con.execute('DELETE FROM active_spawns WHERE group_id=?',(gid,)); await msg.reply_text(NO_SPAWN); return
        aliases=json.loads(ch['aliases'] or '[]')
        names=[ch['name'],*aliases]
        tokens={w.casefold() for name in names for w in re.findall(r'[\w]+',name) if len(w)>0}
        if not guess or not any(w in tokens for w in re.findall(r'[\w]+',guess)):
            await msg.reply_text(NO_SPAWN); return
        add_to_collection(uid,ch['id'],update.effective_user.username or '',con)
        con.execute('DELETE FROM active_spawns WHERE group_id=?',(gid,))
    await msg.reply_photo(ch['image'],caption=f"🎉 Congratulations {update.effective_user.first_name}!
You got a new waifu!
🌸 Name: {ch['name']}
🎬 Anime: {ch['anime']}
✨ Rarity: {RARITY_EMOJI.get(ch['rarity'],'')} {ch['rarity']}
🆔 ID: {ch['id']}
Check /collection now.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('COLLECTION 🔥',callback_data=f'col:p:{uid}:0')]]))

async def claim_random(update,context,kind):
    uid=update.effective_user.id; now=int(time.time()); message=update.effective_message
    with db() as con:
        state=user_state(uid,update.effective_user.username or '',update.effective_user.first_name or '',con)
        column='last_waifu' if kind=='waifu' else 'last_marriage'; cooldown=43200 if kind=='waifu' else 3600
        if state[column]+cooldown>now:
            await message.reply_text(f'⏳ Please wait {remaining(state[column]+cooldown-now)} before trying /{kind} again.'); return
        if kind=='marriage' and random.random()>0.35:
            con.execute(f'UPDATE user_state SET {column}=? WHERE user_id=?',(now,uid))
            await message.reply_text('Your marriage proposal was rejected and she ran away! 🤡'); return
        rarities=['Shiny','Ultimate'] if kind=='marriage' else ['Starter','Rising','Shiny','Ultimate','RoyalArc']
        weights=[1,1] if kind=='marriage' else [50,50,50,50,5]
        chars=list_characters(rarities,con)
        buckets={r:[c for c in chars if c['rarity']==r] for r in rarities}; available=[r for r in rarities if buckets[r]]
        if not available:
            await message.reply_text('No waifus are available yet. Ask an uploader to add characters.'); return
        rarity=random.choices(available,weights=[weights[rarities.index(r)] for r in available],k=1)[0]; ch=random.choice(buckets[rarity])
        add_to_collection(uid,ch['id'],update.effective_user.username or '',con)
        con.execute(f'UPDATE user_state SET {column}=? WHERE user_id=?',(now,uid))
    await message.reply_photo(ch['image'],caption=f"Congratulations {update.effective_user.first_name}! You won!
🌸 Name: {ch['name']}
✨ Rarity: {RARITY_EMOJI.get(ch['rarity'],'')} {ch['rarity']}
🎬 Anime: {ch['anime']}
🆔 ID: {ch['id']}")

async def waifu_cmd(update,context): await claim_random(update,context,'waifu')
async def marriage_cmd(update,context): await claim_random(update,context,'marriage')

async def propose_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; now=int(time.time())
    with db() as con:
        state=user_state(uid,update.effective_user.username or '',update.effective_user.first_name or '',con)
        if state['last_propose']+300>now:
            await update.message.reply_text(f'Cooldown! Wait {remaining(state["last_propose"]+300-now)} before proposing again.'); return
        cash,_=get_balance(uid,con)
        if cash<30000: await update.message.reply_text('YOU NEED 30,000 CASH💸 TO PROPOSE.'); return
        adjust_balance(uid,-30000,0,con)
        con.execute('UPDATE user_state SET last_propose=? WHERE user_id=?',(now,uid))
    intro=await update.message.reply_photo('https://t.me/movieandweb_s_group/17959',caption=f"{update.effective_user.first_name} 💫 The moment you've been waiting for 💫")
    process=await intro.reply_text('Proposing her... 💍')
    await asyncio.sleep(random.randint(3,5))
    accepted=random.random()<random.uniform(.35,.45)
    if accepted:
        rarities=['ChristmasArc','RoyalArc','NeonArc','CelestialArc']; weights=[35,60,35,55]
        chars=list_characters(rarities); available=[r for r in rarities if any(c['rarity']==r for c in chars)]
        if available:
            rar=random.choices(available,weights=[weights[rarities.index(r)] for r in available],k=1)[0]; ch=random.choice([c for c in chars if c['rarity']==rar])
            with db() as con: add_to_collection(uid,ch['id'],update.effective_user.username or '',con)
            await context.bot.send_photo(update.effective_chat.id,ch['image'],caption=f"{ch['name']} has accepted your proposal! 😇
🌸 Name: {ch['name']}
✨ Rarity: {ch['rarity']}
🎬 Anime: {ch['anime']}",reply_to_message_id=process.message_id)
        else: await process.reply_text('She accepted, but no eligible waifus are uploaded yet.')
    else:
        url=random.choice(['https://t.me/movieandweb_s_group/18022','https://t.me/movieandweb_s_group/17925'])
        await context.bot.send_photo(update.effective_chat.id,url,caption='You have been friend-zoned! 🥲',reply_to_message_id=process.message_id)

async def spawn_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_owner(uid): return
    if len(context.args)!=2 or context.args[1].lower() not in ('on','off'):
        await update.message.reply_text('Usage: /spawn <rarity> <on|off>'); return
    rarity=rarity_name(context.args[0])
    if not rarity: await update.message.reply_text('Unknown rarity.'); return
    with db() as con:
        con.execute('INSERT INTO rarity_switch(rarity,enabled) VALUES(?,?) ON CONFLICT(rarity) DO UPDATE SET enabled=excluded.enabled',(rarity,int(context.args[1].lower()=='on')))
    await update.message.reply_text(f'{RARITY_EMOJI[rarity]} {rarity} spawning {context.args[1].upper()}.')

async def changetime_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','sudo'): return
    if update.effective_chat.type=='private': return
    try: minutes=int(context.args[0])
    except (ValueError,IndexError): await update.message.reply_text('Use /changetime <2-120 minutes>.'); return
    if not 2<=minutes<=120: await update.message.reply_text('Spawn interval must be 2 to 120 minutes.'); return
    with db() as con:
        con.execute('INSERT INTO groups_state(group_id,messages,interval_seconds,enabled) VALUES(?,0,?,1) ON CONFLICT(group_id) DO UPDATE SET interval_seconds=excluded.interval_seconds,enabled=1',(update.effective_chat.id,minutes*60))
    await update.message.reply_text(f'⏱️ Spawn interval set to every {minutes} minutes for this group.')

async def upload_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not has_role(uid,'owner','uploader'): return
    msg=update.effective_message; image=None; spec=''
    if msg.photo:
        image=msg.photo[-1].file_id; spec=msg.caption or ' '.join(context.args)
    elif msg.reply_to_message and msg.reply_to_message.photo:
        image=msg.reply_to_message.photo[-1].file_id; spec=msg.text or ' '.join(context.args)
    if not image:
        await msg.reply_text('Send or reply to a photo with /upload and Name, Rarity, Anime, and optional Price/ID.'); return
    fields={}
    for line in spec.replace('/upload','').splitlines():
        if ':' in line:
            k,v=line.split(':',1); fields[k.strip().lower()]=v.strip()
    if not fields and spec:
        parts=[x.strip() for x in spec.replace('/upload','').split(',')]
        if len(parts)>=3: fields={'name':parts[0],'rarity':parts[1],'anime':parts[2]}; fields['price']=parts[3] if len(parts)>3 else ''
    name=fields.get('name'); anime=fields.get('anime'); rarity=rarity_name(fields.get('rarity',''))
    if not name or not anime or not rarity:
        await msg.reply_text('Missing/invalid fields. Use Name: ..., Rarity: ..., Anime: ..., Price: ...'); return
    low,high=__import__('waifux.config',fromlist=['RARITY_PRICES']).RARITY_PRICES[rarity]
    try: price=int(fields.get('price') or price_for_rarity(rarity))
    except ValueError: await msg.reply_text('Price must be a number.'); return
    if not low<=price<=high: await msg.reply_text(f'Price for {rarity} must be between {low:,} and {high:,}.'); return
    with db() as con:
        cid=fields.get('id')
        if not cid:
            used={int(r['id']) for r in con.execute('SELECT id FROM characters WHERE id GLOB "[0-9]*"') if str(r['id']).isdigit()}
            cid=next((f'{i:04d}' for i in range(1,10000) if i not in used),None)
        if not cid or get_character(cid,con): await msg.reply_text('That ID is already used or no IDs are available.'); return
        add_character({'id':cid,'name':name,'anime':anime,'rarity':rarity,'image':image,'price':price,'uploader_id':uid},con=None)
        reward=int(price*.03); adjust_balance(uid,reward,0,con)
    await msg.reply_photo(image,caption=char_info({'id':cid,'name':name,'anime':anime,'rarity':rarity,'price':price})+f"
🎉 Uploaded successfully! Uploader reward: {reward:,} Cash.",parse_mode='HTML')
    if UPLOAD_CHANNEL:
        try: await context.bot.send_photo(UPLOAD_CHANNEL,image,caption=char_caption({'id':cid,'name':name,'anime':anime,'rarity':rarity,'price':price}),parse_mode='HTML')
        except Exception: pass

async def dupload_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not has_role(update.effective_user.id,'owner','uploader'): return
    if not context.args: await update.message.reply_text('Use /dupload <character_id>.'); return
    cid=context.args[0]; ch=get_character(cid)
    if not ch: await update.message.reply_text('Waifu not found in bot database.'); return
    import uuid
    tx=uuid.uuid4().hex
    with db() as con: con.execute('INSERT INTO pending_actions(id,user_id,kind,payload,expires_at) VALUES(?,?,?,?,?)',(tx,update.effective_user.id,'dupload',json.dumps({'id':cid}),int(time.time())+60))
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirm Delete',callback_data=f'del:ok:{tx}'),InlineKeyboardButton('❌ Cancel',callback_data=f'del:no:{tx}')]])
    await update.message.reply_photo(ch['image'],caption=f"Delete {ch['name']} ({cid}) from all collections?",reply_markup=kb)

async def delete_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); _,action,tx=q.data.split(':',2)
    with db() as con:
        row=con.execute('SELECT * FROM pending_actions WHERE id=? AND kind="dupload"',(tx,)).fetchone()
        if not row or row['user_id']!=q.from_user.id: await q.answer('Request expired or not yours.',show_alert=True); return
        payload=json.loads(row['payload']); con.execute('DELETE FROM pending_actions WHERE id=?',(tx,))
    if action=='no': await q.edit_message_caption('Deletion cancelled. No data changed.'); return
    remove_character(payload['id']); await q.edit_message_caption('Waifu removed from the character catalogue and user collections.')

def register_characters(app):
    app.add_handler(CommandHandler('hunt',hunt)); app.add_handler(CommandHandler('waifu',waifu_cmd)); app.add_handler(CommandHandler('marriage',marriage_cmd)); app.add_handler(CommandHandler('marry',marriage_cmd)); app.add_handler(CommandHandler('propose',propose_cmd))
    app.add_handler(CommandHandler('spawn',spawn_cmd)); app.add_handler(CommandHandler('changetime',changetime_cmd)); app.add_handler(CommandHandler('upload',upload_cmd)); app.add_handler(CommandHandler('dupload',dupload_cmd))
    app.add_handler(CallbackQueryHandler(delete_callback,pattern=r'^del:'))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND,group_message),group=-1)
