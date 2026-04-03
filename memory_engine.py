"""
memory_engine.py — Motor de aprendizado e memória do agente
O agente aprende:
  - Quais padrões de score historicamente acertam
  - Quais setores performam melhor em cada regime de mercado
  - Quais condições de funding/OI precedem acertos
  - Quais sinais devem ser ignorados (padrões de falha)
Persiste tudo no banco de dados.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from database import get_pool, write_system_event
from logger import get_logger

log = get_logger("memory_engine")

# Memory limits — prevents unbounded RAM growth
MAX_PATTERN_KEYS   = 500     # max distinct patterns remembered
MAX_MEMORY_EVENTS  = 1000    # max rows in pattern_log kept in DB
MAX_SECTOR_KEYS    = 100

# ---------------------------------------------------------------------------
# DB Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_memory (
    id           BIGSERIAL PRIMARY KEY,
    memory_type  VARCHAR(50)  NOT NULL,
    key          VARCHAR(200) NOT NULL,
    value        JSONB        NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (memory_type, key)
);
CREATE INDEX IF NOT EXISTS idx_memory_type ON agent_memory (memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_key  ON agent_memory (key);

CREATE TABLE IF NOT EXISTS pattern_log (
    id           BIGSERIAL PRIMARY KEY,
    symbol       VARCHAR(20)  NOT NULL,
    direction    VARCHAR(10)  NOT NULL,
    score        NUMERIC(6,2) NOT NULL,
    regime       VARCHAR(20),
    sector       VARCHAR(20),
    funding_bias VARCHAR(20),
    oi_trend     VARCHAR(20),
    macro_risk   NUMERIC(6,2),
    outcome      VARCHAR(10),
    pnl_pct      NUMERIC(10,4),
    horizon_h    INTEGER,
    logged_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pattern_outcome ON pattern_log (outcome);
CREATE INDEX IF NOT EXISTS idx_pattern_sector  ON pattern_log (sector);
CREATE INDEX IF NOT EXISTS idx_pattern_regime  ON pattern_log (regime);
"""


async def ensure_memory_schema() -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        log.info("DB_RECOVERED", "memory schema verified", db_status="UP")
    except Exception as exc:
        log.error("DB_CONNECT_FAIL", f"memory schema error: {exc}", db_status="DOWN")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PatternMemory:
    """What the agent has learned about a pattern."""
    pattern_key:    str
    total_signals:  int   = 0
    tp1_count:      int   = 0
    sl_count:       int   = 0
    neutral_count:  int   = 0
    avg_pnl:        float = 0.0
    win_rate:       float = 0.0
    should_ignore:  bool  = False    # True if win_rate < 30%
    confidence:     float = 0.0      # 0-1, based on sample size

    def update(self, outcome: str, pnl: float) -> None:
        self.total_signals += 1
        if outcome == "TP1":
            self.tp1_count += 1
        elif outcome == "SL":
            self.sl_count += 1
        else:
            self.neutral_count += 1

        # Running average PnL
        self.avg_pnl = (self.avg_pnl * (self.total_signals - 1) + pnl) / self.total_signals
        self.win_rate = self.tp1_count / self.total_signals * 100
        self.confidence = min(self.total_signals / 30, 1.0)   # full confidence at 30+ signals
        self.should_ignore = self.win_rate < 30 and self.confidence > 0.5


@dataclass
class SectorMemory:
    """Per-sector performance by BTC regime."""
    sector:     str
    regime:     str
    win_rate:   float = 50.0
    avg_pnl:    float = 0.0
    signals:    int   = 0
    hot:        bool  = False


@dataclass
class AgentInsight:
    """What the agent knows right now — injected into signals."""
    pattern_verdict:    str = "SEM DADOS"
    sector_verdict:     str = "SEM DADOS"
    similar_past:       str = ""
    ignore_signal:      bool = False
    confidence_boost:   float = 0.0   # -10 to +10 score adjustment
    explanation:        List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MemoryEngine
# ---------------------------------------------------------------------------

