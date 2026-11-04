"""Environment-backed configuration. Never put secrets in this file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _id_set(name: str) -> set[int]:
    values: set[int] = set()
    for item in os.getenv(name, "").split(","):
        item = item.strip()
        if item:
            try:
                values.add(int(item))
            except ValueError as exc:
                raise RuntimeError(f"{name} must contain comma-separated Telegram IDs") from exc
    return values


@dataclass(frozen=True)
class Settings:
    bot_token: str = (os.getenv("BOT_TOKEN") or os.getenv("Bot_Token") or "").strip()
    mongo_uri: str = os.getenv("MONGO_URI", "").strip()
    mongo_db_name: str = os.getenv("MONGO_DB_NAME", "Character_catcher").strip()
    owner_id: int = _int_env("OWNER_ID")
    sudo_users: set[int] = None  # type: ignore[assignment]
    uploader_users: set[int] = None  # type: ignore[assignment]
    group_id: int = _int_env("GROUP_ID")
    character_channel_id: str = os.getenv("CHARA_CHANNEL_ID", "").strip()
    bot_username: str = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    support_chat: str = os.getenv("SUPPORT_CHAT", "").strip()
    update_chat: str = os.getenv("UPDATE_CHAT", "").strip()
    spawn_every_messages: int = _int_env("SPAWN_EVERY_MESSAGES", 50)
    spawn_lifetime_seconds: int = _int_env("SPAWN_LIFETIME_SECONDS", 300)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()


def load_settings() -> Settings:
    settings = Settings(sudo_users=_id_set("SUDO_USERS"), uploader_users=_id_set("UPLOADER_USERS"))
    missing = []
    if not settings.bot_token:
        missing.append("BOT_TOKEN")
    if not settings.mongo_uri:
        missing.append("MONGO_URI")
    if not settings.owner_id:
        missing.append("OWNER_ID")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    if settings.spawn_every_messages < 1:
        raise RuntimeError("SPAWN_EVERY_MESSAGES must be at least 1")
    if settings.spawn_lifetime_seconds < 30:
        raise RuntimeError("SPAWN_LIFETIME_SECONDS must be at least 30")
    return settings


RARITIES = {
    "starter": {"name": "Starter", "emoji": "⚪", "weight": 50, "price": (20000, 25000)},
    "rising": {"name": "Rising", "emoji": "🟢", "weight": 50, "price": (25000, 33000)},
    "shiny": {"name": "Shiny", "emoji": "🟣", "weight": 50, "price": (35000, 45000)},
    "ultimate": {"name": "Ultimate", "emoji": "🟡", "weight": 50, "price": (50000, 60000)},
    "beacharc": {"name": "BeachArc", "emoji": "🏖️", "weight": 13, "price": (600000, 850000)},
    "chibiarc": {"name": "Chibiarc", "emoji": "👼", "weight": 17, "price": (400000, 550000)},
    "winterarc": {"name": "WinterArc", "emoji": "❄️", "weight": 19, "price": (650000, 850000)},
    "lovearc": {"name": "LoveArc", "emoji": "💞", "weight": 3, "price": (800000, 1100000)},
    "darkarc": {"name": "DarkArc", "emoji": "🎃", "weight": 5, "price": (600000, 850000)},
    "christmasarc": {"name": "ChristmasArc", "emoji": "🎄", "weight": 15, "price": (600000, 750000)},
    "royalarc": {"name": "RoyalArc", "emoji": "👑", "weight": 35, "price": (475000, 650000)},
    "neonarc": {"name": "NeonArc", "emoji": "💫", "weight": 25, "price": (400000, 600000)},
    "celestialarc": {"name": "CelestialArc", "emoji": "🪽", "weight": 35, "price": (550000, 810000)},
    "crossarc": {"name": "CrossArc", "emoji": "🧬", "weight": 4, "price": (800000, 1100000)},
    "mangaarc": {"name": "MangaArc", "emoji": "✨", "weight": 50, "price": (300000, 450000)},
    "limitedarc": {"name": "LimitedArc", "emoji": "🔮", "weight": 10, "price": (750000, 1000000)},
    "specialarc": {"name": "SpecialArc", "emoji": "🫧", "weight": 8, "price": (800000, 1300000)},
    "divinearc": {"name": "DivineArc", "emoji": "🥵", "weight": 8, "price": (800000, 1300000)},
    "cinemaarc": {"name": "CinemaArc", "emoji": "🎥", "weight": 8, "price": (800000, 1300000)},
}

LEGACY_RARITY_ALIASES = {"common": "starter", "rare": "rising", "legendary": "shiny", "medium": "ultimate"}


def normalize_rarity(value: str) -> str:
    key = "".join(ch for ch in value.lower() if ch.isalnum())
    return LEGACY_RARITY_ALIASES.get(key, key)


def rarity_meta(value: str) -> dict:
    key = normalize_rarity(value)
    return RARITIES.get(key, {"name": value or "Unknown", "emoji": "🎴", "weight": 1, "price": (20000, 25000)})
