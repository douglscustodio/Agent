"""
scanner.py — Full market intelligence pipeline (Phase 4 + upgrades)
Fetches real candles + funding + OI from Hyperliquid REST
Runs full scoring pipeline and returns ranked signals.

UPGRADE: Crowded trade detection
  Detects the "trapped long" setup: high funding + high OI + price near recent high.
  These are short-squeeze / liquidation cascade candidates that look bullish
  on the surface but are actually dangerous. Signals flagged as crowded are
  downgraded or annotated.

UPGRADE: Memory leak prevention
  _meta_cache is now capped at MAX_META_CACHE entries (evicts oldest on overflow).
  This prevents unbounded growth in long-running deployments.

UPGRADE: adaptive_weights injected into compute_score
  The AdaptiveEngine's current weights are passed to the scorer so that
  regime-adaptive + learned weights work together.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from btc_regime import compute_adx, compute_relative_strength, RegimeResult
from database import write_system_event
from derivatives import (
    compute_funding_score, compute_oi_acceleration,
    estimate_liquidation_zones,
)
from filters import run_all_filters
from freshness import check_candle_freshness, check_snapshot_freshness
from hyperliquid_client import (
    Candle, AssetMeta,
    fetch_all_candles, fetch_all_metas, fetch_volume_24h,
)
from logger import get_logger
from ranking import rank_signals, format_ranking_summary, RankingResult
from scoring import compute_score, ScoreResult
from data_quality import update_quality, get_current_quality, log_quality_warnings

log = get_logger("scanner")

# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS = [
    "BTC", "ETH", "SOL", "ARB", "OP", "AVAX", "NEAR", "APT",
    "SUI", "INJ", "TIA", "JTO", "PYTH", "WIF", "BONK", "PEPE",
    "LDO", "RNDR", "FET", "TAO", "DOGE", "LINK", "UNI", "AAVE",
]

CANDLE_INTERVAL = os.getenv("SCAN_INTERVAL", "15m")
CANDLE_COUNT    = 200


def get_symbols() -> List[str]:
    env = os.getenv("SCAN_SYMBOLS", "")
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    return DEFAULT_SYMBOLS


# ---------------------------------------------------------------------------
# Caches with memory bounds
# UPGRADE: MAX_META_CACHE prevents unbounded dict growth in long deployments
# ---------------------------------------------------------------------------

MAX_META_CACHE: int = 150   # ~6× universe size, generous but bounded

_oi_history:  Dict[str, List[float]] = {}   # symbol → last 5 OI values (already bounded)
_meta_cache:  Dict[str, "AssetMeta"] = {}   # symbol → latest AssetMeta
_meta_insert_order: List[str] = []          # UPGRADE: track insertion order for eviction
_snapshot_ts: Dict[str, float] = {}         # symbol → last snapshot unix ts


def _update_oi_history(symbol: str, oi: float) -> None:
    hist = _oi_history.setdefault(symbol, [])
    hist.append(oi)
    if len(hist) > 5:
        hist.pop(0)
    _snapshot_ts[symbol] = time.time()


def _get_oi_history(symbol: str) -> List[float]:
    return _oi_history.get(symbol, [0.0, 0.0, 0.0])


def _update_meta_cache(meta_map: Dict[str, "AssetMeta"]) -> None:
    """
    UPGRADE: Cache update with LRU-style eviction to prevent memory leaks.
    Removes oldest entries when cache exceeds MAX_META_CACHE.
    """
    global _meta_insert_order
    for sym, meta in meta_map.items():
        if sym not in _meta_cache:
            _meta_insert_order.append(sym)
        _meta_cache[sym] = meta

    # Evict oldest entries if over limit
    while len(_meta_cache) > MAX_META_CACHE:
        oldest = _meta_insert_order.pop(0)
        _meta_cache.pop(oldest, None)


# ---------------------------------------------------------------------------
# UPGRADE: Crowded trade detection
#
# A "crowded trade" has three simultaneous conditions:
#   1. Funding rate is very high (> CROWD_FUNDING_THRESHOLD) → everyone is long
#   2. Open interest is historically elevated (via OI acceleration score)
#   3. Price has failed to make new highs despite the positioning → absorption
#
# This combination is the classic "trapped long" setup. Price looks bullish
# (people are piling in), but it's actually a coiled spring for a flush.
# We don't hard-reject these — we annotate them and apply a score penalty
# so the system avoids initiating fresh longs into crowd positions.
# ---------------------------------------------------------------------------

CROWD_FUNDING_THRESHOLD   = 0.0005   # > 0.05% per 8h = hot funding
CROWD_PRICE_STAGNATION    = 0.015    # within 1.5% of recent high = no progress
CROWD_SCORE_PENALTY       = 8.0      # points deducted from composite score


@dataclass
class CrowdedTradeResult:
    is_crowded:   bool
    funding_8h:   float
    price_vs_high: float   # how far price is from recent high (0 = at high)
    reason:       str = ""


def detect_crowded_trade(
    symbol:   str,
    meta:     "AssetMeta",
    closes:   List[float],
    oi_score: float,
) -> CrowdedTradeResult:
    """
    Detects crowded-long setups: high funding + OI elevated + price stagnating.
    Returns CrowdedTradeResult with is_crowded flag and metadata.
    """
    funding_high = abs(meta.funding_8h) > CROWD_FUNDING_THRESHOLD

    # Price stagnation: current price near recent high (last 24 candles ≈ 6h on 15m)
    lookback = min(24, len(closes) - 1)
    recent_high = max(closes[-lookback:]) if lookback > 0 else closes[-1]
    price_vs_high = (recent_high - closes[-1]) / (recent_high + 1e-10)
    price_stagnant = price_vs_high < CROWD_PRICE_STAGNATION   # near high, no new progress

    # OI elevated (score > 70 means OI is building fast)
    oi_elevated = oi_score > 70.0

    is_crowded = funding_high and price_stagnant and oi_elevated

    if is_crowded:
        reason = (
            f"funding={meta.funding_8h*100:.4f}%/8h "
            f"price_vs_high={price_vs_high*100:.2f}% "
            f"oi_score={oi_score:.0f}"
        )
        log.warning(
            "PERFORMANCE_LOGGED",
            f"CROWDED TRADE detected: {symbol} — {reason}",
            symbol=symbol,
        )
    else:
        reason = ""

    return CrowdedTradeResult(
        is_crowded=is_crowded,
        funding_8h=meta.funding_8h,
        price_vs_high=price_vs_high,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Direction detection
# ---------------------------------------------------------------------------

def _detect_direction(candles: List[Candle]) -> str:
    if len(candles) < 20:
        return "LONG"
    closes = [c.close for c in candles]
    sma20  = sum(closes[-20:]) / 20
    return "LONG" if closes[-1] > sma20 else "SHORT"


# ---------------------------------------------------------------------------
# Per-symbol scan
# ---------------------------------------------------------------------------

async def scan_symbol(
    symbol:      str,
    candles:     List[Candle],
    meta:        "AssetMeta",
    btc_candles: List[Candle],
    volume_24h:  float,
    adaptive_weights: Optional[Dict] = None,   # from AdaptiveEngine
    sector_heat_map:  Optional[Dict[str, float]] = None,  # UPGRADE: from NewsEngine
) -> Optional[ScoreResult]:
    if len(candles) < 30:
        log.warning("PERFORMANCE_LOGGED", f"skip {symbol}: insufficient candles ({len(candles)})", symbol=symbol)
        return None

    t0 = time.monotonic()

    closes    = [c.close for c in candles]
    highs     = [c.high  for c in candles]
    lows      = [c.low   for c in candles]
    volumes   = [c.volume for c in candles]   # UPGRADE: for volume confirmation
    opens     = [c.open   for c in candles]   # UPGRADE: for candle direction
    btc_closes = [c.close for c in btc_candles] if btc_candles else closes

    direction = _detect_direction(candles)

    # 1. Freshness
    last_ts = candles[-1].timestamp
    cf = check_candle_freshness(last_ts, symbol)
    if not cf.is_fresh:
        log.warning("PERFORMANCE_LOGGED", f"skip {symbol}: stale candles", symbol=symbol)
        return None

    snap_ts = _snapshot_ts.get(symbol, time.time())
    sf = check_snapshot_freshness(snap_ts, symbol)
    if not sf.is_fresh:
        log.warning("PERFORMANCE_LOGGED", f"{symbol}: stale derivatives snapshot", symbol=symbol)

    # 2. Update OI history
    _update_oi_history(symbol, meta.open_interest)

    # 3. Volume
    vol_usd = volume_24h if volume_24h > 0 else meta.mark_price * meta.open_interest * 0.1

    # 4. Swing origin
    swing_origin = closes[-10] if len(closes) >= 10 else closes[0]

    # 5. Hard filters + volume confirmation
    all_passed, filter_results, vol_confirm = run_all_filters(
        closes=closes,
        high=highs,
        low=lows,
        volume_24h_usd=vol_usd,
        swing_origin_price=swing_origin,
        direction=direction,
        symbol=symbol,
        volumes=volumes,    # UPGRADE: micro entry confirmation
        opens=opens,        # UPGRADE: candle direction detection
    )

    # 6. BTC regime
    regime = compute_adx(highs, lows, closes)

    # 7. Relative strength
    rs = compute_relative_strength(symbol, closes, btc_closes)

    # 8. Derivatives
    funding  = compute_funding_score(symbol, meta.funding_8h, direction)
    oi_hist  = _get_oi_history(symbol)
    oi_accel = compute_oi_acceleration(symbol, oi_hist)
    liq      = estimate_liquidation_zones(symbol, closes[-1], highs, lows)

    # 9. UPGRADE: Crowded trade detection
    crowd = detect_crowded_trade(symbol, meta, closes, oi_accel.score)

    # 10. Resolve sector heat for this symbol
    from sector_rotation import classify_symbol as _classify
    _sector = _classify(symbol)
    _heat   = (sector_heat_map or {}).get(_sector, 50.0)

    # 11. Score (with adaptive weights + context bonuses injected)
    score = compute_score(
        symbol=symbol,
        direction=direction,
        closes=closes,
        high=highs,
        low=lows,
        swing_origin=swing_origin,
        regime=regime,
        rs=rs,
        funding=funding,
        oi_accel=oi_accel,
        liq=liq,
        filter_results=filter_results,
        adaptive_weights=adaptive_weights,
        sector_heat=_heat,                                          # UPGRADE
        vol_confirm_bonus=vol_confirm.score_bonus if vol_confirm else 0.0,  # UPGRADE
    )

    # 12. UPGRADE: Apply crowded trade penalty to score
    if crowd.is_crowded and score.total > 0 and direction == "LONG":
        original = score.total
        score.total = max(0.0, score.total - CROWD_SCORE_PENALTY)
        # Re-classify after penalty
        from scoring import classify_score
        score.band = classify_score(score.total)
        log.warning(
            "PERFORMANCE_LOGGED",
            f"CROWDED TRADE penalty: {symbol} score {original:.1f} → {score.total:.1f} "
            f"reason: {crowd.reason}",
            symbol=symbol,
        )

    log.timed(
        "PERFORMANCE_LOGGED",
        f"{symbol} {direction} score={score.total:.1f} [{score.band}]"
        + (" [CROWDED]" if crowd.is_crowded else ""),
        t0,
        symbol=symbol,
        direction=direction,
        score=score.total,
    )
    return score


# ---------------------------------------------------------------------------
# Full scan cycle
# ---------------------------------------------------------------------------

async def run_scan_cycle(
    snapshots:        list = None,
    btc_closes:       list = None,
    adaptive_weights: Optional[Dict] = None,
    sector_heat_map:  Optional[Dict[str, float]] = None,  # UPGRADE: from NewsEngine
) -> RankingResult:
    symbols = get_symbols()
    t0      = time.monotonic()

    log.info("PERFORMANCE_LOGGED", f"scan cycle starting: {len(symbols)} symbols")
    await write_system_event(
        "PERFORMANCE_LOGGED", f"scan starting: {len(symbols)} symbols",
        level="INFO", module="scanner",
    )

    all_syms   = list(set(symbols + ["BTC"]))
    candle_map = await fetch_all_candles(all_syms, interval=CANDLE_INTERVAL, count=CANDLE_COUNT)
    btc_candles = candle_map.get("BTC", [])

    hyperliquid_ok = bool(candle_map) and len(candle_map) > 0

    async with aiohttp.ClientSession() as session:
        meta_map = await fetch_all_metas(session)

        vol_tasks   = {sym: fetch_volume_24h(session, sym) for sym in symbols}
        vol_results = await asyncio.gather(*vol_tasks.values(), return_exceptions=True)
        vol_map = {
            sym: (v if isinstance(v, float) else 0.0)
            for sym, v in zip(vol_tasks.keys(), vol_results)
        }

    # UPGRADE: bounded cache update
    _update_meta_cache(meta_map)

    # UPGRADE: track data quality
    from websocket_client import ws_price_cache
    update_quality(
        market_fetched_at=time.time(),
        hyperliquid_available=hyperliquid_ok,
        ws_connected=bool(ws_price_cache),
        symbols_requested=len(symbols),
        symbols_with_data=sum(1 for s in symbols if s in candle_map and candle_map[s]),
    )

    scan_tasks = []
    valid_syms = []
    for sym in symbols:
        candles = candle_map.get(sym, [])
        meta    = meta_map.get(sym)
        if not candles or not meta:
            log.warning("PERFORMANCE_LOGGED", f"skip {sym}: no data", symbol=sym)
            continue
        scan_tasks.append(
            scan_symbol(sym, candles, meta, btc_candles, vol_map.get(sym, 0.0), adaptive_weights, sector_heat_map)
        )
        valid_syms.append(sym)

    raw_results = await asyncio.gather(*scan_tasks, return_exceptions=True)
    scores = [r for r in raw_results if isinstance(r, ScoreResult)]

    oi_map   = {}
    fund_map = {}
    for sym in valid_syms:
        meta = meta_map.get(sym)
        if meta:
            candles   = candle_map.get(sym, [])
            direction = _detect_direction(candles) if candles else "LONG"
            oi_map[sym]   = compute_oi_acceleration(sym, _get_oi_history(sym))
            fund_map[sym] = compute_funding_score(sym, meta.funding_8h, direction)

    ranking = rank_signals(scores, max_signals=3, oi_accel_map=oi_map, funding_map=fund_map)

    if not scores:
        log.info("NO_TRADE", "No symbols passed scoring - market conditions not favorable")
        return ranking

    if not ranking.top:
        log.info("NO_TRADE", "No signals above threshold - market conditions not favorable")
        return ranking

    if not ranking.top:
        log.info("NO_TRADE", "No signals generated in this scan")
        return ranking
    
    avg_score = sum(s.total for s in ranking.top) / len(ranking.top) if ranking.top else 0
    log.info("NO_TRADE", f"Signals found: avg_score={avg_score:.1f}, count={len(ranking.top)}")

    summary = format_ranking_summary(ranking)
    for line in summary.splitlines():
        if line.strip():
            log.info("PERFORMANCE_LOGGED", line)

    elapsed = round((time.monotonic() - t0) * 1000, 2)
    quality = get_current_quality()
    log.info(
        "PERFORMANCE_LOGGED",
        f"scan complete: {len(scores)} scored, {ranking.total_valid} valid, "
        f"{len(ranking.top)} top signals, sectors={ranking.sectors_hit} — {elapsed}ms",
        latency_ms=elapsed,
    )
    await write_system_event(
        "PERFORMANCE_LOGGED",
        f"scan done: {len(ranking.top)} signals in {elapsed}ms sectors={ranking.sectors_hit}",
        level="INFO", module="scanner", latency_ms=elapsed,
    )

    log_quality_warnings()

    return ranking


# ---------------------------------------------------------------------------
# WS hook
# ---------------------------------------------------------------------------

async def on_market_data(raw_data: dict) -> None:
    channel = raw_data.get("channel", "")
    if channel == "allMids":
        mids = raw_data.get("data", {}).get("mids", {})
        try:
            from websocket_client import ws_price_cache
            for sym, price_str in mids.items():
                try:
                    ws_price_cache[sym] = float(price_str)
                except (ValueError, TypeError):
                    pass
        except ImportError:
            pass
