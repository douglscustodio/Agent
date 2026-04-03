"""
scoring.py — Composite 0–100 signal scoring engine

Score bands:
  < 35  → REJECT
  35–59 → WATCHLIST (allow signals >= 35)
  60–74 → VALID
  75+   → HIGH_CONVICTION

Base weights sum to 1.0:
  relative_strength   0.20
  adx_regime          0.15
  rsi_quality         0.15
  funding             0.15
  oi_acceleration     0.15
  bb_squeeze          0.10
  atr_quality         0.10

UPGRADE: Regime-adaptive weights
  TRENDING  → boost adx_regime, relative_strength, atr_quality
  RANGING   → boost bb_squeeze, funding, oi_acceleration
  WEAK      → balanced, slight mean-reversion bias
  Adaptive weights (learned from DB) applied first; regime multipliers on top.

UPGRADE: Expected Value (EV) scoring
  ev_score = P(win) × reward_risk_ratio − (1 − P(win))
  Stored in ScoreResult for ranking tie-breaks.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import numpy as np

from btc_regime import RegimeResult, RelativeStrengthResult, Regime
from derivatives import FundingResult, OIAccelerationResult, LiquidationResult
from filters import FilterResult
from logger import get_logger
from squeeze_detector import detect_squeeze, score_adjustment_for_squeeze

log = get_logger("scoring")


# ---------------------------------------------------------------------------
# Score band enum
# ---------------------------------------------------------------------------

class ScoreBand(str, Enum):
    REJECT          = "REJECT"
    WATCHLIST       = "WATCHLIST"
    VALID           = "VALID"
    HIGH_CONVICTION = "HIGH_CONVICTION"


def classify_score(score: float) -> ScoreBand:
    if score < 35:
        return ScoreBand.REJECT
    elif score < 60:
        return ScoreBand.WATCHLIST
    elif score < 75:
        return ScoreBand.VALID
    else:
        return ScoreBand.HIGH_CONVICTION


# ---------------------------------------------------------------------------
# Sub-score helpers (unchanged)
# ---------------------------------------------------------------------------

def _score_adx_regime(regime: RegimeResult, direction: str) -> float:
    if regime.regime == Regime.RANGING:
        return 50.0   # Afrouxado: 30 -> 50 para permitir mais sinais em range
    base = min(regime.adx * 2.5, 100.0)
    if direction == "LONG"  and regime.trend_direction == "UP":
        base = min(base + 15, 100)
    elif direction == "SHORT" and regime.trend_direction == "DOWN":
        base = min(base + 15, 100)
    elif regime.trend_direction == "NEUTRAL":
        base = max(base - 10, 30)  # Afrouxado
    else:
        base = max(base - 15, 20)  # Afrouxado
    return round(base, 2)


def _score_relative_strength(rs: RelativeStrengthResult, direction: str) -> float:
    score = 50.0
    rs_pct = rs.rs_pct
    if direction == "LONG":
        if rs_pct > 8:     score = 95.0
        elif rs_pct > 4:   score = 80.0
        elif rs_pct > 0:   score = 65.0
        elif rs_pct > -8:  score = 50.0
        else:              score = 30.0
    else:
        if rs_pct < -8:    score = 95.0
        elif rs_pct < -4:  score = 80.0
        elif rs_pct < 0:   score = 65.0
        elif rs_pct < 8:   score = 50.0
        else:              score = 30.0
    return round(score, 2)


def _score_rsi_quality(closes: List[float], direction: str) -> float:
    from filters import _compute_rsi
    rsi = _compute_rsi(closes)
    if direction == "LONG":
        if   45 <= rsi <= 70: return 90.0
        elif 40 <= rsi <  45: return 75.0
        elif 35 <= rsi <  40: return 60.0
        elif 70 <  rsi <= 75: return 55.0
        elif rsi > 75:        return 25.0
        else:                 return 40.0
    else:
        if   30 <= rsi <= 55: return 90.0
        elif 55 <  rsi <= 60: return 75.0
        elif 60 <  rsi <= 65: return 60.0
        elif 25 <= rsi <  30: return 55.0
        elif rsi < 25:        return 25.0
        else:                 return 40.0


def _score_bb_squeeze(closes: List[float]) -> float:
    from filters import _compute_bb_width
    width = _compute_bb_width(closes)
    if   width < 0.03: return 95.0
    elif width < 0.05: return 80.0
    elif width < 0.08: return 65.0
    elif width < 0.10: return 50.0
    elif width < 0.15: return 35.0
    else:              return 20.0


def _score_atr_quality(
    high: List[float], low: List[float], close: List[float], swing_origin: float,
) -> float:
    from filters import _compute_atr, ATR_LATE_ENTRY_MULTIPLIER
    atr = _compute_atr(high, low, close)
    if atr < 1e-10:
        return 50.0
    ratio = abs(close[-1] - swing_origin) / atr
    if   ratio < 0.5:  return 95.0
    elif ratio < 1.0:  return 80.0
    elif ratio < 1.5:  return 65.0
    elif ratio < 2.0:  return 45.0
    else:              return 20.0


# ---------------------------------------------------------------------------
# ScoreResult
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    symbol:     str
    direction:  str
    total:      float
    band:       ScoreBand
    components: Dict[str, float] = field(default_factory=dict)
    liq_risk:   str = "LOW"
    reject_reason: Optional[str] = None
    # UPGRADE: new fields
    dominant_component: str   = ""       # highest weighted-contribution component
    ev_score:           float = 0.0      # expected value estimate
    regime_used:        str   = "UNKNOWN"
    effective_weights:  Dict[str, float] = field(default_factory=dict)

    @property
    def is_tradeable(self) -> bool:
        return self.band in (ScoreBand.VALID, ScoreBand.HIGH_CONVICTION)


# ---------------------------------------------------------------------------
# Base weights
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
# UPGRADE: Regime weight multipliers
#
# TRENDING: trend-following factors are more predictive
# RANGING:  compression + derivatives dominate; trend indicators are noise
# WEAK:     balanced, slight mean-reversion bias
# ---------------------------------------------------------------------------

REGIME_WEIGHT_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    Regime.TRENDING.value: {
        "relative_strength": 1.30,  # momentum leaders matter more in trends
        "adx_regime":        1.40,  # trend strength IS the signal
        "rsi_quality":       1.00,
        "funding":           0.85,  # funding less predictive in strong trends
        "oi_acceleration":   1.10,
        "bb_squeeze":        0.60,  # squeezes less relevant mid-trend
        "atr_quality":       1.25,  # entry timing more critical in trends
    },
    Regime.RANGING.value: {
        "relative_strength": 0.80,  # RS less meaningful in chop
        "adx_regime":        0.50,  # ADX will be low anyway
        "rsi_quality":       1.30,  # RSI mean-reversion more predictive
        "funding":           1.35,  # funding extremes reliably revert in ranges
        "oi_acceleration":   1.30,  # OI build-up often precedes breakout
        "bb_squeeze":        1.45,  # compression → breakout is ranging edge
        "atr_quality":       0.90,
    },
    Regime.WEAK.value: {
        "relative_strength": 1.00,
        "adx_regime":        0.75,
        "rsi_quality":       1.15,
        "funding":           1.15,
        "oi_acceleration":   1.15,
        "bb_squeeze":        1.10,
        "atr_quality":       1.00,
    },
}


def get_effective_weights(
    regime: Optional[Regime] = None,
    adaptive_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Compute effective weights in two passes:
      1. Start from adaptive_weights (learned via DB) or BASE_WEIGHTS
      2. Apply regime multipliers (structural market-phase bias)
      3. Renormalise to sum=1.0

    This keeps two orthogonal signals cleanly separated:
      adaptive_weights  = what has historically worked (trained signal)
      regime_multipliers = what the current market structure favours (contextual bias)
    """
    base = deepcopy(adaptive_weights) if adaptive_weights else deepcopy(WEIGHTS)
    # Merge: ensure all keys exist (guards against schema drift)
    for k in WEIGHTS:
        base.setdefault(k, WEIGHTS[k])

    if regime is not None:
        mults = REGIME_WEIGHT_MULTIPLIERS.get(regime.value, {})
        for k in base:
            base[k] = base[k] * mults.get(k, 1.0)

    total = sum(base.values())
    if total > 1e-10:
        base = {k: round(v / total, 6) for k, v in base.items()}

    return base


