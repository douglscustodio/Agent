"""
scanner.py — Full market intelligence pipeline (Phase 4 complete)
Fetches real candles + funding + OI from Hyperliquid REST
Runs full scoring pipeline and returns ranked signals
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

log = get_logger("scanner")

# ---------------------------------------------------------------------------
# Symbols to scan (override via SCAN_SYMBOLS env var)
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
# OI history cache (needed for acceleration: need ≥ 3 snapshots)
# ---------------------------------------------------------------------------

_oi_history: Dict[str, List[float]] = {}   # symbol → last 5 OI values
_meta_cache: Dict[str, 'AssetMeta'] = {}    # symbol → latest AssetMeta (price, funding, OI)
_snapshot_ts: Dict[str, float] = {}        # symbol → last snapshot unix ts


def _update_oi_history(symbol: str, oi: float) -> None:
    hist = _oi_history.setdefault(symbol, [])
    hist.append(oi)
    if len(hist) > 5:
        hist.pop(0)
    _snapshot_ts[symbol] = time.time()


def _get_oi_history(symbol: str) -> List[float]:
    return _oi_history.get(symbol, [0.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# Direction detection (simple trend bias)
# ---------------------------------------------------------------------------

def _detect_direction(candles: List[Candle]) -> str:
    """Simple direction: compare last close to SMA-20."""
    if len(candles) < 20:
        return "LONG"
    closes = [c.close for c in candles]
    sma20  = sum(closes[-20:]) / 20
    return "LONG" if closes[-1] > sma20 else "SHORT"


# ---------------------------------------------------------------------------
# Per-symbol scan
# ---------------------------------------------------------------------------

async def scan_symbol(
    symbol:    str,
    candles:   List[Candle],
    meta:      AssetMeta,
    btc_candles: List[Candle],
    volume_24h: float,
) -> Optional[ScoreResult]:
    """
    Full pipeline for one symbol. Returns ScoreResult or None if rejected.
    """
    if len(candles) < 30:
        log.warning("PERFORMANCE_LOGGED", f"skip {symbol}: insufficient candles ({len(candles)})", symbol=symbol)
        return None

    t0 = time.monotonic()

    closes = [c.close for c in candles]
    highs  = [c.high  for c in candles]
    lows   = [c.low   for c in candles]
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
    # Don't reject on stale snapshot — just log
    if not sf.is_fresh:
        log.warning("PERFORMANCE_LOGGED", f"{symbol}: stale derivatives snapshot", symbol=symbol)

    # 2. Update OI history
    _update_oi_history(symbol, meta.open_interest)

    # 3. Volume — estimate if zero
    vol_usd = volume_24h if volume_24h > 0 else meta.mark_price * meta.open_interest * 0.1

    # 4. Swing origin = close 10 candles ago (for ATR filter)
    swing_origin = closes[-10] if len(closes) >= 10 else closes[0]

    # 5. Hard filters
    all_passed, filter_results = run_all_filters(
        closes=closes,
        high=highs,
        low=lows,
        volume_24h_usd=vol_usd,
        swing_origin_price=swing_origin,
        direction=direction,
        symbol=symbol,
    )

    # 6. BTC regime
    regime = compute_adx(highs, lows, closes)

    # 7. Relative strength vs BTC
    rs = compute_relative_strength(symbol, closes, btc_closes)

    # 8. Derivatives
    funding  = compute_funding_score(symbol, meta.funding_8h, direction)
    oi_hist  = _get_oi_history(symbol)
    oi_accel = compute_oi_acceleration(symbol, oi_hist)
    liq      = estimate_liquidation_zones(symbol, closes[-1], highs, lows)

    # 9. Score
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
    )

    log.timed(
        "PERFORMANCE_LOGGED",
        f"{symbol} {direction} score={score.total:.1f} [{score.band}]",
        t0,
        symbol=symbol,
        direction=direction,
        score=score.total,
    )
    return score


# ---------------------------------------------------------------------------
# Full scan cycle — called by scheduler every 5 min
# ---------------------------------------------------------------------------

async def run_scan_cycle(
    snapshots:   list = None,    # ignored — kept for API compat
    btc_closes:  list = None,    # ignored — fetched internally
) -> RankingResult:
    """
    1. Fetch all candles + meta from Hyperliquid
    2. Run per-symbol pipeline concurrently
    3. Rank top 3
    """
    symbols = get_symbols()
    t0      = time.monotonic()

    log.info("PERFORMANCE_LOGGED", f"scan cycle starting: {len(symbols)} symbols")
    await write_system_event(
        "PERFORMANCE_LOGGED", f"scan starting: {len(symbols)} symbols",
        level="INFO", module="scanner",
    )

    # Fetch candles for all symbols + BTC concurrently
    all_syms = list(set(symbols + ["BTC"]))
    candle_map = await fetch_all_candles(all_syms, interval=CANDLE_INTERVAL, count=CANDLE_COUNT)
    btc_candles = candle_map.get("BTC", [])

    # Fetch meta (funding, OI, mark price) for all assets
    async with aiohttp.ClientSession() as session:
        meta_map = await fetch_all_metas(session)

        # Fetch 24h volumes concurrently
        vol_tasks = {sym: fetch_volume_24h(session, sym) for sym in symbols}
        vol_results = await asyncio.gather(*vol_tasks.values(), return_exceptions=True)
        vol_map = {
            sym: (v if isinstance(v, float) else 0.0)
            for sym, v in zip(vol_tasks.keys(), vol_results)
        }

    # Update global meta cache for price lookups by notifier
    _meta_cache.update(meta_map)

    # Per-symbol scan concurrently
    scan_tasks = []
    valid_syms = []
    for sym in symbols:
        candles = candle_map.get(sym, [])
        meta    = meta_map.get(sym)
        if not candles or not meta:
            log.warning("PERFORMANCE_LOGGED", f"skip {sym}: no data", symbol=sym)
            continue
        scan_tasks.append(scan_symbol(sym, candles, meta, btc_candles, vol_map.get(sym, 0.0)))
        valid_syms.append(sym)

    raw_results = await asyncio.gather(*scan_tasks, return_exceptions=False)
    scores = [r for r in raw_results if r is not None]

    # Build derivative maps for tie-breaking
    oi_map   = {}
    fund_map = {}
    for sym in valid_syms:
        meta = meta_map.get(sym)
        if meta:
            candles = candle_map.get(sym, [])
            direction = _detect_direction(candles) if candles else "LONG"
            oi_map[sym]   = compute_oi_acceleration(sym, _get_oi_history(sym))
            fund_map[sym] = compute_funding_score(sym, meta.funding_8h, direction)

    ranking = rank_signals(scores, max_signals=3, oi_accel_map=oi_map, funding_map=fund_map)

    summary = format_ranking_summary(ranking)
    for line in summary.splitlines():
        if line.strip():
            log.info("PERFORMANCE_LOGGED", line)

    elapsed = round((time.monotonic() - t0) * 1000, 2)
    log.info(
        "PERFORMANCE_LOGGED",
        f"scan complete: {len(scores)} scored, {ranking.total_valid} valid, "
        f"{len(ranking.top)} top signals — {elapsed}ms",
        latency_ms=elapsed,
    )
    await write_system_event(
        "PERFORMANCE_LOGGED",
        f"scan done: {len(ranking.top)} signals in {elapsed}ms",
        level="INFO", module="scanner", latency_ms=elapsed,
    )

    return ranking


# ---------------------------------------------------------------------------
# WS hook (called by websocket_client on each frame)
# ---------------------------------------------------------------------------

async def on_market_data(raw_data: dict) -> None:
    """
    Update live price cache from allMids WS frame.
    Candle data comes from REST (scheduled), not WS.
    """
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
