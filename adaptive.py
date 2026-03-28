"""
adaptive.py — Adaptive scoring weight engine
Reads performance stats and nudges component weights toward
factors that correlate with TP1 outcomes.
Weights are persisted to DB and injected into scoring.py at runtime.
"""

import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, Optional

from database import get_pool, write_system_event
from logger import get_logger
from scoring import WEIGHTS as BASE_WEIGHTS

log = get_logger("adaptive")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_SAMPLES_TO_ADAPT = 20       # minimum performance records before adjusting
MAX_WEIGHT_DELTA     = 0.05     # max shift per component per adaptation cycle
WEIGHT_MIN           = 0.05     # floor per component
WEIGHT_MAX           = 0.35     # ceiling per component
ADAPT_INTERVAL_H     = 24       # how often weights are re-evaluated

# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS adaptive_weights (
    id          BIGSERIAL PRIMARY KEY,
    weights     JSONB        NOT NULL,
    win_rate    NUMERIC(6,2),
    avg_pnl     NUMERIC(8,4),
    sample_n    INTEGER,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""


async def ensure_adaptive_schema() -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        log.info("DB_RECOVERED", "adaptive_weights table verified", db_status="UP")
    except Exception as exc:
        log.error("DB_CONNECT_FAIL", f"adaptive schema error: {exc}", db_status="DOWN")


# ---------------------------------------------------------------------------
# Weight state
# ---------------------------------------------------------------------------

@dataclass
class WeightState:
    weights:   Dict[str, float] = field(default_factory=lambda: deepcopy(BASE_WEIGHTS))
    win_rate:  float = 0.0
    avg_pnl:   float = 0.0
    sample_n:  int   = 0
    updated_at: float = 0.0

    def normalise(self) -> None:
        """Ensure weights still sum to 1.0 after adjustment."""
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: round(v / total, 6) for k, v in self.weights.items()}


# ---------------------------------------------------------------------------
# AdaptiveEngine
# ---------------------------------------------------------------------------

