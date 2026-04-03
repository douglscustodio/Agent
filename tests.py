"""
tests.py — Testes unitários do Jarvis AI Trading Monitor
Cobre: scoring, filters, btc_regime, derivatives, ranking, retry_utils

Executar: python tests.py
Sem dependências externas — testa apenas lógica pura.
"""

import asyncio
import sys
import time
import traceback
from dataclasses import dataclass
from typing import List

# ── Test runner simples ──────────────────────────────────────────────────────

_tests: list = []
_passed = 0
_failed = 0


def test(fn):
    """Decorator que registra um teste."""
    _tests.append(fn)
    return fn


def run_all() -> int:
    global _passed, _failed
    print(f"\n{'='*60}")
    print(f"  Jarvis AI — Suite de testes")
    print(f"{'='*60}\n")

    for fn in _tests:
        name = fn.__name__.replace("test_", "").replace("_", " ")
        try:
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
            print(f"  ✅  {name}")
            _passed += 1
        except AssertionError as exc:
            print(f"  ❌  {name}")
            print(f"       → {exc}")
            _failed += 1
        except Exception as exc:
            print(f"  💥  {name}")
            traceback.print_exc()
            _failed += 1

    total = _passed + _failed
    print(f"\n{'='*60}")
    print(f"  {_passed}/{total} passed   {_failed} failed")
    print(f"{'='*60}\n")
    return _failed


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_closes(n: int = 50, trend: str = "up") -> List[float]:
    """Generate synthetic price series."""
    base = 100.0
    closes = []
    for i in range(n):
        if trend == "up":
            closes.append(base + i * 0.5)
        elif trend == "down":
            closes.append(base - i * 0.5)
        else:
            closes.append(base + (i % 5 - 2) * 0.1)
    return closes


def _make_ohlcv(closes: List[float], spread: float = 0.5):
    highs  = [c + spread for c in closes]
    lows   = [c - spread for c in closes]
    return highs, lows, closes


# ============================================================================
# scoring.py
# ============================================================================

@test
def test_score_band_boundaries():
    from scoring import classify_score, ScoreBand
    assert classify_score(0.0)   == ScoreBand.REJECT
    assert classify_score(59.9)  == ScoreBand.REJECT
    assert classify_score(60.0)  == ScoreBand.WATCHLIST
    assert classify_score(74.9)  == ScoreBand.WATCHLIST
    assert classify_score(75.0)  == ScoreBand.VALID
    assert classify_score(84.9)  == ScoreBand.VALID
    assert classify_score(85.0)  == ScoreBand.HIGH_CONVICTION
    assert classify_score(100.0) == ScoreBand.HIGH_CONVICTION


