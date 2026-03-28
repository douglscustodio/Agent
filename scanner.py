"""
scanner.py — Market intelligence orchestrator (Phase 2)
Wires together: freshness → filters → btc_regime → derivatives → scoring → ranking
Called by websocket_client on each data update or on a timed scan cycle.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from btc_regime import compute_adx, compute_relative_strength, RegimeResult
from database import write_system_event
from derivatives import (
    compute_funding_score,
    compute_oi_acceleration,
    estimate_liquidation_zones,
    FundingResult,
    OIAccelerationResult,
    LiquidationResult,
)
from filters import run_all_filters
from freshness import check_candle_freshness, check_snapshot_freshness
from logger import get_logger
from ranking import rank_signals, format_ranking_summary, RankingResult
from scoring import compute_score, ScoreResult

log = get_logger("scanner")


# ---------------------------------------------------------------------------
# MarketSnapshot — input contract
# ---------------------------------------------------------------------------

@dataclass
class MarketSnapshot:
    """
    All raw market data for a single symbol, as delivered by websocket_client.
    Phase 2: populated from Hyperliquid WS frames.
    Phase 3: extended with order-book depth.
    """
    symbol:              str
    direction:           str                    # "LONG" | "SHORT"

    # OHLCV (chronological, most recent last)
    closes:              List[float]
    highs:               List[float]
    lows:                List[float]
    last_candle_ts:      float                  # unix epoch of last close

    # Volume
    volume_24h_usd:      float

    # Swing reference for ATR / late-entry
    swing_origin_price:  float

    # Derivatives
    funding_8h:          float                  # raw 8h funding rate
    oi_history:          List[float]            # OI snapshots (≥ 3)
    snapshot_ts:         float                  # unix epoch of derivatives snapshot

    # Optional metadata
    extra:               Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanResult:
    symbol:        str
    score:         Optional[ScoreResult]
    skipped:       bool = False
    skip_reason:   str  = ""


# ---------------------------------------------------------------------------
# Per-symbol scan
# ---------------------------------------------------------------------------

async def scan_symbol(
    snap:       MarketSnapshot,
    btc_closes: List[float],
) -> ScanResult:
    """
    Full intelligence pipeline for a single symbol.
    Returns ScanResult with score=None if rejected early.
    """
    sym = snap.symbol
    t0  = time.monotonic()

    # 1. Freshness checks
    cf = check_candle_freshness(snap.last_candle_ts, sym)
    if not cf.is_fresh:
        return ScanResult(symbol=sym, score=None, skipped=True, skip_reason=cf.reason)

    sf = check_snapshot_freshness(snap.snapshot_ts, sym)
    if not sf.is_fresh:
        return ScanResult(symbol=sym, score=None, skipped=True, skip_reason=sf.reason)

    # 2. Hard filters (fast reject)
    all_passed, filter_results = run_all_filters(
        closes=snap.closes,
        high=snap.highs,
        low=snap.lows,
        volume_24h_usd=snap.volume_24h_usd,
        swing_origin_price=snap.swing_origin_price,
        direction=snap.direction,
        symbol=sym,
    )

    # 3. BTC regime
    regime: RegimeResult = compute_adx(snap.highs, snap.lows, snap.closes)

    # 4. Relative strength vs BTC
    rs = compute_relative_strength(sym, snap.closes, btc_closes)

    # 5. Derivatives
    funding: FundingResult = compute_funding_score(sym, snap.funding_8h, snap.direction)
    oi_accel: OIAccelerationResult = compute_oi_acceleration(sym, snap.oi_history)
    liq: LiquidationResult = estimate_liquidation_zones(
        sym,
        current_price=snap.closes[-1],
        high_history=snap.highs,
        low_history=snap.lows,
    )

    # 6. Composite score
    score = compute_score(
        symbol=sym,
        direction=snap.direction,
        closes=snap.closes,
        high=snap.highs,
        low=snap.lows,
        swing_origin=snap.swing_origin_price,
        regime=regime,
        rs=rs,
        funding=funding,
        oi_accel=oi_accel,
        liq=liq,
        filter_results=filter_results,
    )

    log.timed(
        "PERFORMANCE_LOGGED",
        f"scan complete {sym}: score={score.total:.1f} [{score.band}]",
        t0,
        symbol=sym,
        direction=snap.direction,
        score=score.total,
    )
    return ScanResult(symbol=sym, score=score)


# ---------------------------------------------------------------------------
# Full scan cycle
# ---------------------------------------------------------------------------

async def run_scan_cycle(
    snapshots:  List[MarketSnapshot],
    btc_closes: List[float],
) -> RankingResult:
    """
    Scan all symbols concurrently, collect scores, rank top 3.
    Called by the websocket_client on each scan trigger.
    """
    t0 = time.monotonic()
    log.info(
        "PERFORMANCE_LOGGED",
        f"scan cycle starting — {len(snapshots)} symbols",
    )

    # Concurrent per-symbol scans
    tasks = [scan_symbol(snap, btc_closes) for snap in snapshots]
    results: List[ScanResult] = await asyncio.gather(*tasks, return_exceptions=False)

    scores  = [r.score for r in results if r.score is not None]
    skipped = [r for r in results if r.skipped]

    if skipped:
        log.warning(
            "PERFORMANCE_LOGGED",
            f"{len(skipped)} symbols skipped (freshness/filter): "
            + ", ".join(f"{s.symbol}({s.skip_reason[:30]})" for s in skipped[:5]),
        )

    # Build lookup maps for tie-breaking
    oi_map   = {}
    fund_map = {}
    for snap in snapshots:
        oi_map[snap.symbol]   = compute_oi_acceleration(snap.symbol, snap.oi_history)
        fund_map[snap.symbol] = compute_funding_score(snap.symbol, snap.funding_8h, snap.direction)

    ranking = rank_signals(scores, max_signals=3, oi_accel_map=oi_map, funding_map=fund_map)

    summary = format_ranking_summary(ranking)
    log.info(
        "PERFORMANCE_LOGGED",
        f"scan cycle complete — top {len(ranking.top)} signals selected",
    )

    # Dual-write cycle summary to DB
    await write_system_event(
        "PERFORMANCE_LOGGED",
        f"scan cycle: {len(snapshots)} symbols, {ranking.total_valid} valid, "
        f"{len(ranking.top)} top signals",
        level="INFO",
        module="scanner",
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )

    # Print formatted summary to stdout (visible in Railway logs)
    for line in summary.splitlines():
        if line.strip():
            log.info("ALERT_SENT", line)

    return ranking


# ---------------------------------------------------------------------------
# Stub: called by websocket_client._handle_message (Phase 3 wires real data)
# ---------------------------------------------------------------------------

async def on_market_data(raw_data: dict) -> None:
    """
    Entry point from websocket_client.
    Phase 2: stub that logs receipt.
    Phase 3: parse raw_data → MarketSnapshot → run_scan_cycle.
    """
    log.debug(
        "PERFORMANCE_LOGGED",
        "market data frame received (scanner stub — Phase 3 wires full parse)",
    )
    # TODO Phase 3: parse Hyperliquid allMids/trades/funding frames
    # snapshot = _parse_hyperliquid_frame(raw_data)
    # await run_scan_cycle([snapshot], btc_closes)
