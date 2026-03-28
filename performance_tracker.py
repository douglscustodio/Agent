"""
performance_tracker.py — Alert outcome tracking
Checks price at 1h / 4h / 24h after alert and classifies: TP1 / SL / NEUTRAL
Persists all results to DB table `performance_log`
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

from database import get_pool, write_system_event
from logger import get_logger

log = get_logger("performance_tracker")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TP1_PCT  = 0.03    # +3% = TP1 hit
SL_PCT   = 0.02    # -2% = SL hit
CHECK_INTERVALS_H = [1, 4, 24]

# ---------------------------------------------------------------------------
# Enums / dataclasses
# ---------------------------------------------------------------------------

class Outcome(str, Enum):
    TP1     = "TP1"
    SL      = "SL"
    NEUTRAL = "NEUTRAL"
    PENDING = "PENDING"


@dataclass
class PendingAlert:
    alert_id:     str
    symbol:       str
    direction:    str
    score:        float
    entry_price:  float
    alerted_at:   float       # unix epoch
    checked_1h:   bool = False
    checked_4h:   bool = False
    checked_24h:  bool = False
    final_outcome: Optional[Outcome] = None


@dataclass
class PerformanceRecord:
    alert_id:       str
    symbol:         str
    direction:      str
    score:          float
    entry_price:    float
    check_price:    float
    pnl_pct:        float
    outcome:        Outcome
    horizon_h:      int
    alerted_at:     float
    checked_at:     float


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS performance_log (
    id              BIGSERIAL PRIMARY KEY,
    alert_id        VARCHAR(120) NOT NULL,
    symbol          VARCHAR(20)  NOT NULL,
    direction       VARCHAR(10)  NOT NULL,
    score           NUMERIC(6,2) NOT NULL,
    entry_price     NUMERIC(20,8) NOT NULL,
    check_price     NUMERIC(20,8) NOT NULL,
    pnl_pct         NUMERIC(10,4) NOT NULL,
    outcome         VARCHAR(10)  NOT NULL,
    horizon_h       INTEGER      NOT NULL,
    alerted_at      TIMESTAMPTZ  NOT NULL,
    checked_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_perf_symbol    ON performance_log (symbol);
CREATE INDEX IF NOT EXISTS idx_perf_outcome   ON performance_log (outcome);
CREATE INDEX IF NOT EXISTS idx_perf_alerted   ON performance_log (alerted_at DESC);
CREATE INDEX IF NOT EXISTS idx_perf_alert_id  ON performance_log (alert_id);
"""


async def ensure_performance_schema() -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        log.info("DB_RECOVERED", "performance_log table verified", db_status="UP")
    except Exception as exc:
        log.error("DB_CONNECT_FAIL", f"performance schema error: {exc}", db_status="DOWN")


# ---------------------------------------------------------------------------
# Price provider stub
# ---------------------------------------------------------------------------

async def get_current_price(symbol: str) -> Optional[float]:
    """
    Fetch current mid-price for symbol.
    Phase 4: reads from ws_state price cache populated by websocket_client.
    Returns None if unavailable.
    """
    try:
        from websocket_client import ws_price_cache
        return ws_price_cache.get(symbol)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Outcome classifier
# ---------------------------------------------------------------------------

def classify_outcome(
    entry_price: float,
    check_price: float,
    direction:   str,
) -> Tuple[Outcome, float]:
    if entry_price <= 0:
        return Outcome.NEUTRAL, 0.0

    if direction.upper() == "LONG":
        pnl_pct = (check_price - entry_price) / entry_price
    else:
        pnl_pct = (entry_price - check_price) / entry_price

    if pnl_pct >= TP1_PCT:
        return Outcome.TP1, pnl_pct
    elif pnl_pct <= -SL_PCT:
        return Outcome.SL, pnl_pct
    else:
        return Outcome.NEUTRAL, pnl_pct


