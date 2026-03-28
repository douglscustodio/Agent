"""
filters.py — Pre-scoring hard filters
All filters return FilterResult(passed: bool, reason: str, value: float).
A symbol that fails ANY hard filter is rejected before scoring.
Filters:
  1. RSI overbought/oversold
  2. Bollinger Band width anti-FOMO
  3. ATR late-entry gate
  4. Liquidity (volume) gate
"""

from dataclasses import dataclass
from typing import List, Optional

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
RSI_LONG_MIN    = 40.0   # Longs only valid above this RSI (not in freefall)
RSI_SHORT_MAX   = 60.0   # Shorts only valid below this RSI


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
    rs  = avg_gain / avg_loss
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
    else:  # SHORT
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

BB_WIDTH_FOMO_THRESHOLD = 0.08   # BB width > 8% of price → already expanded, FOMO risk
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
    return (upper - lower) / mid   # normalised width


def filter_bb_width(
    closes: List[float],
    symbol: str = "",
) -> FilterResult:
    """
    Reject entries when Bollinger Band width is already expanded (FOMO).
    A wide band means the move is already extended — late entry risk is high.
    """
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

ATR_LATE_ENTRY_MULTIPLIER = 1.5   # if price moved > 1.5× ATR from last swing → late entry


def _compute_atr(
    high: List[float],
    low:  List[float],
    close: List[float],
    period: int = 14,
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
    """
    Reject if price has already moved > ATR_LATE_ENTRY_MULTIPLIER × ATR
    from the swing origin (e.g. breakout candle close).
    """
    atr = _compute_atr(high, low, close, period)
    if atr < 1e-10:
        return FilterResult(True, "ATR_LATE_ENTRY", 0.0, 0.0, "ATR unavailable, skip")

    current_price  = close[-1]
    price_move     = abs(current_price - swing_origin_price)
    max_allowed    = ATR_LATE_ENTRY_MULTIPLIER * atr

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

LIQUIDITY_MIN_VOLUME_USD = 1_000_000.0   # minimum 24h volume in USD


def filter_liquidity(
    volume_24h_usd: float,
    symbol: str = "",
    min_volume_usd: float = LIQUIDITY_MIN_VOLUME_USD,
) -> FilterResult:
    """
    Reject illiquid markets where slippage would destroy any edge.
    """
    if volume_24h_usd < min_volume_usd:
        reason = (
            f"24h volume ${volume_24h_usd:,.0f} < minimum ${min_volume_usd:,.0f} — illiquid"
        )
        log.warning("PERFORMANCE_LOGGED", f"liquidity filter FAIL {symbol}: {reason}", symbol=symbol)
        return FilterResult(False, "LIQUIDITY", volume_24h_usd, min_volume_usd, reason)

    return FilterResult(True, "LIQUIDITY", volume_24h_usd, min_volume_usd,
                        f"liquidity OK (${volume_24h_usd:,.0f})")


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
) -> tuple[bool, List[FilterResult]]:
    """
    Run all hard filters in order. Returns (all_passed, results_list).
    Stops on first failure (fast-path rejection).
    """
    results: List[FilterResult] = []

    checks = [
        lambda: filter_liquidity(volume_24h_usd, symbol),
        lambda: filter_rsi(closes, direction, symbol=symbol),
        lambda: filter_bb_width(closes, symbol),
        lambda: filter_atr_late_entry(high, low, closes, swing_origin_price, direction, symbol=symbol),
    ]

    for check in checks:
        result = check()
        results.append(result)
        if not result.passed:
            log.warning(
                "PERFORMANCE_LOGGED",
                f"filter chain REJECTED {symbol} at {result.filter_name}: {result.reason}",
                symbol=symbol,
            )
            return False, results

    log.debug(
        "PERFORMANCE_LOGGED",
        f"all filters PASSED for {symbol}",
        symbol=symbol,
    )
    return True, results
