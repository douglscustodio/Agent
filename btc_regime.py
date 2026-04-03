"""
btc_regime.py — BTC macro-regime detection
Computes:
  - ADX-based trend regime (TRENDING / RANGING / WEAK)
  - Relative strength of any coin vs BTC
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import numpy as np

from logger import get_logger

log = get_logger("btc_regime")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Regime(str, Enum):
    TRENDING = "TRENDING"   # ADX >= 25
    RANGING  = "RANGING"    # ADX < 20
    WEAK     = "WEAK"       # 20 <= ADX < 25


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RegimeResult:
    regime: Regime
    adx: float
    plus_di: float
    minus_di: float
    trend_direction: str        # "UP" | "DOWN" | "NEUTRAL"


@dataclass
class RelativeStrengthResult:
    symbol: str
    rs_score: float             # > 1.0 outperforming BTC, < 1.0 underperforming
    rs_pct:   float             # percentage difference vs BTC return
    outperforming: bool


# ---------------------------------------------------------------------------
# ADX computation (Wilder's smoothing, no external TA-Lib dependency)
# ---------------------------------------------------------------------------

def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
    )
    return tr


def _wilder_smooth(series: np.ndarray, period: int) -> np.ndarray:
    result = np.zeros_like(series)
    result[period - 1] = series[:period].sum()
    for i in range(period, len(series)):
        result[i] = result[i - 1] - result[i - 1] / period + series[i]
    return result


def compute_adx(
    high: List[float],
    low:  List[float],
    close: List[float],
    period: int = 14,
) -> RegimeResult:
    """
    Compute ADX, +DI, -DI using Wilder smoothing.
    Minimum required candles: period * 2 + 1
    """
    h = np.array(high,  dtype=float)
    l = np.array(low,   dtype=float)
    c = np.array(close, dtype=float)

    n = len(c)
    if n < period * 2 + 1:
        log.warning(
            "PERFORMANCE_LOGGED",
            f"insufficient candles for ADX ({n} < {period * 2 + 1}), defaulting RANGING",
        )
        return RegimeResult(
            regime=Regime.RANGING, adx=0.0,
            plus_di=0.0, minus_di=0.0, trend_direction="NEUTRAL",
        )

    tr = _true_range(h, l, c)

    up_move   = h[1:] - h[:-1]
    down_move = l[:-1] - l[1:]

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move,  0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_s        = _wilder_smooth(tr[1:],      period)
    plus_dm_s   = _wilder_smooth(plus_dm,     period)
    minus_dm_s  = _wilder_smooth(minus_dm,    period)

    # Avoid divide-by-zero
    eps = 1e-10
    plus_di  = 100 * plus_dm_s  / (tr_s + eps)
    minus_di = 100 * minus_dm_s / (tr_s + eps)

    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + eps)
    adx_arr = _wilder_smooth(dx[period - 1:], period)

    adx_val     = float(adx_arr[-1])
    plus_di_val = float(plus_di[-1])
    minus_di_val= float(minus_di[-1])

    if adx_val >= 25:
        regime = Regime.TRENDING
    elif adx_val < 20:
        regime = Regime.RANGING
    else:
        regime = Regime.WEAK

    if plus_di_val > minus_di_val:
        direction = "UP"
    elif minus_di_val > plus_di_val:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    log.debug(
        "PERFORMANCE_LOGGED",
        f"ADX={adx_val:.2f} regime={regime} direction={direction}",
    )
    return RegimeResult(
        regime=regime,
        adx=round(adx_val, 4),
        plus_di=round(plus_di_val, 4),
        minus_di=round(minus_di_val, 4),
        trend_direction=direction,
    )


# ---------------------------------------------------------------------------
# Relative strength vs BTC
# ---------------------------------------------------------------------------

def compute_relative_strength(
    symbol: str,
    coin_closes: List[float],
    btc_closes:  List[float],
    lookback: int = 14,
) -> RelativeStrengthResult:
    """
    RS = (coin_return / btc_return) over `lookback` candles.
    rs_score > 1  → coin outperforming BTC
    rs_score < 1  → coin underperforming BTC
    """
    if len(coin_closes) < lookback + 1 or len(btc_closes) < lookback + 1:
        log.warning(
            "PERFORMANCE_LOGGED",
            f"insufficient data for RS ({symbol}), returning neutral",
            symbol=symbol,
        )
        return RelativeStrengthResult(
            symbol=symbol, rs_score=1.0, rs_pct=0.0, outperforming=False,
        )

    eps = 1e-10
    coin_return = (coin_closes[-1] - coin_closes[-lookback]) / (coin_closes[-lookback] + eps)
    btc_return  = (btc_closes[-1]  - btc_closes[-lookback])  / (btc_closes[-lookback]  + eps)

    rs_score = (1 + coin_return) / (1 + btc_return + eps)
    rs_pct   = (rs_score - 1.0) * 100

    log.debug(
        "PERFORMANCE_LOGGED",
        f"RS {symbol}: score={rs_score:.4f} coin={coin_return*100:.2f}% btc={btc_return*100:.2f}%",
        symbol=symbol,
        score=round(rs_score, 4),
    )
    return RelativeStrengthResult(
        symbol=symbol,
        rs_score=round(rs_score, 6),
        rs_pct=round(rs_pct, 4),
        outperforming=rs_score > 1.0,
    )