class MemoryEngine:
    def __init__(self) -> None:
        self._patterns:      Dict[str, PatternMemory] = {}
        self._sector_memory: Dict[str, SectorMemory]  = {}

    async def startup(self) -> None:
        await ensure_memory_schema()
        await self._load_from_db()
        await self._purge_old_patterns()
        log.info("SYSTEM_READY", f"memory engine ready: {len(self._patterns)} patterns loaded")

    # ------------------------------------------------------------------
    # Record a new signal outcome (called by performance_tracker)
    # ------------------------------------------------------------------

    async def record_outcome(
        self,
        symbol:      str,
        direction:   str,
        score:       float,
        regime:      str,
        sector:      str,
        funding_bias: str,
        oi_trend:    str,
        macro_risk:  float,
        outcome:     str,
        pnl_pct:     float,
        horizon_h:   int,
    ) -> None:
        """Record signal outcome and update pattern memory."""
        pattern_key = self._make_pattern_key(regime, sector, direction, score)

        # Update in-memory pattern
        if pattern_key not in self._patterns:
            self._patterns[pattern_key] = PatternMemory(pattern_key=pattern_key)
        self._patterns[pattern_key].update(outcome, pnl_pct)

        # Update sector memory
        sec_key = f"{sector}:{regime}"
        if sec_key not in self._sector_memory:
            self._sector_memory[sec_key] = SectorMemory(sector=sector, regime=regime)
        sm = self._sector_memory[sec_key]
        sm.signals += 1
        sm.avg_pnl  = (sm.avg_pnl * (sm.signals - 1) + pnl_pct) / sm.signals
        if outcome == "TP1":
            sm.win_rate = (sm.win_rate * (sm.signals - 1) + 100) / sm.signals
        else:
            sm.win_rate = (sm.win_rate * (sm.signals - 1)) / sm.signals
        sm.hot = sm.win_rate > 60 and sm.signals >= 5

        # Persist to DB
        await self._persist_pattern(pattern_key, self._patterns[pattern_key])
        await self._persist_sector(sec_key, sm)
        await self._log_pattern(
            symbol, direction, score, regime, sector,
            funding_bias, oi_trend, macro_risk, outcome, pnl_pct, horizon_h,
        )

        log.info(
            "MEMORY_UPDATED",
            f"memory updated: {pattern_key} win_rate={self._patterns[pattern_key].win_rate:.1f}%",
            symbol=symbol, score=score,
        )

    # ------------------------------------------------------------------
    # Get insight for a new signal (called before dispatch)
    # ------------------------------------------------------------------

    def get_insight(
        self,
        direction:  str,
        score:      float,
        regime:     str,
        sector:     str,
        macro_risk: float,
    ) -> AgentInsight:
        insight    = AgentInsight()
        pattern_key= self._make_pattern_key(regime, sector, direction, score)
        pm         = self._patterns.get(pattern_key)
        sec_key    = f"{sector}:{regime}"
        sm         = self._sector_memory.get(sec_key)

        # Pattern verdict
        if pm and pm.confidence > 0.3:
            if pm.should_ignore:
                insight.ignore_signal = True
                insight.pattern_verdict = f"[WARN] Padrão similar falhou {pm.sl_count}x — taxa de acerto só {pm.win_rate:.0f}%"
                insight.confidence_boost = -10
                insight.explanation.append(insight.pattern_verdict)
            elif pm.win_rate > 65:
                insight.pattern_verdict = f"[OK] Padrão similar acertou {pm.tp1_count}x de {pm.total_signals} ({pm.win_rate:.0f}%)"
                insight.confidence_boost = min(pm.win_rate / 10 - 5, 8)
                insight.explanation.append(insight.pattern_verdict)
            else:
                insight.pattern_verdict = f" Padrão com resultado misto ({pm.win_rate:.0f}% de acerto)"
                insight.explanation.append(insight.pattern_verdict)

        # Sector verdict
        if sm and sm.signals >= 3:
            if sm.hot:
                insight.sector_verdict = f"[HOT] Setor {sector} quente neste regime ({sm.win_rate:.0f}% acerto)"
                insight.confidence_boost += 5
                insight.explanation.append(insight.sector_verdict)
            elif sm.win_rate < 35:
                insight.sector_verdict = f" Setor {sector} frio neste regime ({sm.win_rate:.0f}% acerto)"
                insight.confidence_boost -= 5
                insight.explanation.append(insight.sector_verdict)

        # Macro risk adjustment
        if macro_risk > 75:
            insight.confidence_boost -= 8
            insight.explanation.append("[WARN] Risco macro alto — sinal reduzido automaticamente")
        elif macro_risk < 35:
            insight.confidence_boost += 3
            insight.explanation.append("[OK] Ambiente macro favorável")

        return insight

    # ------------------------------------------------------------------
    # Hot sectors in current regime
    # ------------------------------------------------------------------

    def get_hot_sectors(self, regime: str) -> List[str]:
        return [
            sm.sector for sm in self._sector_memory.values()
            if sm.regime == regime and sm.hot
        ]

    def get_ignore_patterns(self) -> List[str]:
        return [k for k, pm in self._patterns.items() if pm.should_ignore]

    # ------------------------------------------------------------------
    # DB I/O
    # ------------------------------------------------------------------

    async def _persist_pattern(self, key: str, pm: PatternMemory) -> None:
        sql = """
            INSERT INTO agent_memory (memory_type, key, value, updated_at)
            VALUES ('pattern', $1, $2::jsonb, NOW())
            ON CONFLICT (memory_type, key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(sql, key, json.dumps({
                    "total": pm.total_signals, "tp1": pm.tp1_count,
                    "sl": pm.sl_count, "neutral": pm.neutral_count,
                    "avg_pnl": pm.avg_pnl, "win_rate": pm.win_rate,
                    "ignore": pm.should_ignore, "confidence": pm.confidence,
                }))
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"persist pattern failed: {exc}", db_status="DOWN")

    async def _persist_sector(self, key: str, sm: SectorMemory) -> None:
        sql = """
            INSERT INTO agent_memory (memory_type, key, value, updated_at)
            VALUES ('sector', $1, $2::jsonb, NOW())
            ON CONFLICT (memory_type, key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(sql, key, json.dumps({
                    "sector": sm.sector, "regime": sm.regime,
                    "win_rate": sm.win_rate, "avg_pnl": sm.avg_pnl,
                    "signals": sm.signals, "hot": sm.hot,
                }))
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"persist sector failed: {exc}", db_status="DOWN")

    async def _log_pattern(self, symbol, direction, score, regime, sector,
                           funding_bias, oi_trend, macro_risk, outcome, pnl_pct, horizon_h) -> None:
        sql = """
            INSERT INTO pattern_log
                (symbol, direction, score, regime, sector, funding_bias,
                 oi_trend, macro_risk, outcome, pnl_pct, horizon_h)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(sql, symbol, direction, score, regime, sector,
                                   funding_bias, oi_trend, macro_risk, outcome, pnl_pct, horizon_h)
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"log pattern failed: {exc}", db_status="DOWN")

    async def _purge_old_patterns(self) -> None:
        """Remove oldest pattern_log rows and trim in-memory dicts."""
        # Trim in-memory dicts
        if len(self._patterns) > MAX_PATTERN_KEYS:
            # Remove lowest confidence patterns first
            sorted_keys = sorted(self._patterns, key=lambda k: self._patterns[k].confidence)
            for k in sorted_keys[:len(self._patterns) - MAX_PATTERN_KEYS]:
                del self._patterns[k]
            log.info("MEMORY_PURGE", f"trimmed patterns to {len(self._patterns)}")

        if len(self._sector_memory) > MAX_SECTOR_KEYS:
            sorted_keys = sorted(self._sector_memory, key=lambda k: self._sector_memory[k].signals)
            for k in sorted_keys[:len(self._sector_memory) - MAX_SECTOR_KEYS]:
                del self._sector_memory[k]

        # Trim DB pattern_log
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    f"""DELETE FROM pattern_log WHERE id NOT IN (
                        SELECT id FROM pattern_log ORDER BY logged_at DESC LIMIT {MAX_MEMORY_EVENTS}
                    )"""
                )
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"purge pattern_log failed: {exc}", db_status="DOWN")

    async def _load_from_db(self) -> None:
        sql = "SELECT memory_type, key, value FROM agent_memory"
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql)
            for row in rows:
                val = json.loads(row["value"])
                if row["memory_type"] == "pattern":
                    pm = PatternMemory(
                        pattern_key=row["key"],
                        total_signals=val.get("total", 0),
                        tp1_count=val.get("tp1", 0),
                        sl_count=val.get("sl", 0),
                        neutral_count=val.get("neutral", 0),
                        avg_pnl=val.get("avg_pnl", 0.0),
                        win_rate=val.get("win_rate", 0.0),
                        should_ignore=val.get("ignore", False),
                        confidence=val.get("confidence", 0.0),
                    )
                    self._patterns[row["key"]] = pm
                elif row["memory_type"] == "sector":
                    sm = SectorMemory(
                        sector=val.get("sector", ""),
                        regime=val.get("regime", ""),
                        win_rate=val.get("win_rate", 50.0),
                        avg_pnl=val.get("avg_pnl", 0.0),
                        signals=val.get("signals", 0),
                        hot=val.get("hot", False),
                    )
                    self._sector_memory[row["key"]] = sm
        except Exception as exc:
            log.warning("MEMORY_LOAD_FAIL", f"memory load failed: {exc}")

    @staticmethod
    def _make_pattern_key(regime: str, sector: str, direction: str, score: float) -> str:
        score_bucket = "HIGH" if score >= 85 else ("MED" if score >= 75 else "LOW")
        return f"{regime}:{sector}:{direction}:{score_bucket}"
