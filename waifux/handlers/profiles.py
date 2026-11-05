from __future__ import annotations
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from ..database import db, collection_rows, user_state
from ..utils import username

async def setprofile(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        fid=update.message.reply_to_message.photo[-1].file_id
        with db() as con:
            user_state(u.id,u.username or '',u.first_name or '',con)
            con.execute('UPDATE user_state SET profile_photo=? WHERE user_id=?',(fid,u.id))
        return await update.message.reply_text('Profile photo updated.')
    if not context.args: return await update.message.reply_text('Reply to a photo and use /setprofile, or /setprofile waifu <id>.')
    if context.args[0].lower()=='waifu' and len(context.args)>1:
        with db() as con:
            own=con.execute('SELECT 1 FROM collection.collections WHERE user_id=? AND waifu_id=?',(u.id,context.args[1])).fetchone()
            if not own: return await update.message.reply_text('You do not own that waifu.')
            con.execute('UPDATE user_state SET profile_waifu=? WHERE user_id=?',(context.args[1],u.id))
        return await update.message.reply_text('Profile waifu updated.')
    await update.message.reply_text('Send a photo and reply with /setprofile to set your profile picture.')

async def profile(update:Update,context:ContextTypes.DEFAULT_TYPE):
    user=update.effective_user
    target=update.message.reply_to_message.from_user if update.message.reply_to_message and update.message.reply_to_message.from_user else user
    with db() as con:
        state=user_state(target.id,target.username or '',target.first_name or '',con)
        total=con.execute('SELECT COUNT(*) n FROM collection.collections WHERE user_id=?',(target.id,)).fetchone()['n']
        cash,dia=__import__('waifux.database',fromlist=['get_balance']).get_balance(target.id,con)
    text=f'👤 <b>{username(target)}</b>\n🎴 Waifus: {total}\n💰 Cash: {cash:,}\n💎 Diamonds: {dia:,}'
    if state['profile_waifu']: text+=f'\n🌸 Featured: {state["profile_waifu"]}'
    await update.message.reply_text(text,parse_mode='HTML')

async def photos(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if not update.message.reply_to_message or not update.message.reply_to_message.photo: return await update.message.reply_text('Reply to a photo with /setprofile.')
    fid=update.message.reply_to_message.photo[-1].file_id
    with db() as con:
        user_state(u.id,u.username or '',u.first_name or '',con)
        con.execute('UPDATE user_state SET profile_photo=? WHERE user_id=?',(fid,u.id))
    await update.message.reply_text('Profile photo updated.')

def register_profiles(app):
    app.add_handler(CommandHandler('profile',profile)); app.add_handler(CommandHandler('setprofile',setprofile))
    app.add_handler(CommandHandler('setprofilephoto',photos))
