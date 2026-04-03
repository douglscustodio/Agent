"""
freshness.py — Data freshness validation
Rejects stale candles / snapshots before they reach scoring.
"""

import time
from dataclasses import dataclass
from typing import Optional

from logger import get_logger

log = get_logger("freshness")

# Maximum age in seconds before data is considered stale
MAX_CANDLE_AGE_S: int   = 120   # 2 minutes
MAX_SNAPSHOT_AGE_S: int = 30    # 30 seconds (derivatives tick data)


@dataclass
class FreshnessResult:
    is_fresh: bool
    age_seconds: float
    reason: Optional[str] = None


def check_candle_freshness(last_close_ts: float, symbol: str = "") -> FreshnessResult:
    """
    Validate that the most recent candle close timestamp is within MAX_CANDLE_AGE_S.
    last_close_ts: unix epoch seconds (float)
    """
    age = time.time() - last_close_ts
    if age > MAX_CANDLE_AGE_S:
        reason = f"candle is {age:.0f}s old (max {MAX_CANDLE_AGE_S}s)"
        log.warning(
            "PERFORMANCE_LOGGED",
            f"stale candle rejected: {reason}",
            symbol=symbol,
        )
        return FreshnessResult(is_fresh=False, age_seconds=age, reason=reason)
    return FreshnessResult(is_fresh=True, age_seconds=age)


def check_snapshot_freshness(snapshot_ts: float, symbol: str = "") -> FreshnessResult:
    """
    Validate that a derivatives snapshot (funding, OI) is within MAX_SNAPSHOT_AGE_S.
    """
    age = time.time() - snapshot_ts
    if age > MAX_SNAPSHOT_AGE_S:
        reason = f"snapshot is {age:.0f}s old (max {MAX_SNAPSHOT_AGE_S}s)"
        log.warning(
            "PERFORMANCE_LOGGED",
            f"stale snapshot rejected: {reason}",
            symbol=symbol,
        )
        return FreshnessResult(is_fresh=False, age_seconds=age, reason=reason)
    return FreshnessResult(is_fresh=True, age_seconds=age)