# ---------------------------------------------------------------------------
# UPGRADE: Expected Value estimator
#
# EV = P(win) × reward − (1 − P(win)) × risk
# We approximate:
#   P(win)  ≈ score / 100
#   reward  = tp_atr_mult × ATR
#   risk    = sl_atr_mult  × ATR
# Result normalised to 0–100 (50 = break-even).
# ---------------------------------------------------------------------------

def _estimate_ev(
    score: float,
    high:  List[float],
    low:   List[float],
    close: List[float],
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 1.0,
) -> float:
    from filters import _compute_atr
    atr = _compute_atr(high, low, close)
    if atr < 1e-10:
        return 50.0

    p_win  = score / 100.0
    rr     = tp_atr_mult / (sl_atr_mult + 1e-10)
    ev     = p_win * rr - (1.0 - p_win)           # positive = edge
    # Map to 0–100: ev=0 → 50, ev=1 → 75, ev=-1 → 25
    ev_score = round(50.0 + ev * 25.0, 2)
    return max(0.0, min(100.0, ev_score))


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
    adaptive_weights: Optional[Dict[str, float]] = None,   # from AdaptiveEngine
    sector_heat:      float = 50.0,    # UPGRADE: narrative heat score 0–100 (50=neutral)
    vol_confirm_bonus: float = 0.0,    # UPGRADE: from VolumeConfirmResult.score_bonus
) -> ScoreResult:
    """
    Compute composite 0–100 score for a symbol/direction.
    Returns a REJECT band if any hard filter failed.

    Additive bonuses applied AFTER weighted composite (not part of weights):
      sector_heat:      narrative momentum bonus — max ±5 points
      vol_confirm_bonus: volume confirmation bonus — −10 to +5 points
    These are capped so they cannot push a REJECT into VALID.
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
                symbol=symbol, direction=direction, score=0.0,
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

    # UPGRADE: regime-aware weights
    effective_w = get_effective_weights(regime.regime, adaptive_weights)

    total = sum(components[k] * effective_w[k] for k in components)
    total = round(min(max(total, 0.0), 100.0), 2)
    band  = classify_score(total)

    # UPGRADE: dominant component (highest weighted contribution)
    contributions = {k: components[k] * effective_w[k] for k in components}
    dominant = max(contributions, key=contributions.get)

    # UPGRADE: expected value
    ev = _estimate_ev(total, high, low, closes)

    # UPGRADE: apply additive contextual bonuses
    # Sector heat: +5 if very hot (heat=100), -5 if very cold (heat=0)
    heat_bonus = round((sector_heat - 50.0) / 10.0, 2)   # range: -5 to +5
    heat_bonus = max(-5.0, min(5.0, heat_bonus))

    # Volume confirmation: directly from VolumeConfirmResult.score_bonus
    vol_bonus = max(-10.0, min(5.0, vol_confirm_bonus))

    total_pre_bonus = total
    total = round(min(max(total + heat_bonus + vol_bonus, 0.0), 100.0), 2)
    band  = classify_score(total)

    if heat_bonus != 0 or vol_bonus != 0:
        log.info(
            "PERFORMANCE_LOGGED",
            f"contextual bonuses {symbol}: heat={heat_bonus:+.1f} vol={vol_bonus:+.1f} "
            f"score {total_pre_bonus:.1f} → {total:.1f}",
            symbol=symbol,
        )

    liq_risk = liq.risk_level if liq else "LOW"

    if liq_risk == "HIGH" and band == ScoreBand.HIGH_CONVICTION:
        band  = ScoreBand.VALID
        total = min(total, 84.9)
        log.warning(
            "PERFORMANCE_LOGGED",
            f"score downgraded for {symbol}: liq zone HIGH risk",
            symbol=symbol, direction=direction, score=total,
        )

    squeeze = detect_squeeze(
        funding_rate=funding.raw_rate if hasattr(funding, 'raw_rate') else funding.funding_8h if hasattr(funding, 'funding_8h') else None,
        oi_change_pct=oi_accel.change_pct if hasattr(oi_accel, 'change_pct') else None,
        current_price=closes[-1] if closes else 0,
        ath_price=max(closes) if closes else 0,
        position_direction=direction,
    )
    
    # Squeeze only penalizes, doesn't block
    if squeeze.is_squeeze:
        penalty = 15.0  # -15 points penalty
        total = max(0.0, total - penalty)
        log.warning(
            "PERFORMANCE_LOGGED",
            f"SQUEEZE PENALTY for {symbol}: -{penalty}pts → {total:.1f} [{squeeze.recommendation}]",
            symbol=symbol, direction=direction, score=total,
        )
    elif squeeze.danger_level == "MEDIUM":
        penalty = 8.0  # -8 points penalty
        total = max(0.0, total - penalty)
        log.warning(
            "PERFORMANCE_LOGGED",
            f"SQUEEZE WARNING for {symbol}: -{penalty}pts → {total:.1f}",
            symbol=symbol, direction=direction, score=total,
        )
    
    band = classify_score(total)
    
    # Squeeze already penalized above (lines 405-420)
    # Band classification handles rejection if score drops below threshold

    log.info(
        "PERFORMANCE_LOGGED",
        f"score {symbol} ({direction}): {total:.1f} [{band}] "
        f"regime={regime.regime.value} dominant={dominant} ev={ev:.1f}",
        symbol=symbol, direction=direction, score=total,
    )
    return ScoreResult(
        symbol=symbol,
        direction=direction,
        total=total,
        band=band,
        components=components,
        liq_risk=liq_risk,
        dominant_component=dominant,
        ev_score=ev,
        regime_used=regime.regime.value,
        effective_weights=effective_w,
    )
