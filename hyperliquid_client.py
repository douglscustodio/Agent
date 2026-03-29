"""
hyperliquid_client.py — Hyperliquid REST API client
Fetches: candles (OHLCV), funding rates, open interest, mark prices
Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp

from logger import get_logger

log = get_logger("hyperliquid_client")

HL_REST_URL = "https://api.hyperliquid.xyz/info"
REQUEST_TIMEOUT = 15
MAX_CANDLES = 200

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Candle:
    timestamp: float    # unix epoch seconds
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float


@dataclass
class AssetMeta:
    symbol:       str
    mark_price:   float
    funding_8h:   float     # raw 8h funding rate
    open_interest: float    # in USD


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

async def _post(session: aiohttp.ClientSession, payload: dict) -> Optional[dict]:
    try:
        async with session.post(
            HL_REST_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            log.error("HEALTH_CHECK_FAIL", f"Hyperliquid REST {resp.status}: {await resp.text()}")
            return None
    except Exception as exc:
        log.error("HEALTH_CHECK_FAIL", f"Hyperliquid REST error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------

async def fetch_candles(
    session: aiohttp.ClientSession,
    symbol:  str,
    interval: str = "15m",
    count:   int = MAX_CANDLES,
) -> List[Candle]:
    """
    Fetch OHLCV candles from Hyperliquid.
    Intervals: 1m 3m 5m 15m 30m 1h 2h 4h 8h 12h 1d
    """
    end_ms   = int(time.time() * 1000)
    # Estimate start based on interval and count
    interval_ms = _interval_to_ms(interval)
    start_ms = end_ms - interval_ms * count

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin":       symbol,
            "interval":   interval,
            "startTime":  start_ms,
            "endTime":    end_ms,
        },
    }
    data = await _post(session, payload)
    if not data or not isinstance(data, list):
        log.warning("HEALTH_CHECK_FAIL", f"no candle data for {symbol}", symbol=symbol)
        return []

    candles = []
    for c in data:
        try:
            candles.append(Candle(
                timestamp=float(c["t"]) / 1000,
                open=float(c["o"]),
                high=float(c["h"]),
                low=float(c["l"]),
                close=float(c["c"]),
                volume=float(c["v"]),
            ))
        except (KeyError, ValueError, TypeError):
            continue

    log.debug(
        "PERFORMANCE_LOGGED",
        f"candles fetched {symbol}: {len(candles)} × {interval}",
        symbol=symbol,
    )
    return candles


def _interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    val  = int(interval[:-1])
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return val * multipliers.get(unit, 60_000)


# ---------------------------------------------------------------------------
# Meta (mark price, funding, OI) for all assets
# ---------------------------------------------------------------------------

async def fetch_all_metas(session: aiohttp.ClientSession) -> Dict[str, AssetMeta]:
    """
    Fetch mark prices, funding rates, and OI for all perpetuals.
    Returns dict: symbol → AssetMeta
    """
    # meta endpoint
    meta_payload  = {"type": "meta"}
    # assetContexts endpoint gives funding + OI
    ctx_payload   = {"type": "metaAndAssetCtxs"}

    data = await _post(session, ctx_payload)
    if not data or not isinstance(data, list) or len(data) < 2:
        log.error("HEALTH_CHECK_FAIL", "failed to fetch Hyperliquid metaAndAssetCtxs")
        return {}

    universe = data[0].get("universe", [])
    contexts = data[1]

    result: Dict[str, AssetMeta] = {}
    for i, asset in enumerate(universe):
        try:
            sym = asset["name"]
            ctx = contexts[i]
            result[sym] = AssetMeta(
                symbol=sym,
                mark_price=float(ctx.get("markPx", 0)),
                funding_8h=float(ctx.get("funding", 0)),
                open_interest=float(ctx.get("openInterest", 0)),
            )
        except (IndexError, KeyError, ValueError, TypeError):
            continue

    log.info(
        "PERFORMANCE_LOGGED",
        f"meta fetched: {len(result)} assets",
    )
    return result


# ---------------------------------------------------------------------------
# Volume (24h) from trades
# ---------------------------------------------------------------------------

async def fetch_volume_24h(
    session: aiohttp.ClientSession,
    symbol:  str,
) -> float:
    """Estimate 24h volume in USD from 1h candles × 24."""
    candles = await fetch_candles(session, symbol, interval="1h", count=24)
    if not candles:
        return 0.0
    # volume in USD ≈ volume_base × close_price
    total = sum(c.volume * c.close for c in candles)
    return round(total, 2)


# ---------------------------------------------------------------------------
# Bulk candle fetch (all symbols concurrently)
# ---------------------------------------------------------------------------

async def fetch_all_candles(
    symbols:  List[str],
    interval: str = "15m",
    count:    int = MAX_CANDLES,
) -> Dict[str, List[Candle]]:
    """
    Fetch candles for all symbols concurrently.
    Returns dict: symbol → List[Candle]
    """
    async with aiohttp.ClientSession() as session:
        tasks = {sym: fetch_candles(session, sym, interval, count) for sym in symbols}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    output: Dict[str, List[Candle]] = {}
    for sym, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            log.error("HEALTH_CHECK_FAIL", f"candle fetch failed {sym}: {result}", symbol=sym)
            output[sym] = []
        else:
            output[sym] = result

    return output
