from __future__ import annotations
import html, random, re, time
from datetime import datetime, timezone
from .config import RARITY_EMOJI, RARITY_PRICES

def norm(value): return re.sub(r'\s+',' ',str(value or '')).strip().casefold()
def rarity_name(value):
    key=norm(value).replace(' ','')
    for r in RARITY_EMOJI:
        if norm(r).replace(' ','')==key: return r
    try: return list(RARITY_EMOJI)[int(value)-1]
    except (ValueError,IndexError,TypeError): return None

def fmt(n): return f'{int(n):,}'
def username(user): return '@'+user.username if getattr(user,'username',None) else html.escape(getattr(user,'full_name','User'))
def char_caption(ch):
    return f"🌸 <b>{html.escape(ch['name'])}</b>
🎬 Anime: {html.escape(ch['anime'])}
✨ Rarity: {RARITY_EMOJI.get(ch['rarity'],'')} {html.escape(ch['rarity'])}
🆔 ID: <code>{html.escape(str(ch['id']))}</code>
💰 Price: {fmt(ch['price'])}"
def char_info(ch): return f"🌐 <b>CHARACTER INFO</b>
✧ Name: {html.escape(ch['name'])} {RARITY_EMOJI.get(ch['rarity'],'')}
✧ Rarity: {RARITY_EMOJI.get(ch['rarity'],'')} {html.escape(ch['rarity'])}
✧ Anime: {html.escape(ch['anime'])}
✧ Price: {fmt(ch['price'])}
✧ ID: <code>{html.escape(str(ch['id']))}</code>"
def price_for_rarity(rarity):
    low,high=RARITY_PRICES[rarity]; return random.randint(low,high)
def remaining(seconds):
    seconds=max(0,int(seconds)); h,rem=divmod(seconds,3600); m,s=divmod(rem,60)
    return f'{h:02d}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'
def day_key(): return datetime.now(timezone.utc).date().isoformat()
