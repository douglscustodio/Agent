"""
config.py — Environment-driven configuration
All values loaded from environment variables (Railway / .env)
"""

import os
from dataclasses import dataclass, field


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class DatabaseConfig:
    url: str                = field(default_factory=lambda: _require("DATABASE_URL"))
    pool_min: int           = field(default_factory=lambda: int(_optional("DB_POOL_MIN", "2")))
    pool_max: int           = field(default_factory=lambda: int(_optional("DB_POOL_MAX", "10")))
    connect_timeout: int    = field(default_factory=lambda: int(_optional("DB_CONNECT_TIMEOUT", "10")))
    startup_retries: int    = field(default_factory=lambda: int(_optional("DB_STARTUP_RETRIES", "5")))
    retry_base_delay: float = field(default_factory=lambda: float(_optional("DB_RETRY_BASE_DELAY", "2.0")))


@dataclass(frozen=True)
class WebSocketConfig:
    url: str                  = field(default_factory=lambda: _optional(
                                    "WS_URL", "wss://api.hyperliquid.xyz/ws"
                                ))
    heartbeat_interval: int   = field(default_factory=lambda: int(_optional("WS_HEARTBEAT_INTERVAL", "30")))
    reconnect_base_delay: float = field(default_factory=lambda: float(_optional("WS_RECONNECT_BASE_DELAY", "1.0")))
    reconnect_max_delay: float  = field(default_factory=lambda: float(_optional("WS_RECONNECT_MAX_DELAY", "60.0")))
    reconnect_max_attempts: int = field(default_factory=lambda: int(_optional("WS_RECONNECT_MAX_ATTEMPTS", "0")))
    # 0 = unlimited
    dead_stream_timeout: int    = field(default_factory=lambda: int(_optional("WS_DEAD_STREAM_TIMEOUT", "60")))


@dataclass(frozen=True)
class HealthConfig:
    host: str = field(default_factory=lambda: _optional("HEALTH_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_optional("PORT", "8080")))


@dataclass(frozen=True)
class AppConfig:
    env: str      = field(default_factory=lambda: _optional("APP_ENV", "production"))
    log_level: str = field(default_factory=lambda: _optional("LOG_LEVEL", "INFO"))
    db: DatabaseConfig    = field(default_factory=DatabaseConfig)
    ws: WebSocketConfig   = field(default_factory=WebSocketConfig)
    health: HealthConfig  = field(default_factory=HealthConfig)


# Singleton — import this everywhere
config = AppConfig()
