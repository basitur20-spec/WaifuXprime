from __future__ import annotations
import json
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,InlineQueryResultPhoto,Update
from telegram.ext import CallbackQueryHandler,CommandHandler,ContextTypes,InlineQueryHandler
from ..database import db,collection_rows,get_character,set_favorite,user_state
from ..utils import RARITY_EMOJI

PAGE=10

def _filtered(rows,mode,value):
    if mode=='rarity': return [r for r in rows if (r['rarity'] or '').casefold()==value.casefold()]
    if mode=='anime': return [r for r in rows if (r['anime'] or '').casefold()==value.casefold()]
    if mode=='character': return [r for r in rows if (r['name'] or '').casefold()==value.casefold()]
    return rows

def _collection_text(rows,owner,page,mode,value,viewer):
    start=page*PAGE; chunk=rows[start:start+PAGE]
    title='YOUR WAIFU COLLECTION' if owner==viewer else f"{owner}'S WAIFU COLLECTION · VIEW ONLY"
    lines=[f'📚 <b>{title}</b>',f'Page {page+1}/{max(1,(len(rows)+PAGE-1)//PAGE)}']
    if mode!='default': lines.append(f'Filter: {mode.title()} · {value}')
    if not chunk: lines.append('\nNo waifus yet.')
    current=None
    for row in chunk:
        anime=row['anime'] or 'Unknown anime'
        if anime!=current: lines.extend(['',f'🎬 <b>{anime}</b>']); current=anime
        fav='❤️ ' if row['favorite'] else ''
        lines.append(f"{fav}🆔 <code>{row['waifu_id']}</code> | {RARITY_EMOJI.get(row['rarity'],'⚪')} | {row['name'] or 'Unknown'}")
    return '\n'.join(lines)

def _keyboard(owner,page,total,mode,value,viewer):
    rows=[]
    if owner==viewer:
        nav=[]
        if page>0: nav.append(InlineKeyboardButton('◀ Prev',callback_data=f'col:p:{owner}:{page-1}'))
        if page+1<total: nav.append(InlineKeyboardButton('Next ▶',callback_data=f'col:p:{owner}:{page+1}'))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton('✨ Waifus',switch_inline_query_current_chat='')])
        rows.append([InlineKeyboardButton('❌ Close',callback_data=f'col:x:{owner}:{page}')])
    else:
        rows.append([InlineKeyboardButton('✨ Waifus',switch_inline_query_current_chat='')])
    return InlineKeyboardMarkup(rows)

