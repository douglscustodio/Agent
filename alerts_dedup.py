"""
alerts_dedup.py — Alert deduplication engine
Rules:
  - 2-hour cooldown per symbol+direction key
  - Override if score delta > 10 points vs last sent
  - DB-backed persistence (survives restarts)
  - In-memory fast-path cache
  - Full JSON event logging on every decision
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from database import get_pool, write_system_event
from logger import get_logger

log = get_logger("alerts_dedup")

COOLDOWN_SECONDS   = 3_600    # 1 hora (era 2h — muito agressivo)
SCORE_DELTA_OVERRIDE = 8.0    # reenviar se score melhorou 8+ pts
DAILY_MAX_PER_SYMBOL = 3  # máximo 3 alertas do mesmo símbolo por dia

# ---------------------------------------------------------------------------
# In-memory cache entry
# ---------------------------------------------------------------------------

@dataclass
class DedupEntry:
    symbol:        str
    direction:     str
    last_sent_ts:  float      # unix epoch
    last_score:    float
    send_count:    int


# ---------------------------------------------------------------------------
# DedupStore
# ---------------------------------------------------------------------------

class AlertDedupStore:
    """
    Deduplication store backed by PostgreSQL table `alert_dedup`.
    Falls back to in-memory only if DB is unavailable.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, DedupEntry] = {}

    # ------------------------------------------------------------------
    # Schema bootstrap (called once at startup)
    # ------------------------------------------------------------------

    async def ensure_table(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS alert_dedup (
            key            VARCHAR(120) PRIMARY KEY,
            symbol         VARCHAR(20)  NOT NULL,
            direction      VARCHAR(10)  NOT NULL,
            last_sent_ts   DOUBLE PRECISION NOT NULL,
            last_score     NUMERIC(6,2) NOT NULL,
            send_count     INTEGER      NOT NULL DEFAULT 1,
            updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_dedup_symbol ON alert_dedup (symbol);
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(sql)
            log.info("DB_RECOVERED", "alert_dedup table verified", db_status="UP")
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"could not create alert_dedup table: {exc}", db_status="DOWN")

    # ------------------------------------------------------------------
    # Core decision
    # ------------------------------------------------------------------

    async def should_send(
        self,
        symbol:    str,
        direction: str,
        score:     float,
    ) -> Tuple[bool, str]:
        """
        Returns (should_send: bool, reason: str).
        Reasons: COOLDOWN | SCORE_DELTA_OVERRIDE | NEW | COOLDOWN_ACTIVE
        """
        key   = _make_key(symbol, direction)
        entry = await self._load(key)

     if entry is None:
         log.info(
          "ALERT_SENT",
           f"novo alerta {key} — enviando",
           symbol=symbol, direction=direction, score=score,
           )
            return True, "NOVO"
            return True, "NEW"

        now     = time.time()
            
        elapsed = now - entry.last_sent_ts
        delta   = score - entry.last_score

        # Score delta override
        if delta >= SCORE_DELTA_OVERRIDE:
            log.info(
                "ALERT_SENT",
                f"score delta override {key}: Δ{delta:.1f} (prev={entry.last_score:.1f} now={score:.1f})",
                symbol=symbol, direction=direction, score=score,
            )
            await write_system_event(
                "ALERT_SENT",
                f"score delta override: {symbol} {direction} Δ{delta:.1f}",
                level="INFO", module="alerts_dedup",
                symbol=symbol, direction=direction, score=score,
            )
            return True, "SCORE_DELTA_OVERRIDE"

        # Still in cooldown
        if elapsed < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - elapsed)
            log.info(
                "ALERT_SUPPRESSED",
                f"cooldown active {key}: {remaining}s remaining, score={score:.1f}",
                symbol=symbol, direction=direction, score=score,
            )
            await write_system_event(
                "ALERT_SUPPRESSED",
                f"cooldown active for {symbol} {direction} ({remaining}s left)",
                level="INFO", module="alerts_dedup",
                symbol=symbol, direction=direction, score=score,
            )
            return False, "COOLDOWN_ACTIVE"

        # Cooldown expired
        log.info(
            "ALERT_SENT",
            f"cooldown expired {key} — sending",
            symbol=symbol, direction=direction, score=score,
        )
        return True, "COOLDOWN_EXPIRED"

    async def record_sent(
        self,
        symbol:    str,
        direction: str,
        score:     float,
    ) -> None:
        """Persist a sent-alert record."""
        key = _make_key(symbol, direction)
        now = time.time()
        existing = await self._load(key)
        count = (existing.send_count + 1) if existing else 1

        entry = DedupEntry(
            symbol=symbol,
            direction=direction,
            last_sent_ts=now,
            last_score=score,
            send_count=count,
        )
        self._cache[key] = entry
        await self._persist(key, entry)

    # ------------------------------------------------------------------
    # DB I/O
    # ------------------------------------------------------------------

    async def _load(self, key: str) -> Optional[DedupEntry]:
        # Fast-path: in-memory
        if key in self._cache:
            return self._cache[key]

        # DB load
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT symbol, direction, last_sent_ts, last_score, send_count "
                    "FROM alert_dedup WHERE key = $1",
                    key,
                )
            if row:
                entry = DedupEntry(
                    symbol=row["symbol"],
                    direction=row["direction"],
                    last_sent_ts=float(row["last_sent_ts"]),
                    last_score=float(row["last_score"]),
                    send_count=int(row["send_count"]),
                )
                self._cache[key] = entry
                return entry
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"dedup load failed: {exc}", db_status="DOWN")
        return None

    async def _persist(self, key: str, entry: DedupEntry) -> None:
        sql = """
            INSERT INTO alert_dedup (key, symbol, direction, last_sent_ts, last_score, send_count, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (key) DO UPDATE
                SET last_sent_ts = EXCLUDED.last_sent_ts,
                    last_score   = EXCLUDED.last_score,
                    send_count   = EXCLUDED.send_count,
                    updated_at   = NOW()
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    sql, key, entry.symbol, entry.direction,
                    entry.last_sent_ts, entry.last_score, entry.send_count,
                )
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"dedup persist failed: {exc}", db_status="DOWN")


def _make_key(symbol: str, direction: str) -> str:
    return f"{symbol.upper()}:{direction.upper()}"
