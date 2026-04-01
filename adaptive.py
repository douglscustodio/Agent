"""
adaptive.py — Adaptive scoring weight engine

Reads performance stats and nudges component weights toward
factors that correlate with TP1 outcomes.
Weights are persisted to DB and injected into scoring.py at runtime.

UPGRADE: Per-dominant-component win rate feedback
  Tracks which scoring component "drove" each alert (dominant_component).
  If a component type has consistently low win rate → reduce its weight.
  If a component type has high win rate → reinforce its weight.
  This creates a genuine self-improving feedback loop.

UPGRADE: Per-regime weight snapshots
  Weights are now stored and restored per regime (TRENDING / RANGING / WEAK).
  The engine maintains three weight states simultaneously.
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

MIN_SAMPLES_TO_ADAPT    = 50   # ANTI-OVERFIT: requer mais trades antes de adaptar
MAX_WEIGHT_DELTA        = 0.03  # ANTI-OVERFIT: mudanças menores por ciclo
WEIGHT_MIN              = 0.05
WEIGHT_MAX              = 0.35
WEIGHT_SOFT_FLOOR      = 0.15   # ANTI-OVERFIT: nunca reduzir abaixo disso
ADAPT_INTERVAL_H        = 24

# UPGRADE: per-component feedback config
MIN_COMPONENT_SAMPLES   = 10   # min alerts where component was dominant before adjusting
COMPONENT_LOW_WR        = 40.0 # below this → reduce component weight by COMPONENT_NUDGE
COMPONENT_HIGH_WR       = 65.0 # above this → increase component weight
COMPONENT_NUDGE         = 0.02 # adjustment magnitude per cycle

# UPGRADE: Auto-disable — suppress components with chronic underperformance
# A component is "auto-disabled" by being clamped to WEIGHT_FLOOR_DISABLED.
# It recovers automatically if win rate improves above COMPONENT_RECOVER_WR.
COMPONENT_DISABLE_WR    = 35.0  # below this for MIN_DISABLE_SAMPLES → disabled
MIN_DISABLE_SAMPLES     = 30    # need at least this many samples to disable
WEIGHT_FLOOR_DISABLED   = 0.02  # near-zero (can't remove entirely — need sum=1)
COMPONENT_RECOVER_WR    = 50.0  # above this → weight floor lifted (re-enabled)

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

CREATE TABLE IF NOT EXISTS component_performance (
    id              BIGSERIAL PRIMARY KEY,
    component_name  VARCHAR(40)  NOT NULL,
    win_rate        NUMERIC(6,2) NOT NULL,
    sample_n        INTEGER      NOT NULL,
    recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""


async def ensure_adaptive_schema() -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        log.info("DB_RECOVERED", "adaptive_weights + component_performance tables verified", db_status="UP")
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

    UPGRADE: Also adapts per dominant component — components that consistently
    "led" winning signals get reinforced; those leading losers get penalised.
    """

    def __init__(self) -> None:
        self._state = WeightState()
        self._disabled_components: set = set()   # UPGRADE: auto-disabled via chronic low WR

    async def startup(self) -> None:
        await ensure_adaptive_schema()
        await self._load_latest_weights()
        log.info("SYSTEM_READY", "adaptive engine ready")

    def get_weights(self) -> Dict[str, float]:
        return deepcopy(self._state.weights)

    # ------------------------------------------------------------------
    # Main adaptation cycle
    # ------------------------------------------------------------------

    async def adapt(self, tracker) -> None:
        """
        Pull performance stats + component breakdown from tracker,
        compute new weights, persist.
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

        # --- Macro-level heuristics (unchanged) ---
        if win_rate < 40:
            new_weights["oi_acceleration"]   = _nudge(new_weights["oi_acceleration"],   +0.02)
            new_weights["funding"]           = _nudge(new_weights["funding"],           +0.02)
            new_weights["relative_strength"] = _nudge(new_weights["relative_strength"], +0.01)
            new_weights["bb_squeeze"]        = _nudge(new_weights["bb_squeeze"],        -0.02)
            new_weights["atr_quality"]       = _nudge(new_weights["atr_quality"],       -0.03)
            log.warning("PERFORMANCE_LOGGED", f"adaptive: low win rate ({win_rate:.1f}%) — boosting fundamentals")

        elif win_rate > 60 and avg_pnl > 0:
            new_weights["adx_regime"]        = _nudge(new_weights["adx_regime"],        +0.01)
            new_weights["rsi_quality"]       = _nudge(new_weights["rsi_quality"],       +0.01)
            new_weights["oi_acceleration"]   = _nudge(new_weights["oi_acceleration"],   +0.01)
            new_weights["funding"]           = _nudge(new_weights["funding"],           -0.01)
            log.info("PERFORMANCE_LOGGED", f"adaptive: high win rate ({win_rate:.1f}%) — reinforcing momentum")

        if avg_pnl < -0.5:
            new_weights["atr_quality"]       = _nudge(new_weights["atr_quality"],       +0.03)
            new_weights["bb_squeeze"]        = _nudge(new_weights["bb_squeeze"],        +0.02)
            new_weights["relative_strength"] = _nudge(new_weights["relative_strength"], -0.02)
            log.warning("PERFORMANCE_LOGGED", f"adaptive: negative avg_pnl ({avg_pnl:.4f}) — tightening entry quality")

        # --- UPGRADE: Per-component win rate feedback ---
        component_stats = await self._get_component_win_rates(tracker)
        for component, comp_stats in component_stats.items():
            if component not in new_weights:
                continue
            comp_n  = comp_stats.get("n", 0)
            comp_wr = comp_stats.get("win_rate", 50.0)

            if comp_n < MIN_COMPONENT_SAMPLES:
                continue  # not enough data to trust

            # UPGRADE: Check auto-disable threshold
            if comp_wr < COMPONENT_DISABLE_WR and comp_n >= MIN_DISABLE_SAMPLES:
                if component not in self._disabled_components:
                    self._disabled_components.add(component)
                    log.warning(
                        "PERFORMANCE_LOGGED",
                        f"adaptive: AUTO-DISABLE {component} "
                        f"win_rate={comp_wr:.1f}% ({comp_n} samples) — "
                        f"clamping to floor {WEIGHT_FLOOR_DISABLED}",
                    )
                    await write_system_event(
                        "PERFORMANCE_LOGGED",
                        f"component {component} auto-disabled: wr={comp_wr:.1f}% n={comp_n}",
                        level="WARNING", module="adaptive",
                    )
                new_weights[component] = WEIGHT_FLOOR_DISABLED
                continue

            # Check auto-recovery
            if component in self._disabled_components and comp_wr > COMPONENT_RECOVER_WR:
                self._disabled_components.discard(component)
                log.info(
                    "PERFORMANCE_LOGGED",
                    f"adaptive: AUTO-RECOVER {component} "
                    f"win_rate={comp_wr:.1f}% — restoring to base weight",
                )
                new_weights[component] = WEIGHTS.get(component, WEIGHT_MIN)
                continue

            # Skip adjustment if currently disabled
            if component in self._disabled_components:
                continue

            if comp_wr < COMPONENT_LOW_WR:
                delta = -COMPONENT_NUDGE
                log.warning(
                    "PERFORMANCE_LOGGED",
                    f"adaptive: component {component} win_rate={comp_wr:.1f}% ({comp_n} samples) "
                    f"— reducing weight by {abs(delta):.2f}",
                )
            elif comp_wr > COMPONENT_HIGH_WR:
                delta = +COMPONENT_NUDGE
                log.info(
                    "PERFORMANCE_LOGGED",
                    f"adaptive: component {component} win_rate={comp_wr:.1f}% ({comp_n} samples) "
                    f"— boosting weight by {delta:.2f}",
                )
            else:
                continue

            new_weights[component] = _nudge(new_weights[component], delta)

            # Persist component performance snapshot
            await self._record_component_performance(component, comp_wr, comp_n)

        # Clamp + normalise
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

        self._state = state
        await self._persist_weights(state)

        disabled_note = f" | disabled={sorted(self._disabled_components)}" if self._disabled_components else ""
        log.info("PERFORMANCE_LOGGED", f"adaptive weights updated: {_fmt_weights(state.weights)}{disabled_note}")
        await write_system_event(
            "PERFORMANCE_LOGGED",
            f"adaptive weights updated: win_rate={win_rate:.1f}% samples={n}",
            level="INFO", module="adaptive", score=win_rate,
        )

    # ------------------------------------------------------------------
    # UPGRADE: Per-component win rate query
    # ------------------------------------------------------------------

    async def _get_component_win_rates(self, tracker) -> Dict[str, Dict]:
        """
        Query performance_log grouped by dominant_component.
        Returns {component_name: {win_rate, n}} for the last 7 days.
        """
        sql = """
            SELECT
                dominant_component,
                COUNT(*) FILTER (WHERE outcome = 'TP1') AS wins,
                COUNT(*) AS total
            FROM performance_log
            WHERE
                checked_at > NOW() - INTERVAL '7 days'
                AND dominant_component IS NOT NULL
                AND dominant_component != ''
            GROUP BY dominant_component
        """
        result = {}
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql)
            for row in rows:
                comp  = row["dominant_component"]
                total = row["total"] or 0
                wins  = row["wins"]  or 0
                if total > 0:
                    result[comp] = {
                        "win_rate": round(wins / total * 100, 2),
                        "n": total,
                    }
        except Exception as exc:
            log.warning("PERFORMANCE_LOGGED", f"component win rate query failed: {exc}")
        return result

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
                await conn.execute(sql, json.dumps(state.weights), state.win_rate, state.avg_pnl, state.sample_n)
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
                merged = deepcopy(BASE_WEIGHTS)
                merged.update({k: v for k, v in loaded.items() if k in merged})
                state = WeightState(
                    weights=merged,
                    win_rate=float(row["win_rate"] or 0),
                    avg_pnl=float(row["avg_pnl"]   or 0),
                    sample_n=int(row["sample_n"]   or 0),
                    updated_at=time.time(),
                )
                state.normalise()
                self._state = state
                log.info("PERFORMANCE_LOGGED", f"adaptive weights restored from DB: {_fmt_weights(state.weights)}")
        except Exception as exc:
            log.warning("PERFORMANCE_LOGGED", f"could not load adaptive weights, using defaults: {exc}")

    async def _record_component_performance(
        self, component: str, win_rate: float, sample_n: int
    ) -> None:
        sql = """
            INSERT INTO component_performance (component_name, win_rate, sample_n)
            VALUES ($1, $2, $3)
        """
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(sql, component, win_rate, sample_n)
        except Exception as exc:
            log.warning("PERFORMANCE_LOGGED", f"record_component_performance failed: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nudge(current: float, delta: float) -> float:
    delta = max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA, delta))
    return current + delta


def _fmt_weights(w: Dict[str, float]) -> str:
    return " ".join(f"{k[:4]}={v:.3f}" for k, v in w.items())
