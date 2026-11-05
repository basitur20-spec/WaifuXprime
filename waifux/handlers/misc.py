from __future__ import annotations
import time,uuid
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,Update
from telegram.ext import CommandHandler,MessageHandler,filters,CallbackQueryHandler,ContextTypes
from ..database import db,adjust_balance,get_balance,collection_rows,add_to_collection,remove_from_collection,audit
from ..utils import char_info

async def find_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text('Use /find <name, anime, rarity, or ID>.')
    q=' '.join(context.args).casefold()
    with db() as con:
        rows=con.execute('SELECT * FROM characters WHERE lower(name) LIKE ? OR lower(anime) LIKE ? OR lower(rarity) LIKE ? OR id=? LIMIT 10',(f'%{q}%',f'%{q}%',f'%{q}%',q)).fetchall()
    if not rows: return await update.message.reply_text('No matching characters.')
    for ch in rows:
        try: await update.message.reply_photo(ch['image'],caption=char_info(ch),parse_mode='HTML')
        except Exception: await update.message.reply_text(char_info(ch),parse_mode='HTML')

async def gift_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message
    target=msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None
    args=context.args
    if not target or not args: return await msg.reply_text('Reply to a user and use /gift <waifu_id>.')
    cid=args[0]; uid=update.effective_user.id
    with db() as con:
        if not con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=?',(uid,cid)).fetchone(): return await msg.reply_text('You do not own that waifu.')
        if not remove_from_collection(uid,cid,con): return await msg.reply_text('Could not transfer this waifu.')
        add_to_collection(target.id,cid,target.username or '',con)
        con.execute('INSERT INTO gifts(giver,receiver,character_id,created_at) VALUES(?,?,?,?)',(uid,target.id,cid,int(time.time())))
    await msg.reply_text('🎁 Gift sent!')

async def trade_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message; target=msg.reply_to_message.from_user if msg.reply_to_message and msg.reply_to_message.from_user else None
    if not target or len(context.args)<2: return await msg.reply_text('Reply to a user: /trade <your_waifu_id> <wanted_waifu_id>.')
    uid=update.effective_user.id; offered,wanted=context.args[:2]; tid=uuid.uuid4().hex[:12]
    with db() as con:
        if not con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=?',(uid,offered)).fetchone(): return await msg.reply_text('You do not own the offered waifu.')
        con.execute('INSERT INTO trades(id,initiator,partner,offered_id,wanted_id,expires_at) VALUES(?,?,?,?,?,?)',(tid,uid,target.id,offered,wanted,int(time.time())+300))
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('Accept',callback_data=f'trade:yes:{tid}'),InlineKeyboardButton('Decline',callback_data=f'trade:no:{tid}')]])
    await msg.reply_text(f'Trade offer: {offered} for {wanted}. Expires in 5 minutes.',reply_markup=kb)

async def trade_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; _,action,tid=q.data.split(':',2)
    with db() as con:
        t=con.execute('SELECT * FROM trades WHERE id=?',(tid,)).fetchone()
        if not t or t['partner']!=q.from_user.id or t['expires_at']<time.time():
            await q.answer('Trade expired or not yours.',show_alert=True); return
        if action=='yes':
            if not con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=?',(t['partner'],t['wanted_id'])).fetchone() or not con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=?',(t['initiator'],t['offered_id'])).fetchone():
                con.execute('DELETE FROM trades WHERE id=?',(tid,)); await q.answer('An item is no longer available.',show_alert=True); return
            remove_from_collection(t['initiator'],t['offered_id'],con); remove_from_collection(t['partner'],t['wanted_id'],con)
            add_to_collection(t['partner'],t['offered_id'],'',con); add_to_collection(t['initiator'],t['wanted_id'],'',con)
        con.execute('DELETE FROM trades WHERE id=?',(tid,))
    await q.answer('Trade completed.' if action=='yes' else 'Trade declined.')
    await q.edit_message_text('✅ Trade completed.' if action=='yes' else 'Trade declined.')

async def claim_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    with db() as con:
        event=con.execute('SELECT * FROM claim_events WHERE active=1 ORDER BY id DESC LIMIT 1').fetchone()
        if not event: return await update.message.reply_text('No active claim event.')
        if con.execute('SELECT 1 FROM user_claims WHERE user_id=? AND event_id=?',(uid,event['id'])).fetchone(): return await update.message.reply_text('Already claimed.')
        try:
            if event['reward_type']=='cash': adjust_balance(uid,cash=int(event['reward_value']),con=con)
            elif event['reward_type']=='diamonds': adjust_balance(uid,diamonds=int(event['reward_value']),con=con)
            elif event['reward_type']=='waifu': add_to_collection(uid,event['reward_value'],update.effective_user.username or '',con)
            else: return await update.message.reply_text('Unknown reward type.')
        except (ValueError,TypeError): return await update.message.reply_text('Reward is unavailable.')
        con.execute('INSERT INTO user_claims(user_id,event_id,claimed_at) VALUES(?,?,?)',(uid,event['id'],int(time.time())))
    await update.message.reply_text('🎉 Reward claimed!')

async def filter_set(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type=='private': return
    if len(context.args)<2: return await update.message.reply_text('Use /filter <word> <reply text>.')
    name=context.args[0].casefold(); response=' '.join(context.args[1:])
    with db() as con: con.execute('INSERT OR REPLACE INTO filters(chat_id,name,response) VALUES(?,?,?)',(update.effective_chat.id,name,response))
    await update.message.reply_text('Filter saved.')

async def filter_del(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text('Use /stopfilter <word>.')
    with db() as con: con.execute('DELETE FROM filters WHERE chat_id=? AND name=?',(update.effective_chat.id,context.args[0].casefold()))
    await update.message.reply_text('Filter removed.')

async def auto_filter(update:Update,context:ContextTypes.DEFAULT_TYPE):
    text=(update.effective_message.text or '').casefold()
    with db() as con: rows=con.execute('SELECT name,response FROM filters WHERE chat_id=?',(update.effective_chat.id,)).fetchall()
    for r in rows:
        if r['name'] in text:
            await update.effective_message.reply_text(r['response']); break

async def cooldowns(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not update.effective_user: return
    with db() as con:
        s=con.execute('SELECT * FROM user_state WHERE user_id=?',(update.effective_user.id,)).fetchone()
    await update.message.reply_text('Use /hunt in a group to catch a spawned character. Daily waifu and marriage limits reset each day.')

def register_misc(app):
    for cmd,fn in [('find',find_cmd),('dfind',find_cmd),('gift',gift_cmd),('trade',trade_cmd),('claim',claim_cmd),('gclaim',claim_cmd),('filter',filter_set),('stopfilter',filter_del),('cooldown',cooldowns)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(trade_callback,pattern=r'^trade:'))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,auto_filter),group=2)