@test
def test_weights_sum_to_one():
    from scoring import WEIGHTS
    total = sum(WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"


@test
def test_score_components_present():
    from scoring import ScoreResult, ScoreBand
    result = ScoreResult(
        symbol="BTC", direction="LONG",
        total=85.0, band=ScoreBand.HIGH_CONVICTION,
    )
    assert result.is_tradeable
    assert result.total == 85.0


@test
def test_score_reject_on_filter_fail():
    from scoring import compute_score, ScoreBand
    from filters import FilterResult
    from btc_regime import compute_adx, compute_relative_strength
    from derivatives import compute_funding_score, compute_oi_acceleration

    closes = _make_closes(50, "up")
    highs, lows, _ = _make_ohlcv(closes)
    regime = compute_adx(highs, lows, closes)
    rs     = compute_relative_strength("ETH", closes, closes)
    fund   = compute_funding_score("ETH", 0.0001, "LONG")
    oi     = compute_oi_acceleration("ETH", [1e6, 1.1e6, 1.2e6])

    failed_filter = [FilterResult(
        passed=False, filter_name="LIQUIDITY",
        value=0, threshold=1e6,
        reason="too illiquid",
    )]

    result = compute_score(
        symbol="ETH", direction="LONG",
        closes=closes, high=highs, low=lows,
        swing_origin=closes[0], regime=regime, rs=rs,
        funding=fund, oi_accel=oi, filter_results=failed_filter,
    )
    assert result.band == ScoreBand.REJECT, f"Expected REJECT, got {result.band}"
    assert result.total == 0.0


# ============================================================================
# filters.py
# ============================================================================

@test
def test_rsi_filter_overbought_rejected():
    from filters import filter_rsi
    # Very strong uptrend → RSI overbought
    closes = [100.0 + i * 2 for i in range(30)]
    result = filter_rsi(closes, direction="LONG")
    assert not result.passed, "Overbought should be rejected for LONG"


@test
def test_rsi_filter_normal_passes():
    from filters import filter_rsi
    # Sideways market → RSI around 50
    closes = _make_closes(50, "flat")
    result = filter_rsi(closes, direction="LONG")
    assert result.passed, f"Normal RSI should pass: {result.reason}"


@test
def test_liquidity_filter_rejects_low_volume():
    from filters import filter_liquidity
    result = filter_liquidity(volume_24h_usd=500_000, symbol="SCAM")
    assert not result.passed
    assert result.filter_name == "LIQUIDITY"


@test
def test_liquidity_filter_passes_high_volume():
    from filters import filter_liquidity
    result = filter_liquidity(volume_24h_usd=50_000_000, symbol="BTC")
    assert result.passed


@test
def test_bb_width_antifomo_rejects_expanded():
    from filters import filter_bb_width
    # Simulate very high volatility (expanded bands)
    closes = []
    for i in range(20):
        closes.append(100.0 + (i % 2) * 20)  # ±20% swings → wide bands
    result = filter_bb_width(closes, symbol="TEST")
    assert not result.passed, "Expanded BB should trigger anti-FOMO filter"


# ============================================================================
# btc_regime.py
# ============================================================================

@test
def test_adx_trending_on_strong_trend():
    from btc_regime import compute_adx, Regime
    # Clear uptrend over 60 candles
    closes = [100.0 + i for i in range(60)]
    highs  = [c + 0.5 for c in closes]
    lows   = [c - 0.5 for c in closes]
    result = compute_adx(highs, lows, closes)
    assert result.adx > 0, "ADX should be computed"
    assert result.trend_direction in ("UP", "DOWN", "NEUTRAL")


@test
def test_adx_insufficient_candles_returns_ranging():
    from btc_regime import compute_adx, Regime
    closes = [100.0] * 10   # too few
    highs  = [101.0] * 10
    lows   = [99.0]  * 10
    result = compute_adx(highs, lows, closes)
    assert result.regime == Regime.RANGING, "Should default to RANGING with few candles"


@test
def test_relative_strength_outperforming():
    from btc_regime import compute_relative_strength
    # Coin up 20%, BTC up 5% → outperforming
    coin_closes = [100.0 + i * 1.3 for i in range(20)]
    btc_closes  = [100.0 + i * 0.3 for i in range(20)]
    result = compute_relative_strength("ETH", coin_closes, btc_closes, lookback=14)
    assert result.outperforming, f"Should outperform BTC: rs={result.rs_score}"
    assert result.rs_score > 1.0


@test
def test_relative_strength_underperforming():
    from btc_regime import compute_relative_strength
    coin_closes = [100.0 + i * 0.1 for i in range(20)]
    btc_closes  = [100.0 + i * 1.5 for i in range(20)]
    result = compute_relative_strength("ADA", coin_closes, btc_closes, lookback=14)
    assert not result.outperforming
    assert result.rs_score < 1.0


# ============================================================================
# derivatives.py
# ============================================================================

@test
def test_funding_extreme_positive_bearish_for_long():
    from derivatives import compute_funding_score
    # Extreme positive funding (longs paying) → bad for LONG
    result = compute_funding_score("BTC", funding_8h=0.002, direction="LONG")
    assert result.score < 50, f"High positive funding should hurt LONG: {result.score}"
    assert result.signal == "LONGS_PAYING"


@test
def test_funding_extreme_negative_bullish_for_long():
    from derivatives import compute_funding_score
    result = compute_funding_score("BTC", funding_8h=-0.002, direction="LONG")
    assert result.score > 60, f"Negative funding should help LONG: {result.score}"


@test
def test_oi_acceleration_with_growing_oi():
    from derivatives import compute_oi_acceleration
    oi_hist = [1_000_000.0, 1_100_000.0, 1_300_000.0, 1_600_000.0]
    result  = compute_oi_acceleration("ETH", oi_hist)
    assert result.oi_change_pct > 0, "OI growing"
    assert result.acceleration > 0, "OI accelerating"
    assert result.score > 60


@test
def test_oi_acceleration_with_declining_oi():
    from derivatives import compute_oi_acceleration
    oi_hist = [1_600_000.0, 1_400_000.0, 1_200_000.0]
    result  = compute_oi_acceleration("ETH", oi_hist)
    assert result.oi_change_pct < 0
    assert result.score < 50


@test
def test_liquidation_zones_computed():
    from derivatives import estimate_liquidation_zones
    highs  = [100.0 + i for i in range(20)]
    lows   = [99.0  + i for i in range(20)]
    result = estimate_liquidation_zones("SOL", 115.0, highs, lows)
    assert result.long_liq_zone  is not None
    assert result.short_liq_zone is not None
    assert result.long_liq_zone.price_level < 115.0   # long liq below current
    assert result.short_liq_zone.price_level > 115.0  # short liq above current


# ============================================================================
# ranking.py
# ============================================================================

@test
def test_ranking_returns_max_3():
    from ranking import rank_signals
    from scoring import ScoreResult, ScoreBand

    scores = [
        ScoreResult("BTC",  "LONG",  88.0, ScoreBand.HIGH_CONVICTION),
        ScoreResult("ETH",  "LONG",  82.0, ScoreBand.VALID),
        ScoreResult("SOL",  "LONG",  79.0, ScoreBand.VALID),
        ScoreResult("ARB",  "LONG",  77.0, ScoreBand.VALID),
        ScoreResult("DOGE", "SHORT", 55.0, ScoreBand.WATCHLIST),
    ]
    result = rank_signals(scores, max_signals=3)
    assert len(result.top) <= 3, f"Should return max 3, got {len(result.top)}"
    assert result.top[0].score >= result.top[-1].score, "Should be sorted descending"


@test
def test_ranking_excludes_reject_and_watchlist():
    from ranking import rank_signals
    from scoring import ScoreResult, ScoreBand

    scores = [
        ScoreResult("BTC",  "LONG",  88.0, ScoreBand.HIGH_CONVICTION),
        ScoreResult("SCAM", "LONG",  55.0, ScoreBand.WATCHLIST),
        ScoreResult("JUNK", "LONG",   0.0, ScoreBand.REJECT),
    ]
    result = rank_signals(scores)
    assert len(result.top) == 1
    assert result.top[0].symbol == "BTC"
    assert len(result.watchlist) == 1
    assert len(result.rejected)  == 1


@test
def test_high_conviction_ranked_above_valid():
    from ranking import rank_signals
    from scoring import ScoreResult, ScoreBand

    scores = [
        ScoreResult("ETH",  "LONG",  83.0, ScoreBand.VALID),
        ScoreResult("BTC",  "LONG",  85.0, ScoreBand.HIGH_CONVICTION),
        ScoreResult("SOL",  "LONG",  76.0, ScoreBand.VALID),
    ]
    result = rank_signals(scores)
    assert result.top[0].symbol == "BTC", f"HIGH_CONVICTION should rank first, got {result.top[0].symbol}"


# ============================================================================
# retry_utils.py
# ============================================================================

@test
async def test_retry_succeeds_on_second_attempt():
    from retry_utils import retry_async

    attempts = [0]

    async def flaky():
        attempts[0] += 1
        if attempts[0] < 2:
            raise ConnectionError("first attempt fails")
        return "ok"

    result = await retry_async(flaky, max_attempts=3, base_delay=0.01)
    assert result == "ok"
    assert attempts[0] == 2


@test
async def test_retry_raises_after_max_attempts():
    from retry_utils import retry_async

    async def always_fails():
        raise ValueError("always")

    try:
        await retry_async(always_fails, max_attempts=2, base_delay=0.01)
        assert False, "Should have raised"
    except ValueError:
        pass   # expected


@test
async def test_retry_zero_delay_fast():
    from retry_utils import retry_async

    calls = [0]

    async def eventually():
        calls[0] += 1
        if calls[0] < 3:
            raise RuntimeError("not yet")
        return calls[0]

    start  = time.monotonic()
    result = await retry_async(eventually, max_attempts=3, base_delay=0.01)
    elapsed= time.monotonic() - start

    assert result == 3
    assert elapsed < 1.0, f"Should be fast with 0.01s delay, took {elapsed:.2f}s"


# ============================================================================
# freshness.py
# ============================================================================

@test
def test_freshness_fresh_candle():
    from freshness import check_candle_freshness
    result = check_candle_freshness(time.time() - 30, "BTC")
    assert result.is_fresh


@test
def test_freshness_stale_candle():
    from freshness import check_candle_freshness
    result = check_candle_freshness(time.time() - 300, "BTC")
    assert not result.is_fresh
    assert result.reason is not None


# ============================================================================
# config.py
# ============================================================================

@test
def test_config_has_required_sections():
    from config import config
    assert hasattr(config, "db")
    assert hasattr(config, "ws")
    assert hasattr(config, "health")


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    failed = run_all()
    sys.exit(failed)
