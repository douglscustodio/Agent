"""
derivatives.py — Derivatives market intelligence
Computes:
  - Funding rate score (contrarian + momentum signal)
  - OI acceleration (rate of change of open interest)
  - Liquidation zone estimate (price levels with clustered liq risk)
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from logger import get_logger

log = get_logger("derivatives")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FundingResult:
    symbol: str
    funding_rate: float         # current annualised rate (decimal, e.g. 0.0001)
    funding_8h:   float         # raw 8-hour rate
    score: float                # 0–100 contribution to total score
    signal: str                 # "LONGS_PAYING" | "SHORTS_PAYING" | "NEUTRAL"
    bias: str                   # "LONG_BIAS" | "SHORT_BIAS" | "NEUTRAL"


@dataclass
class OIAccelerationResult:
    symbol: str
    oi_current:     float
    oi_prev:        float
    oi_change_pct:  float
    acceleration:   float       # second derivative of OI (pct change of pct change)
    score: float                # 0–100 contribution


@dataclass
class LiquidationZone:
    price_level: float
    estimated_volume_usd: float
    side: str                   # "LONG_LIQ" | "SHORT_LIQ"
    distance_pct: float         # % distance from current price


@dataclass
class LiquidationResult:
    symbol: str
    current_price: float
    long_liq_zone:  Optional[LiquidationZone]
    short_liq_zone: Optional[LiquidationZone]
    nearest_zone:   Optional[LiquidationZone]
    risk_level: str             # "HIGH" | "MEDIUM" | "LOW"


# ---------------------------------------------------------------------------
# Funding rate scoring
# ---------------------------------------------------------------------------

# Thresholds (annualised)
_FUNDING_EXTREME_POS  =  0.30   # > +30% annualised → market very long → contrarian SHORT
_FUNDING_HIGH_POS     =  0.10
_FUNDING_NEUTRAL_HIGH =  0.03
_FUNDING_NEUTRAL_LOW  = -0.03
_FUNDING_HIGH_NEG     = -0.10
_FUNDING_EXTREME_NEG  = -0.30   # < -30% → market very short → contrarian LONG


def compute_funding_score(
    symbol: str,
    funding_8h: float,              # raw 8h funding rate (e.g. 0.0001 = 0.01%)
    direction: str = "LONG",        # intended trade direction
) -> FundingResult:
    """
    Score funding rate signal for a given intended trade direction.
    Contrarian logic:
      - Extreme positive funding (longs paying) → bad for LONG, good for SHORT
      - Extreme negative funding (shorts paying) → bad for SHORT, good for LONG
    Score reflects tailwind/headwind for the intended direction.
    """
    # Annualise: 3 funding periods per day × 365
    annual = funding_8h * 3 * 365
    direction = direction.upper()

    if annual > _FUNDING_NEUTRAL_LOW and annual < _FUNDING_NEUTRAL_HIGH:
        signal = "NEUTRAL"
        bias   = "NEUTRAL"
    elif annual >= _FUNDING_NEUTRAL_HIGH:
        signal = "LONGS_PAYING"
        bias   = "SHORT_BIAS"        # market crowded long
    else:
        signal = "SHORTS_PAYING"
        bias   = "LONG_BIAS"         # market crowded short

    # Score for LONG direction: best when shorts are paying (contrarian tailwind)
    if direction == "LONG":
        if annual <= _FUNDING_EXTREME_NEG:
            score = 95.0   # extreme short crowding → strong LONG tailwind
        elif annual <= _FUNDING_HIGH_NEG:
            score = 80.0
        elif annual <= _FUNDING_NEUTRAL_LOW:
            score = 65.0
        elif annual <= _FUNDING_NEUTRAL_HIGH:
            score = 55.0   # neutral
        elif annual <= _FUNDING_HIGH_POS:
            score = 40.0   # headwind
        else:
            score = 20.0   # extreme longs paying → avoid LONG
    else:  # SHORT
        if annual >= _FUNDING_EXTREME_POS:
            score = 95.0   # extreme long crowding → strong SHORT tailwind
        elif annual >= _FUNDING_HIGH_POS:
            score = 80.0
        elif annual >= _FUNDING_NEUTRAL_HIGH:
            score = 65.0
        elif annual >= _FUNDING_NEUTRAL_LOW:
            score = 55.0
        elif annual >= _FUNDING_HIGH_NEG:
            score = 40.0
        else:
            score = 20.0

    log.debug(
        "PERFORMANCE_LOGGED",
        f"funding {symbol}: 8h={funding_8h:.6f} annual={annual:.2%} score={score:.1f}",
        symbol=symbol,
        score=score,
        direction=direction,
    )
    return FundingResult(
        symbol=symbol,
        funding_rate=round(annual, 6),
        funding_8h=round(funding_8h, 8),
        score=score,
        signal=signal,
        bias=bias,
    )


# ---------------------------------------------------------------------------
# OI acceleration
# ---------------------------------------------------------------------------

def compute_oi_acceleration(
    symbol: str,
    oi_history: List[float],        # OI snapshots, chronological, ≥ 3 values
) -> OIAccelerationResult:
    """
    OI acceleration = rate of change of OI change.
    Positive acceleration: OI growing faster → conviction building.
    Negative acceleration: OI growth slowing → distribution risk.
    Score: 0–100, higher = stronger conviction.
    """
    eps = 1e-10

    if len(oi_history) < 3:
        log.warning(
            "PERFORMANCE_LOGGED",
            f"insufficient OI history for {symbol} ({len(oi_history)} < 3)",
            symbol=symbol,
        )
        return OIAccelerationResult(
            symbol=symbol,
            oi_current=oi_history[-1] if oi_history else 0,
            oi_prev=oi_history[-2] if len(oi_history) >= 2 else 0,
            oi_change_pct=0.0,
            acceleration=0.0,
            score=50.0,
        )

    oi = np.array(oi_history, dtype=float)
    changes = np.diff(oi) / (oi[:-1] + eps) * 100  # pct change per period

    oi_change_pct = float(changes[-1])
    acceleration  = float(changes[-1] - changes[-2])   # second derivative

    # Scoring: reward growing OI with positive acceleration
    if oi_change_pct > 5 and acceleration > 0:
        score = 90.0
    elif oi_change_pct > 2 and acceleration > 0:
        score = 75.0
    elif oi_change_pct > 0:
        score = 60.0
    elif oi_change_pct > -2:
        score = 45.0
    elif acceleration < -2:
        score = 20.0    # OI collapsing
    else:
        score = 30.0

    log.debug(
        "PERFORMANCE_LOGGED",
        f"OI acceleration {symbol}: change={oi_change_pct:.2f}% accel={acceleration:.2f} score={score}",
        symbol=symbol,
        score=score,
    )
    return OIAccelerationResult(
        symbol=symbol,
        oi_current=float(oi[-1]),
        oi_prev=float(oi[-2]),
        oi_change_pct=round(oi_change_pct, 4),
        acceleration=round(acceleration, 4),
        score=score,
    )


# ---------------------------------------------------------------------------
# Liquidation zone estimation
# ---------------------------------------------------------------------------

# Heuristic: long liquidations cluster below a recent swing low (leverage × price)
# Short liquidations cluster above a recent swing high
# This is a simplified statistical estimate — not an order-book scan.

def estimate_liquidation_zones(
    symbol: str,
    current_price: float,
    high_history:  List[float],     # recent N candle highs
    low_history:   List[float],     # recent N candle lows
    avg_leverage:  float = 10.0,    # assumed average market leverage
) -> LiquidationResult:
    """
    Estimate liquidation clusters using swing high/low + leverage math.
    Long liq zone  ≈ swing_low  × (1 - 1/leverage) — where longs get margin-called
    Short liq zone ≈ swing_high × (1 + 1/leverage) — where shorts get margin-called
    """
    if not high_history or not low_history:
        return LiquidationResult(
            symbol=symbol,
            current_price=current_price,
            long_liq_zone=None,
            short_liq_zone=None,
            nearest_zone=None,
            risk_level="LOW",
        )

    swing_low  = float(np.min(low_history[-20:]))   # last 20 candles
    swing_high = float(np.max(high_history[-20:]))

    # Liquidation price levels
    long_liq_price  = swing_low  * (1 - 1 / avg_leverage)
    short_liq_price = swing_high * (1 + 1 / avg_leverage)

    # Estimated USD volume is heuristic (scaled by price × OI proxy)
    long_zone = LiquidationZone(
        price_level=round(long_liq_price, 6),
        estimated_volume_usd=0.0,           # Phase 3: connect real OI data
        side="LONG_LIQ",
        distance_pct=round((current_price - long_liq_price) / current_price * 100, 4),
    )
    short_zone = LiquidationZone(
        price_level=round(short_liq_price, 6),
        estimated_volume_usd=0.0,
        side="SHORT_LIQ",
        distance_pct=round((short_liq_price - current_price) / current_price * 100, 4),
    )

    # Nearest zone to current price
    nearest = long_zone if long_zone.distance_pct < short_zone.distance_pct else short_zone

    # Risk: if nearest zone is within 3% → HIGH
    if nearest.distance_pct < 3:
        risk = "HIGH"
    elif nearest.distance_pct < 7:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    log.debug(
        "PERFORMANCE_LOGGED",
        f"liq zones {symbol}: long_liq={long_liq_price:.4f} short_liq={short_liq_price:.4f} risk={risk}",
        symbol=symbol,
    )
    return LiquidationResult(
        symbol=symbol,
        current_price=current_price,
        long_liq_zone=long_zone,
        short_liq_zone=short_zone,
        nearest_zone=nearest,
        risk_level=risk,
    )