class AdaptiveEngine:
    """
    Reads performance data and adjusts scoring weights.
    Thread-safe: weights dict is replaced atomically.
    """

    def __init__(self) -> None:
        self._state = WeightState()

    async def startup(self) -> None:
        await ensure_adaptive_schema()
        await self._load_latest_weights()
        log.info("SYSTEM_READY", "adaptive engine ready", )

    def get_weights(self) -> Dict[str, float]:
        """Return current live weights (copy)."""
        return deepcopy(self._state.weights)

    # ------------------------------------------------------------------
    # Main adaptation cycle
    # ------------------------------------------------------------------

    async def adapt(self, tracker) -> None:
        """
        Pull performance stats from tracker, compute new weights, persist.
        `tracker` is a PerformanceTracker instance.
        """
        stats = await tracker.get_recent_stats(days=7)
        n     = stats.get("total", 0)

        if n < MIN_SAMPLES_TO_ADAPT:
            log.info(
                "PERFORMANCE_LOGGED",
                f"adaptive: only {n} samples (need {MIN_SAMPLES_TO_ADAPT}) — keeping current weights",
            )
            return

        win_rate = stats.get("win_rate", 0.0)
        avg_pnl  = stats.get("avg_pnl",  0.0)

        log.info(
            "PERFORMANCE_LOGGED",
            f"adaptive: {n} samples win_rate={win_rate:.1f}% avg_pnl={avg_pnl:.4f}",
        )

        new_weights = deepcopy(self._state.weights)

        # --- Adjustment heuristics ---

        # Low win rate: boost fundamentals (OI, funding) over technicals
        if win_rate < 40:
            new_weights["oi_acceleration"]   = _nudge(new_weights["oi_acceleration"],   +0.02)
            new_weights["funding"]           = _nudge(new_weights["funding"],           +0.02)
            new_weights["relative_strength"] = _nudge(new_weights["relative_strength"], +0.01)
            new_weights["bb_squeeze"]        = _nudge(new_weights["bb_squeeze"],        -0.02)
            new_weights["atr_quality"]       = _nudge(new_weights["atr_quality"],       -0.03)
            log.warning(
                "PERFORMANCE_LOGGED",
                f"adaptive: low win rate ({win_rate:.1f}%) — boosting fundamentals",
            )

        # High win rate with positive PnL: lightly increase momentum factors
        elif win_rate > 60 and avg_pnl > 0:
            new_weights["adx_regime"]        = _nudge(new_weights["adx_regime"],        +0.01)
            new_weights["rsi_quality"]       = _nudge(new_weights["rsi_quality"],       +0.01)
            new_weights["oi_acceleration"]   = _nudge(new_weights["oi_acceleration"],   +0.01)
            new_weights["funding"]           = _nudge(new_weights["funding"],           -0.01)
            log.info(
                "PERFORMANCE_LOGGED",
                f"adaptive: high win rate ({win_rate:.1f}%) — reinforcing momentum",
            )

        # Negative avg PnL despite neutral win rate: tighten late-entry filter weight
        if avg_pnl < -0.5:
            new_weights["atr_quality"]       = _nudge(new_weights["atr_quality"],       +0.03)
            new_weights["bb_squeeze"]        = _nudge(new_weights["bb_squeeze"],        +0.02)
            new_weights["relative_strength"] = _nudge(new_weights["relative_strength"], -0.02)
            log.warning(
                "PERFORMANCE_LOGGED",
                f"adaptive: negative avg_pnl ({avg_pnl:.4f}) — tightening entry quality",
            )

        # Clamp all weights
        for k in new_weights:
            new_weights[k] = max(WEIGHT_MIN, min(WEIGHT_MAX, new_weights[k]))

        state = WeightState(
            weights=new_weights,
            win_rate=win_rate,
            avg_pnl=avg_pnl,
            sample_n=n,
            updated_at=time.time(),
        )
        state.normalise()

        # Atomic replace
        self._state = state
        await self._persist_weights(state)

        log.info(
            "PERFORMANCE_LOGGED",
            f"adaptive weights updated: {_fmt_weights(state.weights)}",
        )
        await write_system_event(
            "PERFORMANCE_LOGGED",
            f"adaptive weights updated: win_rate={win_rate:.1f}% samples={n}",
            level="INFO", module="adaptive",
            score=win_rate,
        )

    # ------------------------------------------------------------------
    # DB I/O
    # ------------------------------------------------------------------

    async def _persist_weights(self, state: WeightState) -> None:
        import json
        sql = """
            INSERT INTO adaptive_weights (weights, win_rate, avg_pnl, sample_n)
            VALUES ($1::jsonb, $2, $3, $4)
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    sql,
                    json.dumps(state.weights),
                    state.win_rate,
                    state.avg_pnl,
                    state.sample_n,
                )
        except Exception as exc:
            log.error("DB_CONNECT_FAIL", f"persist weights failed: {exc}", db_status="DOWN")

    async def _load_latest_weights(self) -> None:
        import json
        sql = "SELECT weights, win_rate, avg_pnl, sample_n FROM adaptive_weights ORDER BY id DESC LIMIT 1"
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(sql)
            if row:
                loaded = json.loads(row["weights"])
                # Merge with base (new keys added in code updates are preserved)
                merged = deepcopy(BASE_WEIGHTS)
                merged.update({k: v for k, v in loaded.items() if k in merged})
                state = WeightState(
                    weights=merged,
                    win_rate=float(row["win_rate"] or 0),
                    avg_pnl=float(row["avg_pnl"]  or 0),
                    sample_n=int(row["sample_n"]  or 0),
                    updated_at=time.time(),
                )
                state.normalise()
                self._state = state
                log.info(
                    "PERFORMANCE_LOGGED",
                    f"adaptive weights restored from DB: {_fmt_weights(state.weights)}",
                )
        except Exception as exc:
            log.warning(
                "PERFORMANCE_LOGGED",
                f"could not load adaptive weights, using defaults: {exc}",
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nudge(current: float, delta: float) -> float:
    delta = max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA, delta))
    return current + delta


def _fmt_weights(w: Dict[str, float]) -> str:
    return " ".join(f"{k[:4]}={v:.3f}" for k, v in w.items())