# ---------------------------------------------------------------------------
# PerformanceTracker
# ---------------------------------------------------------------------------

class PerformanceTracker:
    def __init__(self) -> None:
        self._pending: Dict[str, PendingAlert] = {}   # alert_id → PendingAlert

    async def startup(self) -> None:
        await ensure_performance_schema()
        log.info("SYSTEM_READY", "performance tracker ready")

    # ------------------------------------------------------------------
    # Register new alert
    # ------------------------------------------------------------------

    async def register_alert(
        self,
        alert_id:    str,
        symbol:      str,
        direction:   str,
        score:       float,
        entry_price: float,
    ) -> None:
        pending = PendingAlert(
            alert_id=alert_id,
            symbol=symbol,
            direction=direction,
            score=score,
            entry_price=entry_price,
            alerted_at=time.time(),
        )
        self._pending[alert_id] = pending
        await self._persist_pending(pending)

        log.info(
            "PERFORMANCE_LOGGED",
            f"registered alert for tracking: {symbol} {direction} entry={entry_price:.4f}",
            symbol=symbol,
            direction=direction,
            score=score,
            alert_id=alert_id,
        )

    # ------------------------------------------------------------------
    # Scheduled check — called every hour by scheduler
    # ------------------------------------------------------------------

    async def run_checks(self) -> None:
        """Evaluate all pending alerts against their time horizons."""
        if not self._pending:
            return

        now = time.time()
        completed = []

        for alert_id, pending in self._pending.items():
            elapsed_h = (now - pending.alerted_at) / 3600

            checks_due = []
            if elapsed_h >= 1  and not pending.checked_1h:  checks_due.append(1)
            if elapsed_h >= 4  and not pending.checked_4h:  checks_due.append(4)
            if elapsed_h >= 24 and not pending.checked_24h: checks_due.append(24)

            for horizon in checks_due:
                current_price = await get_current_price(pending.symbol)
                if current_price is None:
                    log.warning(
                        "PERFORMANCE_LOGGED",
                        f"no price for {pending.symbol} at {horizon}h check",
                        symbol=pending.symbol, alert_id=alert_id,
                    )
                    continue

                outcome, pnl_pct = classify_outcome(
                    pending.entry_price, current_price, pending.direction
                )

                record = PerformanceRecord(
                    alert_id=alert_id,
                    symbol=pending.symbol,
                    direction=pending.direction,
                    score=pending.score,
                    entry_price=pending.entry_price,
                    check_price=current_price,
                    pnl_pct=round(pnl_pct * 100, 4),
                    outcome=outcome,
                    horizon_h=horizon,
                    alerted_at=pending.alerted_at,
                    checked_at=time.time(),
                )
                await self._persist_record(record)

                outcome_emoji = "✅" if outcome == Outcome.TP1 else ("❌" if outcome == Outcome.SL else "➖")
                log.info(
                    "PERFORMANCE_LOGGED",
                    f"{outcome_emoji} {pending.symbol} {pending.direction} "
                    f"{horizon}h: {outcome} pnl={pnl_pct*100:.2f}%",
                    symbol=pending.symbol,
                    direction=pending.direction,
                    score=pending.score,
                    latency_ms=horizon * 3600 * 1000,
                    alert_id=alert_id,
                )
                await write_system_event(
                    "PERFORMANCE_LOGGED",
                    f"{pending.symbol} {pending.direction} {horizon}h outcome: {outcome} ({pnl_pct*100:.2f}%)",
                    level="INFO", module="performance_tracker",
                    symbol=pending.symbol, direction=pending.direction,
                    score=pending.score, alert_id=alert_id,
                )

                # Mark horizon checked
                if horizon == 1:  pending.checked_1h  = True
                if horizon == 4:  pending.checked_4h  = True
                if horizon == 24: pending.checked_24h = True
                if outcome in (Outcome.TP1, Outcome.SL):
                    pending.final_outcome = outcome

            # Remove fully evaluated alerts
            if pending.checked_24h:
                completed.append(alert_id)

        for aid in completed:
            self._pending.pop(aid, None)

    # ------------------------------------------------------------------
    # Performance stats for adaptive module
    # ------------------------------------------------------------------

    async def get_recent_stats(self, days: int = 7) -> dict:
        """
        Return win/loss stats over recent days for adaptive weight tuning.
        """
        cutoff_ts = time.time() - days * 86400
        cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc)

        sql = """
            SELECT
                outcome,
                COUNT(*)          AS count,
                AVG(pnl_pct)      AS avg_pnl,
                AVG(score)        AS avg_score,
                horizon_h
            FROM performance_log
            WHERE alerted_at >= $1
            GROUP BY outcome, horizon_h
            ORDER BY horizon_h, outcome
        """
        stats = {"tp1": 0, "sl": 0, "neutral": 0, "avg_pnl": 0.0, "win_rate": 0.0}
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, cutoff_dt)
            total = 0
            pnl_sum = 0.0
            for row in rows:
                if row["outcome"] == Outcome.TP1:
                    stats["tp1"] += row["count"]
                elif row["outcome"] == Outcome.SL:
                    stats["sl"]  += row["count"]
                else:
                    stats["neutral"] += row["count"]
                total   += row["count"]
                pnl_sum += float(row["avg_pnl"] or 0) * row["count"]

            if total > 0:
                stats["avg_pnl"]  = round(pnl_sum / total, 4)
                stats["win_rate"] = round(stats["tp1"] / total * 100, 2)
            stats["total"] = total
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"get_recent_stats failed: {exc}", db_status="DOWN")
        return stats

    # ------------------------------------------------------------------
    # Component-level stats for adaptive tuning
    # ------------------------------------------------------------------

    async def get_component_correlation(self) -> dict:
        """
        Compute correlation between each score component and TP1 outcome.
        Returns dict of component → correlation coefficient proxy.
        Phase 4: simplified — returns avg score of TP1 vs SL alerts.
        """
        sql = """
            SELECT outcome, AVG(score) as avg_score, COUNT(*) as n
            FROM performance_log
            WHERE horizon_h = 24
            GROUP BY outcome
        """
        result = {}
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql)
            for row in rows:
                result[row["outcome"]] = {
                    "avg_score": float(row["avg_score"] or 0),
                    "count": int(row["n"]),
                }
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"get_component_correlation failed: {exc}", db_status="DOWN")
        return result

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    async def _persist_pending(self, p: PendingAlert) -> None:
        sql = """
            INSERT INTO performance_log
                (alert_id, symbol, direction, score, entry_price,
                 check_price, pnl_pct, outcome, horizon_h, alerted_at, checked_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT DO NOTHING
        """
        alerted_dt = datetime.fromtimestamp(p.alerted_at, tz=timezone.utc)
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    sql, p.alert_id, p.symbol, p.direction, p.score,
                    p.entry_price, 0.0, 0.0, Outcome.PENDING.value, 0,
                    alerted_dt, alerted_dt,
                )
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"persist_pending failed: {exc}", db_status="DOWN")

    async def _persist_record(self, r: PerformanceRecord) -> None:
        sql = """
            INSERT INTO performance_log
                (alert_id, symbol, direction, score, entry_price,
                 check_price, pnl_pct, outcome, horizon_h, alerted_at, checked_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """
        alerted_dt = datetime.fromtimestamp(r.alerted_at, tz=timezone.utc)
        checked_dt = datetime.fromtimestamp(r.checked_at, tz=timezone.utc)
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    sql, r.alert_id, r.symbol, r.direction, r.score,
                    r.entry_price, r.check_price, r.pnl_pct, r.outcome.value,
                    r.horizon_h, alerted_dt, checked_dt,
                )
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"persist_record failed: {exc}", db_status="DOWN")
