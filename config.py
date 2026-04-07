"""
config.py — Environment-driven configuration for Jarvis AI
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    hyperliquid_address: str
    hyperliquid_key: str
    telegram_token: str
    telegram_chat_id: str
    groq_api_key: str
    cryptopanic_token: str
    database_url: str


def _getenv(key: str, default: str = "") -> str:
    return os.getenv(key, default)


config = Config(
    hyperliquid_address=_getenv("HYPERLIQUID_ADDRESS", ""),
    hyperliquid_key=_getenv("HYPERLIQUID_PRIVATE_KEY", ""),
    telegram_token=_getenv("TELEGRAM_BOT_TOKEN", ""),
    telegram_chat_id=_getenv("TELEGRAM_CHAT_ID", ""),
    groq_api_key=_getenv("GROQ_API_KEY", ""),
    cryptopanic_token=_getenv("CRYPTOPANIC_TOKEN", ""),
    database_url=_getenv("DATABASE_URL", "sqlite:///jarvis.db"),
)
