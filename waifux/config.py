from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_DB = Path(os.getenv("STATE_DB", str(DATA_DIR / "waifuxprime.db")))
BALANCE_DB = Path(os.getenv("BALANCE_DB", str(DATA_DIR / "balance_system.db")))
COLLECTION_DB = Path(os.getenv("COLLECTION_DB", str(DATA_DIR / "collection.db")))
USERS_JSON = Path(os.getenv("USERS_JSON", str(DATA_DIR / "users.json")))
BOT_DATA_JSON = Path(os.getenv("BOT_DATA_JSON", str(DATA_DIR / "bot_data.json")))
BOT_TOKEN = (os.getenv("BOT_TOKEN") or os.getenv("Bot_Token") or "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "8163432539"))
SUDO_USERS = {int(x) for x in os.getenv("SUDO_USERS", "8470895455").split(",") if x.strip().isdigit()}
UPLOADER_USERS = {int(x) for x in os.getenv("UPLOADER_USERS", "8570944478,8186521526").split(",") if x.strip().isdigit()}
SPAWN_EVERY_MESSAGES = max(1, int(os.getenv("SPAWN_EVERY_MESSAGES", "50")))
SPAWN_LIFETIME_SECONDS = max(30, int(os.getenv("SPAWN_LIFETIME_SECONDS", "300")))
UPLOAD_CHANNEL = os.getenv("UPLOAD_CHANNEL", "")

RARITY_WEIGHTS = {
    "Starter": 50, "Rising": 50, "Shiny": 50, "Ultimate": 50,
    "BeachArc": 13, "ChibiArc": 17, "WinterArc": 19, "LoveArc": 3,
    "DarkArc": 5, "ChristmasArc": 15, "RoyalArc": 35, "NeonArc": 25,
    "CelestialArc": 35, "CrossArc": 4, "MangaArc": 50,
    "LimitedArc": 10, "SpecialArc": 8, "DivineArc": 5, "CinemaArc": 8,
}
RARITY_EMOJI = {
    "Starter":"⚪", "Rising":"🟢", "Shiny":"🟣", "Ultimate":"🟡",
    "BeachArc":"🏖️", "ChibiArc":"👼", "WinterArc":"❄️", "LoveArc":"💞",
    "DarkArc":"🎃", "ChristmasArc":"🎄", "RoyalArc":"👑", "NeonArc":"💫",
    "CelestialArc":"🪽", "CrossArc":"🧬", "MangaArc":"✨", "LimitedArc":"🔮",
    "SpecialArc":"🫧", "DivineArc":"🥵", "CinemaArc":"🎥",
}
RARITY_PRICES = {
    "Starter":(20000,25000), "Rising":(25000,33000), "Shiny":(35000,45000),
    "Ultimate":(50000,60000), "BeachArc":(600000,850000), "ChibiArc":(400000,550000),
    "WinterArc":(650000,850000), "LoveArc":(800000,1100000), "DarkArc":(600000,850000),
    "ChristmasArc":(600000,750000), "RoyalArc":(475000,650000), "NeonArc":(400000,600000),
    "CelestialArc":(550000,810000), "CrossArc":(800000,1100000), "MangaArc":(300000,450000),
    "LimitedArc":(750000,1000000), "SpecialArc":(800000,1300000),
    "DivineArc":(800000,1300000), "CinemaArc":(800000,1300000),
}