async def show_collection(update,context,owner=None,page=0,mode=None,value=None):
    viewer=update.effective_user.id
    owner=owner or viewer
    state=user_state(owner)
    mode=mode or state['dmode']; value=value if value is not None else state['dmode_value']
    rows=_filtered(collection_rows(owner),mode,value)
    page=max(0,min(page,max(0,(len(rows)-1)//PAGE)))
    total=max(1,(len(rows)+PAGE-1)//PAGE)
    fav=next((r for r in rows if r['favorite']),None) if owner==viewer else None
    if fav and fav['image']:
        await update.effective_message.reply_photo(fav['image'],caption='❤️ Your favorite waifu',protect_content=False)
    await update.effective_message.reply_text(_collection_text(rows,owner,page,mode,value,viewer),parse_mode='HTML',reply_markup=_keyboard(owner,page,total,mode,value,viewer))

async def collection_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    owner=update.effective_user.id
    if context.args:
        target=context.args[0].lstrip('@')
        with db() as con:
            found=con.execute('SELECT user_id FROM user_state WHERE username=? OR CAST(user_id AS TEXT)=?',(target,target)).fetchone()
        if not found:
            await update.message.reply_text('User not found.'); return
        owner=int(found['user_id'])
    await show_collection(update,context,owner)

async def collection_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _,action,owner,page=q.data.split(':'); owner=int(owner); page=int(page)
    if action=='x':
        if q.from_user.id==owner: await q.message.delete()
        else: await q.answer('View-only collection.',show_alert=True)
        return
    if q.from_user.id!=owner:
        await q.answer('This collection is view-only.',show_alert=True); return
    state=user_state(owner); rows=_filtered(collection_rows(owner),state['dmode'],state['dmode_value'])
    total=max(1,(len(rows)+PAGE-1)//PAGE); page=max(0,min(page,total-1))
    await q.edit_message_text(_collection_text(rows,owner,page,state['dmode'],state['dmode_value'],owner),parse_mode='HTML',reply_markup=_keyboard(owner,page,total,state['dmode'],state['dmode_value'],owner))

async def fav_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Use /fav <waifu_id>.'); return
    cid=context.args[0]; row=next((r for r in collection_rows(update.effective_user.id) if str(r['waifu_id'])==cid),None)
    if not row:
        await update.message.reply_text('You can favorite only a waifu in your collection.'); return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('INLINE',switch_inline_query_current_chat=row['name'] or ''),InlineKeyboardButton('COLLECTION',callback_data=f'fav:c:{update.effective_user.id}:{cid}'),InlineKeyboardButton('CANCEL',callback_data=f'fav:x:{update.effective_user.id}:{cid}')]])
    if row['image']: await update.message.reply_photo(row['image'],caption=f"Do you want to favorite {row['name']}?",reply_markup=kb)
    else: await update.message.reply_text(f"Do you want to favorite {row['name']}?",reply_markup=kb)

async def fav_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); _,action,uid,cid=q.data.split(':')
    if q.from_user.id!=int(uid): await q.answer('This request belongs to another user.',show_alert=True); return
    if action=='x': await q.edit_message_caption('Favorite cancelled.') if q.message.caption is not None else await q.edit_message_text('Favorite cancelled.'); return
    if not set_favorite(uid,cid): await q.answer('Waifu no longer in your collection.',show_alert=True); return
    await q.edit_message_caption('❤️ Favorite saved. Opening your collection.') if q.message.caption is not None else await q.edit_message_text('❤️ Favorite saved.')
    await show_collection(update,context,int(uid))

async def dmode_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    rarities=list(RARITY_EMOJI)
    buttons=[[InlineKeyboardButton('DEFAULT',callback_data='dm:default:'),InlineKeyboardButton('CHARACTERS',callback_data='dm:character:')],[InlineKeyboardButton('RARITY',callback_data='dm:rarity:') ,InlineKeyboardButton('ANIME',callback_data='dm:anime:')]]
    for i in range(0,len(rarities),4): buttons.append([InlineKeyboardButton(RARITY_EMOJI[r]+' '+r,callback_data='dm:setrarity:'+r) for r in rarities[i:i+4]])
    with db() as con:
        animes=[r['anime'] for r in con.execute('SELECT DISTINCT anime FROM characters ORDER BY anime LIMIT 20')]
    if animes: buttons.extend([[InlineKeyboardButton(a[:30],callback_data='dm:setanime:'+a)] for a in animes])
    await update.message.reply_text('Choose your collection display mode.',reply_markup=InlineKeyboardMarkup(buttons))

async def dmode_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); parts=q.data.split(':',2); mode=parts[1]; value=parts[2] if len(parts)>2 else ''
    if q.from_user.id!=q.message.chat_id and q.message.chat.type=='private': await q.answer('Only the requester can change this mode.',show_alert=True); return
    if mode in ('default','character','rarity','anime'):
        with db() as con: con.execute('UPDATE user_state SET dmode=?,dmode_value=? WHERE user_id=?',(mode,value,q.from_user.id))
        await q.edit_message_text(f'Collection mode set to {mode}.'+(f' · {value}' if value else ''))
        await show_collection(update,context,q.from_user.id)
    elif mode.startswith('set'):
        field=mode[3:].lower(); val=value
        actual='rarity' if field=='rarity' else 'anime'
        with db() as con: con.execute('UPDATE user_state SET dmode=?,dmode_value=? WHERE user_id=?',(actual,val,q.from_user.id))
        await q.edit_message_text(f'Collection filtered by {val}.')
        await show_collection(update,context,q.from_user.id)

async def inline_query(update:Update,context:ContextTypes.DEFAULT_TYPE):
    query=update.inline_query; text=query.query.casefold(); data=[]
    for row in collection_rows(query.from_user.id):
        if text and text not in (row['name'] or '').casefold(): continue
        if row['image']: data.append(InlineQueryResultPhoto(id=f"{row['waifu_id']}-{row['item_rowid']}",photo_url=row['image'],thumbnail_url=row['image'],caption=f"{row['name']} · {row['rarity']} · {row['anime']}"))
        if len(data)>=50: break
    await query.answer(data,cache_time=1,is_personal=True)

def register_collection(app):
    app.add_handler(CommandHandler('collection',collection_cmd))
    app.add_handler(CommandHandler('fav',fav_cmd))
    app.add_handler(CommandHandler('fave',fav_cmd))
    app.add_handler(CommandHandler('dmode',dmode_cmd))
    app.add_handler(CallbackQueryHandler(collection_callback,pattern=r'^col:'))
    app.add_handler(CallbackQueryHandler(fav_callback,pattern=r'^fav:'))
    app.add_handler(CallbackQueryHandler(dmode_callback,pattern=r'^dm:'))
    app.add_handler(InlineQueryHandler(inline_query))
