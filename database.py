"""
database.py — asyncpg connection pool (Supabase / PostgreSQL)
- Pool min=2 max=10 (configurable via env)
- Exponential backoff on startup
- system_events dual-write table
- DB health probe
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from config import config
from logger import get_logger

log = get_logger("database")

# ---------------------------------------------------------------------------
# Pool singleton
# ---------------------------------------------------------------------------

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call init_db() first.")
    return _pool


# ---------------------------------------------------------------------------
# Initialisation with retry
# ---------------------------------------------------------------------------

async def init_db() -> asyncpg.Pool:
    """
    Create the asyncpg pool with exponential backoff.
    Raises RuntimeError if all retries are exhausted.
    """
    global _pool
    cfg = config.db
    attempt = 0
    delay = cfg.retry_base_delay

    while attempt < cfg.startup_retries:
        attempt += 1
        try:
            log.info(
                "DB_CONNECT_ATTEMPT",
                f"connecting to database (attempt {attempt}/{cfg.startup_retries})",
                db_status="CONNECTING",
            )
            _pool = await asyncpg.create_pool(
                dsn=cfg.url,
                min_size=cfg.pool_min,
                max_size=cfg.pool_max,
                command_timeout=cfg.connect_timeout,
                ssl="require",
            )
            await _ensure_schema(_pool)
            log.info("DB_RECOVERED", "database pool ready", db_status="UP")
            return _pool

        except Exception as exc:
            log.error(
                "DB_CONNECT_FAIL",
                f"connection failed: {exc}",
                db_status="DOWN",
                reconnect_attempt=attempt,
            )
            if attempt < cfg.startup_retries:
                log.warning(
                    "DB_CONNECT_FAIL",
                    f"retrying in {delay:.1f}s",
                    db_status="DOWN",
                    reconnect_attempt=attempt,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            else:
                log.critical(
                    "DB_CONNECT_FAIL",
                    "all startup retries exhausted — cannot continue",
                    db_status="DOWN",
                )
                raise RuntimeError("Database startup failed after max retries.") from exc

    raise RuntimeError("Unreachable")


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("DB_CONNECT_FAIL", "database pool closed", db_status="CLOSED")


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS system_events (
    id                 BIGSERIAL PRIMARY KEY,
    timestamp          TIMESTAMPTZ  NOT NULL,
    level              VARCHAR(10)  NOT NULL,
    module             VARCHAR(100) NOT NULL,
    event_type         VARCHAR(100) NOT NULL,
    detail             TEXT         NOT NULL,
    symbol             VARCHAR(20),
    direction          VARCHAR(10),
    score              NUMERIC(10,4),
    ws_status          VARCHAR(20),
    reconnect_attempt  INTEGER,
    db_status          VARCHAR(20),
    alert_id           VARCHAR(100),
    latency_ms         NUMERIC(12,2),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_se_event_type ON system_events (event_type);
CREATE INDEX IF NOT EXISTS idx_se_level      ON system_events (level);
CREATE INDEX IF NOT EXISTS idx_se_timestamp  ON system_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_se_symbol     ON system_events (symbol) WHERE symbol IS NOT NULL;
"""


async def _ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)
    log.info("DB_CONNECT_ATTEMPT", "schema verified / created", db_status="UP")


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------

async def db_ping() -> bool:
    """Returns True if the pool can reach the database."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as exc:
        log.error("HEALTH_CHECK_FAIL", f"db ping failed: {exc}", db_status="DOWN")
        return False


# ---------------------------------------------------------------------------
# system_events writer
# ---------------------------------------------------------------------------

async def write_system_event(
    event_type: str,
    detail: str,
    level: str = "INFO",
    module: str = "system",
    **kwargs: Any,
) -> None:
    """
    Persist a system event row to system_events.
    Never raises — DB errors are logged to stdout only.
    """
    row = {
        "timestamp":         datetime.now(timezone.utc),
        "level":             level,
        "module":            module,
        "event_type":        event_type,
        "detail":            detail,
        "symbol":            kwargs.get("symbol"),
        "direction":         kwargs.get("direction"),
        "score":             kwargs.get("score"),
        "ws_status":         kwargs.get("ws_status"),
        "reconnect_attempt": kwargs.get("reconnect_attempt"),
        "db_status":         kwargs.get("db_status"),
        "alert_id":          kwargs.get("alert_id"),
        "latency_ms":        kwargs.get("latency_ms"),
    }
    sql = """
        INSERT INTO system_events
            (timestamp, level, module, event_type, detail,
             symbol, direction, score, ws_status, reconnect_attempt,
             db_status, alert_id, latency_ms)
        VALUES
            ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
    """
    try:
        if _pool is None:
            log.debug("DB_CONNECT_FAIL", f"pool not ready, skipping DB write for {event_type}")
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(sql, *row.values())
    except Exception as exc:
        log.error("DB_CONNECT_FAIL", f"failed to write system_event: {exc}", db_status="DOWN")
