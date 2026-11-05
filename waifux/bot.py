from __future__ import annotations
import logging
from telegram.ext import Application, ApplicationBuilder
from .config import BOT_TOKEN
from .database import init_db
from .handlers import register_all

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log=logging.getLogger(__name__)

def main():
    if not BOT_TOKEN:
        raise RuntimeError('BOT_TOKEN is missing. Add it in Replit Secrets, then run again.')
    init_db()
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    register_all(app)
    log.info('WaifuX Prime handlers registered; starting Telegram polling.')
    app.run_polling(allowed_updates=['message','callback_query','inline_query','chat_member'])
