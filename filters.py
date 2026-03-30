"""
filters.py — Pre-scoring hard filters

All filters return FilterResult(passed: bool, reason: str, value: float).
A symbol that fails ANY hard filter is rejected before scoring.

Filters:
  1. RSI overbought/oversold
  2. Bollinger Band width anti-FOMO
  3. ATR late-entry gate
  4. Liquidity (volume) gate
  5. [UPGRADE] Volume confirmation — micro entry timing

UPGRADE: Volume Confirmation Filter
  Detects whether the most recent candle has above-average volume.
  A setup with compression (BB squeeze) + no volume spike is pre-breakout:
    → PASS (watch it, don't chase yet)
  A setup with expanding volume on the breakout candle:
    → STRONG CONFIRM (highest confidence entries)
  A setup where price moved but volume is declining:
    → SOFT WARN (annotated, not hard rejected — but scored lower)

  This filter does NOT hard-reject entries on low volume alone (that would
  filter valid mean-reversion setups). Instead it returns a VolumeConfirmResult
  which scoring.py uses to apply a conditional bonus.

  Hard rejection only if: volume is > 3× average AND it's against the direction
  (e.g., massive down-candle volume on a LONG setup = absorption / distribution).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from logger import get_logger

log = get_logger("filters")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FilterResult:
    passed: bool
    filter_name: str
    value: float
    threshold: float
    reason: str


# ---------------------------------------------------------------------------
# 1. RSI Filter
# ---------------------------------------------------------------------------

RSI_OVERBOUGHT  = 75.0
RSI_OVERSOLD    = 25.0
RSI_LONG_MIN    = 40.0
RSI_SHORT_MAX   = 60.0


def _compute_rsi(closes: List[float], period: int = 14) -> float:
    c = np.array(closes, dtype=float)
    if len(c) < period + 1:
        return 50.0
    deltas = np.diff(c)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i])  / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 4)


def filter_rsi(
    closes: List[float],
    direction: str = "LONG",
    period: int = 14,
    symbol: str = "",
) -> FilterResult:
    rsi = _compute_rsi(closes, period)
    direction = direction.upper()

    if direction == "LONG":
        if rsi >= RSI_OVERBOUGHT:
            reason = f"RSI {rsi:.1f} >= overbought threshold {RSI_OVERBOUGHT}"
            log.warning("PERFORMANCE_LOGGED", f"RSI filter FAIL {symbol}: {reason}", symbol=symbol)
            return FilterResult(False, "RSI", rsi, RSI_OVERBOUGHT, reason)
        if rsi < RSI_LONG_MIN:
            reason = f"RSI {rsi:.1f} < LONG minimum {RSI_LONG_MIN} (freefall)"
            log.warning("PERFORMANCE_LOGGED", f"RSI filter FAIL {symbol}: {reason}", symbol=symbol)
            return FilterResult(False, "RSI", rsi, RSI_LONG_MIN, reason)
    else:
        if rsi <= RSI_OVERSOLD:
            reason = f"RSI {rsi:.1f} <= oversold threshold {RSI_OVERSOLD}"
            log.warning("PERFORMANCE_LOGGED", f"RSI filter FAIL {symbol}: {reason}", symbol=symbol)
            return FilterResult(False, "RSI", rsi, RSI_OVERSOLD, reason)
        if rsi > RSI_SHORT_MAX:
            reason = f"RSI {rsi:.1f} > SHORT maximum {RSI_SHORT_MAX}"
            log.warning("PERFORMANCE_LOGGED", f"RSI filter FAIL {symbol}: {reason}", symbol=symbol)
            return FilterResult(False, "RSI", rsi, RSI_SHORT_MAX, reason)

    return FilterResult(True, "RSI", rsi, 0.0, f"RSI {rsi:.1f} OK for {direction}")


# ---------------------------------------------------------------------------
# 2. Bollinger Band Width Anti-FOMO Filter
# ---------------------------------------------------------------------------

BB_WIDTH_FOMO_THRESHOLD = 0.12   # 0.08 era muito restritivo para crypto; 12% é o real FOMO
BB_PERIOD = 20
BB_STD    = 2.0


def _compute_bb_width(closes: List[float], period: int = BB_PERIOD, std: float = BB_STD) -> float:
    c = np.array(closes[-period:], dtype=float)
    if len(c) < period:
        return 0.0
    mid   = np.mean(c)
    sigma = np.std(c, ddof=0)
    upper = mid + std * sigma
    lower = mid - std * sigma
    if mid < 1e-10:
        return 0.0
    return (upper - lower) / mid


def filter_bb_width(closes: List[float], symbol: str = "") -> FilterResult:
    width = _compute_bb_width(closes)
    if width > BB_WIDTH_FOMO_THRESHOLD:
        reason = f"BB width {width:.4f} > FOMO threshold {BB_WIDTH_FOMO_THRESHOLD} (move already extended)"
        log.warning("PERFORMANCE_LOGGED", f"BB width filter FAIL {symbol}: {reason}", symbol=symbol)
        return FilterResult(False, "BB_WIDTH", width, BB_WIDTH_FOMO_THRESHOLD, reason)
    return FilterResult(True, "BB_WIDTH", round(width, 6), BB_WIDTH_FOMO_THRESHOLD,
                        f"BB width {width:.4f} OK")


# ---------------------------------------------------------------------------
# 3. ATR Late-Entry Filter
# ---------------------------------------------------------------------------

ATR_LATE_ENTRY_MULTIPLIER = 2.0   # 1.5 era muito restritivo; 2.0 ATR de folga


def _compute_atr(
    high: List[float], low: List[float], close: List[float], period: int = 14,
) -> float:
    h = np.array(high,  dtype=float)
    l = np.array(low,   dtype=float)
    c = np.array(close, dtype=float)
    if len(c) < 2:
        return 0.0
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return float(np.mean(tr[-period:]))


def filter_atr_late_entry(
    high:  List[float],
    low:   List[float],
    close: List[float],
    swing_origin_price: float,
    direction: str = "LONG",
    period: int = 14,
    symbol: str = "",
) -> FilterResult:
    atr = _compute_atr(high, low, close, period)
    if atr < 1e-10:
        return FilterResult(True, "ATR_LATE_ENTRY", 0.0, 0.0, "ATR unavailable, skip")

    current_price = close[-1]
    price_move    = abs(current_price - swing_origin_price)
    max_allowed   = ATR_LATE_ENTRY_MULTIPLIER * atr

    if price_move > max_allowed:
        reason = (
            f"price moved {price_move:.4f} > {ATR_LATE_ENTRY_MULTIPLIER}×ATR={max_allowed:.4f} "
            f"from swing origin {swing_origin_price:.4f} — late entry"
        )
        log.warning("PERFORMANCE_LOGGED", f"ATR filter FAIL {symbol}: {reason}", symbol=symbol)
        return FilterResult(False, "ATR_LATE_ENTRY", round(price_move, 6), round(max_allowed, 6), reason)

    return FilterResult(True, "ATR_LATE_ENTRY", round(price_move, 6), round(max_allowed, 6),
                        f"ATR late-entry OK (move={price_move:.4f} ATR={atr:.4f})")


# ---------------------------------------------------------------------------
# 4. Liquidity Filter
# ---------------------------------------------------------------------------

LIQUIDITY_MIN_VOLUME_USD = 1_000_000.0


def filter_liquidity(
    volume_24h_usd: float,
    symbol: str = "",
    min_volume_usd: float = LIQUIDITY_MIN_VOLUME_USD,
) -> FilterResult:
    if volume_24h_usd < min_volume_usd:
        reason = f"24h volume ${volume_24h_usd:,.0f} < minimum ${min_volume_usd:,.0f} — illiquid"
        log.warning("PERFORMANCE_LOGGED", f"liquidity filter FAIL {symbol}: {reason}", symbol=symbol)
        return FilterResult(False, "LIQUIDITY", volume_24h_usd, min_volume_usd, reason)
    return FilterResult(True, "LIQUIDITY", volume_24h_usd, min_volume_usd,
                        f"liquidity OK (${volume_24h_usd:,.0f})")


# ---------------------------------------------------------------------------
# 5. UPGRADE: Volume Confirmation Filter
#
# Concept: compare the last candle's volume to the rolling average.
#   ratio = last_volume / avg_volume(lookback)
#
#   ratio > CONFIRM_THRESHOLD         → CONFIRMED  (strong participation)
#   ratio > SOFT_THRESHOLD            → SOFT        (above average, OK)
#   ratio < SOFT_THRESHOLD            → QUIET       (no confirmation yet)
#   ratio > DISTRIBUTION_THRESHOLD
#     AND candle is against direction  → HARD FAIL   (distribution / absorption)
#
# Hard fail only on clear distribution (massive volume against direction).
# QUIET is not a rejection — it's a signal quality indicator used by scorer.
# ---------------------------------------------------------------------------

VOLUME_CONFIRM_LOOKBACK      = 20      # candles for rolling average
VOLUME_CONFIRM_THRESHOLD     = 1.5    # ratio: 1.5× avg = confirmed
VOLUME_SOFT_THRESHOLD        = 0.9    # ratio: at least 90% of avg
VOLUME_DISTRIBUTION_THRESHOLD = 2.5   # ratio: 2.5× avg against direction = distribution


@dataclass
class VolumeConfirmResult:
    """Returned alongside FilterResult — provides scoring context."""
    label: str        # "CONFIRMED" | "SOFT" | "QUIET" | "DISTRIBUTION"
    ratio: float      # last_volume / avg_volume
    score_bonus: float  # additive bonus for scoring (negative = penalty)


def _compute_volume_ratio(volumes: List[float], lookback: int = VOLUME_CONFIRM_LOOKBACK) -> float:
    """Ratio of last candle volume to rolling average of previous N candles."""
    if len(volumes) < lookback + 1:
        return 1.0  # neutral if not enough data
    avg = np.mean(volumes[-lookback - 1:-1])   # exclude last candle
    if avg < 1e-3:
        return 1.0
    return float(volumes[-1]) / avg


def _is_bearish_candle(closes: List[float], opens: List[float]) -> bool:
    """Returns True if the last candle closed lower than it opened."""
    if not opens or len(opens) < 1:
        return False
    return closes[-1] < opens[-1]


def filter_volume_confirmation(
    closes:    List[float],
    volumes:   List[float],
    opens:     Optional[List[float]] = None,
    direction: str = "LONG",
    symbol:    str = "",
    lookback:  int = VOLUME_CONFIRM_LOOKBACK,
) -> Tuple[FilterResult, VolumeConfirmResult]:
    """
    Assess volume confirmation quality for the current bar.

    Returns (FilterResult, VolumeConfirmResult).
    FilterResult.passed is False ONLY on clear distribution signals.
    VolumeConfirmResult carries the score bonus (+/−) for the scorer.
    """
    direction = direction.upper()

    if len(volumes) < lookback + 1:
        vol_result = VolumeConfirmResult(label="QUIET", ratio=1.0, score_bonus=0.0)
        return FilterResult(True, "VOLUME_CONFIRM", 1.0, VOLUME_CONFIRM_THRESHOLD,
                            "insufficient volume history — neutral"), vol_result

    ratio = _compute_volume_ratio(volumes, lookback)

    # Detect if last candle moved against direction with extreme volume
    bearish = _is_bearish_candle(closes, opens or closes)
    against_direction = (direction == "LONG" and bearish) or (direction == "SHORT" and not bearish)

    # Hard fail: distribution / absorption
    if ratio > VOLUME_DISTRIBUTION_THRESHOLD and against_direction:
        reason = (
            f"volume {ratio:.2f}× avg on candle AGAINST direction ({direction}) "
            f"— distribution / absorption signal"
        )
        log.warning("PERFORMANCE_LOGGED", f"volume DISTRIBUTION FAIL {symbol}: {reason}", symbol=symbol)
        vol_result = VolumeConfirmResult(label="DISTRIBUTION", ratio=round(ratio, 3), score_bonus=-10.0)
        return FilterResult(False, "VOLUME_CONFIRM", round(ratio, 3),
                            VOLUME_DISTRIBUTION_THRESHOLD, reason), vol_result

    # Classify quality
    if ratio >= VOLUME_CONFIRM_THRESHOLD:
        label      = "CONFIRMED"
        bonus      = +5.0    # strong participation on breakout candle
        log.info("PERFORMANCE_LOGGED",
                 f"volume CONFIRMED {symbol}: {ratio:.2f}× avg — high conviction entry", symbol=symbol)
    elif ratio >= VOLUME_SOFT_THRESHOLD:
        label = "SOFT"
        bonus = +2.0    # above average, acceptable
    else:
        label = "QUIET"
        bonus = -3.0    # below average — setup forming but not confirmed yet

    vol_result = VolumeConfirmResult(label=label, ratio=round(ratio, 3), score_bonus=bonus)
    return FilterResult(True, "VOLUME_CONFIRM", round(ratio, 3), VOLUME_CONFIRM_THRESHOLD,
                        f"volume {label} ({ratio:.2f}× avg)"), vol_result


# ---------------------------------------------------------------------------
# Composite filter runner
# ---------------------------------------------------------------------------

def run_all_filters(
    closes:              List[float],
    high:                List[float],
    low:                 List[float],
    volume_24h_usd:      float,
    swing_origin_price:  float,
    direction:           str = "LONG",
    symbol:              str = "",
    # UPGRADE: optional per-candle volume data for micro confirmation
    volumes:             Optional[List[float]] = None,
    opens:               Optional[List[float]] = None,
) -> Tuple[bool, List[FilterResult], Optional[VolumeConfirmResult]]:
    """
    Run all hard filters in order. Returns (all_passed, results_list, vol_confirm).
    Stops on first HARD failure.

    UPGRADE: Returns VolumeConfirmResult as third element for use by scorer.
    Volume confirmation is evaluated last — only after all hard filters pass.
    """
    results: List[FilterResult] = []
    vol_confirm: Optional[VolumeConfirmResult] = None

    hard_checks = [
        lambda: filter_liquidity(volume_24h_usd, symbol),
        lambda: filter_rsi(closes, direction, symbol=symbol),
        lambda: filter_bb_width(closes, symbol),
        lambda: filter_atr_late_entry(high, low, closes, swing_origin_price, direction, symbol=symbol),
    ]

    for check in hard_checks:
        result = check()
        results.append(result)
        if not result.passed:
            log.warning(
                "PERFORMANCE_LOGGED",
                f"filter chain REJECTED {symbol} at {result.filter_name}: {result.reason}",
                symbol=symbol,
            )
            return False, results, None

    # UPGRADE: volume confirmation (soft — never hard-fails unless distribution)
    if volumes:
        vol_filter, vol_confirm = filter_volume_confirmation(
            closes, volumes, opens, direction, symbol
        )
        results.append(vol_filter)
        if not vol_filter.passed:
            # Distribution detected — hard reject
            log.warning(
                "PERFORMANCE_LOGGED",
                f"filter chain REJECTED {symbol} at VOLUME_CONFIRM: {vol_filter.reason}",
                symbol=symbol,
            )
            return False, results, vol_confirm

    log.debug("PERFORMANCE_LOGGED", f"all filters PASSED for {symbol}", symbol=symbol)
    return True, results, vol_confirm
