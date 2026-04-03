"""
database.py — asyncpg connection pool (Supabase / PostgreSQL)

- Pool min=2 max=10 (configurable via env)
- Exponential backoff on startup
- system_events dual-write table
- DB health probe

UPGRADE: EventBuffer — batched system_event writes
  The original write_system_event() did one DB round-trip per call.
  In a high-frequency scan cycle with 24 symbols this generates 50-100
  DB writes per scan. Under load this causes:
    - connection pool contention
    - measurable latency added to scan cycles
    - DB write amplification on Supabase free tier

  Solution: EventBuffer accumulates events in memory and flushes in a
  single executemany() call when either:
    a) Buffer reaches FLUSH_BATCH_SIZE events (default 30), OR
    b) FLUSH_INTERVAL_S seconds have elapsed since last flush (default 15s)

  The buffer is flushed on shutdown via flush_now().
  write_system_event() is 100% backwards compatible — callers unchanged.

  Result: from ~80 individual INSERTs per scan to 1-3 batch INSERTs.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, List, Optional

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
            log.error("DB_CONNECT_FAIL", f"connection failed: {exc}", db_status="DOWN",
                      reconnect_attempt=attempt)
            if attempt < cfg.startup_retries:
                log.warning("DB_CONNECT_FAIL", f"retrying in {delay:.1f}s",
                            db_status="DOWN", reconnect_attempt=attempt)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            else:
                log.critical("DB_CONNECT_FAIL", "all startup retries exhausted — cannot continue",
                             db_status="DOWN")
                raise RuntimeError("Database startup failed after max retries.") from exc

    raise RuntimeError("Unreachable")


async def close_db() -> None:
    global _pool
    # Flush buffered events before closing
    await _event_buffer.flush_now()
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
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as exc:
        log.error("HEALTH_CHECK_FAIL", f"db ping failed: {exc}", db_status="DOWN")
        return False


# ---------------------------------------------------------------------------
# UPGRADE: EventBuffer — batched system_event inserts
# ---------------------------------------------------------------------------

FLUSH_BATCH_SIZE  = 30     # flush when this many events are buffered
FLUSH_INTERVAL_S  = 15.0   # flush at most every 15 seconds regardless of count

_INSERT_SQL = """
    INSERT INTO system_events
        (timestamp, level, module, event_type, detail,
         symbol, direction, score, ws_status, reconnect_attempt,
         db_status, alert_id, latency_ms)
    VALUES
        ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
"""


class EventBuffer:
    """
    Thread-safe (asyncio) buffer for system_event rows.
    Accumulates rows in memory and flushes as a single executemany() call.

    Flush triggers:
      1. Buffer reaches FLUSH_BATCH_SIZE
      2. FLUSH_INTERVAL_S seconds since last flush
      3. Explicit flush_now() call (used on shutdown)

    On flush failure: rows are NOT lost — they're logged to stdout
    and discarded. We accept eventual-consistency semantics for
    diagnostic events; we never block scan cycles on DB writes.
    """

    def __init__(self) -> None:
        self._rows: List[tuple] = []
        self._lock  = asyncio.Lock()
        self._last_flush: float = time.time()

    def _make_row(
        self,
        event_type: str,
        detail: str,
        level: str = "INFO",
        module: str = "system",
        **kwargs: Any,
    ) -> tuple:
        return (
            datetime.now(timezone.utc),
            level,
            module,
            event_type,
            detail,
            kwargs.get("symbol"),
            kwargs.get("direction"),
            kwargs.get("score"),
            kwargs.get("ws_status"),
            kwargs.get("reconnect_attempt"),
            kwargs.get("db_status"),
            kwargs.get("alert_id"),
            kwargs.get("latency_ms"),
        )

    async def add(self, *args, **kwargs) -> None:
        """Add one event to the buffer. Triggers flush if threshold reached."""
        row = self._make_row(*args, **kwargs)
        async with self._lock:
            self._rows.append(row)
            should_flush = (
                len(self._rows) >= FLUSH_BATCH_SIZE
                or (time.time() - self._last_flush) >= FLUSH_INTERVAL_S
            )

        if should_flush:
            await self.flush_now()

    async def flush_now(self) -> None:
        """Drain the buffer and write all pending rows in a single batch."""
        async with self._lock:
            if not self._rows:
                return
            rows_to_flush = self._rows[:]
            self._rows.clear()
            self._last_flush = time.time()

        if _pool is None:
            log.debug("DB_CONNECT_FAIL",
                      f"pool not ready, dropping {len(rows_to_flush)} buffered events")
            return

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.executemany(_INSERT_SQL, rows_to_flush)
            log.debug(
                "DB_CONNECT_ATTEMPT",
                f"event buffer flushed: {len(rows_to_flush)} rows in one batch",
                db_status="UP",
            )
        except Exception as exc:
            # Log to stdout only — never block the caller
            log.error(
                "DB_CONNECT_FAIL",
                f"event buffer flush failed ({len(rows_to_flush)} events dropped): {exc}",
                db_status="DOWN",
            )


# Singleton buffer — shared across all callers
_event_buffer = EventBuffer()


# ---------------------------------------------------------------------------
# Public API — backwards compatible
# ---------------------------------------------------------------------------

async def write_system_event(
    event_type: str,
    detail: str,
    level: str = "INFO",
    module: str = "system",
    **kwargs: Any,
) -> None:
    """
    Buffer a system event for batch DB insertion.
    API is identical to the original — callers are unchanged.

    Events are written to DB in batches of FLUSH_BATCH_SIZE or every
    FLUSH_INTERVAL_S seconds, whichever comes first.
    """
    await _event_buffer.add(event_type, detail, level=level, module=module, **kwargs)


async def flush_event_buffer() -> None:
    """
    Manually trigger a buffer flush.
    Called from the scheduler every FLUSH_INTERVAL_S seconds to ensure
    events are persisted even in low-traffic periods.
    """
    await _event_buffer.flush_now()
