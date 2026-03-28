"""
scoring.py — Composite 0–100 signal scoring engine

Score bands:
  < 60  → REJECT
  60–74 → WATCHLIST
  75–84 → VALID
  85+   → HIGH_CONVICTION

Weights sum to 1.0:
  relative_strength   0.20
  adx_regime          0.15
  rsi_quality         0.15
  funding             0.15
  oi_acceleration     0.15
  bb_squeeze          0.10
  atr_quality         0.10
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import numpy as np

from btc_regime import RegimeResult, RelativeStrengthResult, Regime
from derivatives import FundingResult, OIAccelerationResult, LiquidationResult
from filters import FilterResult
from logger import get_logger

log = get_logger("scoring")


# ---------------------------------------------------------------------------
# Score band enum
# ---------------------------------------------------------------------------

class ScoreBand(str, Enum):
    REJECT          = "REJECT"           # < 60
    WATCHLIST       = "WATCHLIST"        # 60–74
    VALID           = "VALID"            # 75–84
    HIGH_CONVICTION = "HIGH_CONVICTION"  # 85+


def classify_score(score: float) -> ScoreBand:
    if score < 60:
        return ScoreBand.REJECT
    elif score < 75:
        return ScoreBand.WATCHLIST
    elif score < 85:
        return ScoreBand.VALID
    else:
        return ScoreBand.HIGH_CONVICTION


# ---------------------------------------------------------------------------
# Sub-score helpers
# ---------------------------------------------------------------------------

def _score_adx_regime(regime: RegimeResult, direction: str) -> float:
    """ADX contributes more when trend is confirmed in intended direction."""
    if regime.regime == Regime.RANGING:
        return 30.0   # ranging: weak signal environment

    base = min(regime.adx * 2.5, 100.0)   # ADX 40 → 100, ADX 25 → 62.5

    # Direction alignment bonus
    if direction == "LONG"  and regime.trend_direction == "UP":
        base = min(base + 15, 100)
    elif direction == "SHORT" and regime.trend_direction == "DOWN":
        base = min(base + 15, 100)
    elif regime.trend_direction == "NEUTRAL":
        pass
    else:
        base = max(base - 20, 0)   # against trend

    return round(base, 2)


def _score_relative_strength(rs: RelativeStrengthResult, direction: str) -> float:
    """RS vs BTC: outperforming is bullish, underperforming is bearish."""
    score = 50.0
    rs_pct = rs.rs_pct

    if direction == "LONG":
        if rs_pct > 10:    score = 95.0
        elif rs_pct > 5:   score = 80.0
        elif rs_pct > 0:   score = 65.0
        elif rs_pct > -5:  score = 45.0
        else:              score = 20.0
    else:  # SHORT
        if rs_pct < -10:   score = 95.0
        elif rs_pct < -5:  score = 80.0
        elif rs_pct < 0:   score = 65.0
        elif rs_pct < 5:   score = 45.0
        else:              score = 20.0

    return round(score, 2)


def _score_rsi_quality(closes: List[float], direction: str) -> float:
    """RSI quality: ideal zones for entry vs momentum confirmation."""
    from filters import _compute_rsi
    rsi = _compute_rsi(closes)

    if direction == "LONG":
        # Sweet spot: 45–65 (not overbought, momentum present)
        if   50 <= rsi <= 65: return 90.0
        elif 45 <= rsi <  50: return 75.0
        elif 40 <= rsi <  45: return 60.0
        elif 65 <  rsi <= 70: return 55.0
        elif rsi > 70:        return 25.0
        else:                 return 35.0
    else:  # SHORT
        if   35 <= rsi <= 50: return 90.0
        elif 50 <  rsi <= 55: return 75.0
        elif 55 <  rsi <= 60: return 60.0
        elif 30 <= rsi <  35: return 55.0
        elif rsi < 30:        return 25.0
        else:                 return 35.0


def _score_bb_squeeze(closes: List[float]) -> float:
    """
    Bollinger squeeze = low width → compression before breakout.
    Opposite of the FOMO filter: here we reward tightness.
    """
    from filters import _compute_bb_width
    width = _compute_bb_width(closes)

    if   width < 0.02: return 95.0   # extreme squeeze
    elif width < 0.04: return 80.0
    elif width < 0.06: return 65.0
    elif width < 0.08: return 50.0
    elif width < 0.12: return 35.0
    else:              return 15.0   # fully expanded, chasing


def _score_atr_quality(
    high: List[float],
    low:  List[float],
    close: List[float],
    swing_origin: float,
) -> float:
    """ATR quality: reward entries close to swing origin (early, not late)."""
    from filters import _compute_atr, ATR_LATE_ENTRY_MULTIPLIER
    atr = _compute_atr(high, low, close)
    if atr < 1e-10:
        return 50.0
    price_move  = abs(close[-1] - swing_origin)
    ratio       = price_move / atr

    if   ratio < 0.3:  return 95.0   # very early entry
    elif ratio < 0.7:  return 80.0
    elif ratio < 1.0:  return 65.0
    elif ratio < 1.5:  return 45.0
    else:              return 15.0   # very late


# ---------------------------------------------------------------------------
# Main ScoreResult
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    symbol:     str
    direction:  str
    total:      float           # 0–100 composite score
    band:       ScoreBand
    components: Dict[str, float] = field(default_factory=dict)
    liq_risk:   str = "LOW"
    reject_reason: Optional[str] = None

    @property
    def is_tradeable(self) -> bool:
        return self.band in (ScoreBand.VALID, ScoreBand.HIGH_CONVICTION)


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

WEIGHTS: Dict[str, float] = {
    "relative_strength": 0.20,
    "adx_regime":        0.15,
    "rsi_quality":       0.15,
    "funding":           0.15,
    "oi_acceleration":   0.15,
    "bb_squeeze":        0.10,
    "atr_quality":       0.10,
}

assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------

def compute_score(
    symbol:           str,
    direction:        str,
    closes:           List[float],
    high:             List[float],
    low:              List[float],
    swing_origin:     float,
    regime:           RegimeResult,
    rs:               RelativeStrengthResult,
    funding:          FundingResult,
    oi_accel:         OIAccelerationResult,
    liq:              Optional[LiquidationResult] = None,
    filter_results:   Optional[List[FilterResult]] = None,
) -> ScoreResult:
    """
    Compute composite 0–100 score for a symbol/direction.
    Returns a REJECT band if any hard filter failed.
    """
    direction = direction.upper()

    # Hard filter gate
    if filter_results:
        failed = [f for f in filter_results if not f.passed]
        if failed:
            reason = f"{failed[0].filter_name}: {failed[0].reason}"
            log.info(
                "PERFORMANCE_LOGGED",
                f"REJECT {symbol} ({direction}) — filter fail: {reason}",
                symbol=symbol,
                direction=direction,
                score=0.0,
            )
            return ScoreResult(
                symbol=symbol, direction=direction,
                total=0.0, band=ScoreBand.REJECT,
                reject_reason=reason,
            )

    components: Dict[str, float] = {
        "relative_strength": _score_relative_strength(rs, direction),
        "adx_regime":        _score_adx_regime(regime, direction),
        "rsi_quality":       _score_rsi_quality(closes, direction),
        "funding":           funding.score,
        "oi_acceleration":   oi_accel.score,
        "bb_squeeze":        _score_bb_squeeze(closes),
        "atr_quality":       _score_atr_quality(high, low, closes, swing_origin),
    }

    total = sum(components[k] * WEIGHTS[k] for k in components)
    total = round(min(max(total, 0.0), 100.0), 2)
    band  = classify_score(total)

    liq_risk = liq.risk_level if liq else "LOW"

    # Downgrade HIGH_CONVICTION if liquidation zone is very close
    if liq_risk == "HIGH" and band == ScoreBand.HIGH_CONVICTION:
        band  = ScoreBand.VALID
        total = min(total, 84.9)
        log.warning(
            "PERFORMANCE_LOGGED",
            f"score downgraded for {symbol}: liq zone HIGH risk",
            symbol=symbol,
            direction=direction,
            score=total,
        )

    log.info(
        "PERFORMANCE_LOGGED",
        f"score {symbol} ({direction}): {total:.1f} [{band}]",
        symbol=symbol,
        direction=direction,
        score=total,
    )
    return ScoreResult(
        symbol=symbol,
        direction=direction,
        total=total,
        band=band,
        components=components,
        liq_risk=liq_risk,
    )
