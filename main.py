"""
main.py — Final integrated entry point (Phase 1–4)
Startup order:
  1. DB pool + schema
  2. Performance tracker
  3. Adaptive engine (restore weights)
  4. News engine
  5. Notifier + dedup table
  6. Health server
  7. WebSocket client (background)
  8. APScheduler (all loops)
  9. Graceful shutdown on SIGINT/SIGTERM
"""

import asyncio
import os
import signal
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from adaptive import AdaptiveEngine
from macro_intelligence import MacroEngine
from memory_engine import MemoryEngine
from btc_regime import RegimeResult
from config import config
from database import init_db, close_db, write_system_event, flush_event_buffer
from health_server import run_health_server, app_state
from logger import get_logger
from news_engine import NewsEngine, NewsArticle, NewsContext
from notifier import Notifier
from performance_tracker import PerformanceTracker
from ranking import RankingResult
from scanner import run_scan_cycle, get_symbols
from scheduler import build_scheduler
from sector_rotation import compute_sector_rotation
from websocket_client import run_websocket_client, ws_state

log = get_logger("main")


# ---------------------------------------------------------------------------
# Global shared state — all loops read/write this
# ---------------------------------------------------------------------------

class AppContext:
    def __init__(self) -> None:
        self.news_engine:          NewsEngine           = NewsEngine(
            cryptopanic_token=os.getenv("CRYPTOPANIC_TOKEN")
        )
        self.notifier:             Optional[Notifier]   = None
        self.tracker:              PerformanceTracker   = PerformanceTracker()
        self.adaptive:             AdaptiveEngine       = AdaptiveEngine()
        self.macro:                MacroEngine          = MacroEngine()
        self.memory:               MemoryEngine         = MemoryEngine()

        # Live data caches (populated by WS + scan loops)
        self.latest_articles:      List[NewsArticle]    = []
        self.latest_ranking:       Optional[RankingResult] = None
        self.btc_closes:           List[float]          = []
        self.btc_highs:            List[float]          = []
        self.btc_lows:             List[float]          = []
        self.latest_regime:        Optional[RegimeResult]  = None
        self.last_scan_ts:         Optional[str]        = None
        self.last_news_ts:         Optional[str]        = None
        self.last_regime_ts:       Optional[str]        = None


ctx = AppContext()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Scheduled job implementations
# ---------------------------------------------------------------------------

async def job_btc_regime() -> None:
    """Refresh BTC regime every 2 min from Hyperliquid candles."""
    from btc_regime import compute_adx
    from hyperliquid_client import fetch_all_candles
    candle_map = await fetch_all_candles(["BTC"], interval="15m", count=100)
    candles    = candle_map.get("BTC", [])
    if len(candles) < 30:
        log.debug("BTC_REGIME_UPDATED", "btc_regime: not enough BTC candles")
        return
    highs  = [c.high  for c in candles]
    lows   = [c.low   for c in candles]
    closes = [c.close for c in candles]
    regime = compute_adx(highs, lows, closes)
    ctx.latest_regime  = regime
    ctx.last_regime_ts = _now_iso()
    log.info(
        "BTC_REGIME_UPDATED",
        f"BTC regime: {regime.regime} ADX={regime.adx:.2f} dir={regime.trend_direction}",
    )


async def job_news_fetch() -> None:
    """Refresh news + macro in parallel every 3 min."""
    t0 = time.monotonic()
    async def _fetch_news():
        try:
            return await asyncio.wait_for(ctx.news_engine.fetch_all(), timeout=8.0)
        except asyncio.TimeoutError:
            log.warning("NEWS_TIMEOUT", "news fetch timed out — using cache")
            return ctx.latest_articles or []

    articles, _ = await asyncio.gather(
        _fetch_news(),
        ctx.macro.refresh(),
        return_exceptions=True,
    )
    if isinstance(articles, list):
        ctx.latest_articles = articles
    ctx.last_news_ts = _now_iso()
    app_state["last_scan_timestamp"] = ctx.last_news_ts
    log.timed("NEWS_FETCH_COMPLETE", f"news+macro parallel: {len(ctx.latest_articles)} articles", t0)


async def job_scan_cycle() -> None:
    """Full scan + ranking + notify every 5 min."""
    t0 = time.monotonic()

    # Guard: skip high-confidence trades if WS is not connected
    current_ws_status = ws_state.get("status", "DISCONNECTED")
    if current_ws_status != "CONNECTED":
        log.warning("SCAN_SKIP", f"WS status={current_ws_status} — skipping scan cycle")
        return

    # Get adaptive weights (learned from performance history)
    live_weights = ctx.adaptive.get_weights()

    # Compute sector heat scores from latest news
    # This feeds narrative momentum into per-symbol scores
    sector_heat_map = ctx.news_engine.get_sector_heat_scores(
        ctx.latest_articles
    ) if ctx.latest_articles else {}
    if sector_heat_map:
        hot = {k: v for k, v in sector_heat_map.items() if v > 65}
        cold = {k: v for k, v in sector_heat_map.items() if v < 35}
        log.info(
            "SCAN_SECTOR_HEAT",
            f"sector heat: hot={list(hot.keys())} cold={list(cold.keys())}",
        )

    # Run full scan — weights + sector heat + vol confirm all injected inside
    try:
        ranking = await asyncio.wait_for(
            run_scan_cycle(
                adaptive_weights=live_weights,
                sector_heat_map=sector_heat_map,
            ),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        log.error("SCAN_TIMEOUT", "scan cycle timed out after 25s — skipping")
        return
    ctx.latest_ranking       = ranking
    ctx.last_scan_ts         = _now_iso()
    app_state["last_scan_timestamp"] = ctx.last_scan_ts

    # Build news context map for top signals
    news_map: Dict[str, Optional[NewsContext]] = {}
    for sig in ranking.top:
        news_map[sig.symbol] = ctx.news_engine.get_context_for_symbol(
            sig.symbol, ctx.latest_articles
        )

    # Sector rotation (for logging/reporting only — heat already applied in scanner)
    if ranking.top:
        symbol_scores = {s.symbol: s.score for s in ranking.top}
        symbol_news   = {s.symbol: (news_map.get(s.symbol) or _empty_news(s.symbol)).impact_score
                         for s in ranking.top}
        sector_result = compute_sector_rotation(symbol_scores, symbol_news)
        log.info(
            "SCAN_SECTOR_ROTATION",
            f"sector rotation: hot={sector_result.hot_sectors} cold={sector_result.cold_sectors[:2]}",
        )

    # Get macro snapshot and memory insights
    macro_snap = ctx.macro.get_snapshot()

    # Dispatch alerts
    await ctx.notifier.dispatch(ranking, news_map, macro_snap=macro_snap, memory=ctx.memory)

    # Register sent alerts for performance tracking
    for sig in ranking.top:
        price = _get_price(sig.symbol)
        if price:
            await ctx.tracker.register_alert(
                alert_id=f"{sig.symbol}:{sig.direction}:{ctx.last_scan_ts}",
                symbol=sig.symbol,
                direction=sig.direction,
                score=sig.score,
                entry_price=price,
                dominant_component=sig.result.dominant_component,   # UPGRADE: feedback loop
            )

    log.timed("SCAN_COMPLETE", f"scan cycle done: {len(ranking.top)} signals", t0)


async def job_performance_checks() -> None:
    """Check alert outcomes every hour."""
    await ctx.tracker.run_checks()


async def job_macro_refresh() -> None:
    """Refresh macro data every 30 min."""
    await ctx.macro.refresh()
    # Check for high-impact macro events and alert
    snap = ctx.macro.get_snapshot()
    if snap:
        high_neg = [e for e in snap.events if e.impact == 'HIGH' and e.sentiment == 'negative']
        if high_neg:
            await ctx.notifier.send_system_alert(
                f'\U0001f534 *ALERTA MACRO*\n'
                f'Evento de alto impacto detectado:\n'
                f'• {high_neg[0].title[:100]}'
            )


async def job_market_report() -> None:
    """Send hourly market report to Telegram."""
    await ctx.notifier.send_market_report()


async def job_btc_spike() -> None:
    """Check BTC for sudden moves every 5 min."""
    await ctx.notifier.check_btc_spike()


async def job_daily_summary() -> None:
    """Send daily summary at midnight UTC."""
    await ctx.notifier.send_daily_summary(tracker=ctx.tracker)


async def job_adaptive_tune() -> None:
    """Re-tune scoring weights every 24 hours."""
    await ctx.adaptive.adapt(ctx.tracker)


async def job_flush_events() -> None:
    """Flush buffered DB events every 15 seconds."""
    await flush_event_buffer()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_scoring_weights(weights: Dict[str, float]) -> None:
    """Inject live weights into the scoring module's global WEIGHTS dict."""
    try:
        import scoring
        for k, v in weights.items():
            if k in scoring.WEIGHTS:
                scoring.WEIGHTS[k] = v
    except Exception as exc:
        log.error("WEIGHT_UPDATE_FAIL", f"weight patch failed: {exc}")


def _get_price(symbol: str) -> Optional[float]:
    try:
        from websocket_client import ws_price_cache
        return ws_price_cache.get(symbol)
    except Exception:
        return None


def _empty_news(symbol: str):
    from news_engine import NewsContext
    return NewsContext(
        symbol=symbol,
        articles=[],
        aggregate_sentiment="neutral",
        impact_score=0.0,
        top_headline="No news",
        freshness_minutes=999.0,
    )


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_event = asyncio.Event()


def _handle_signal(sig) -> None:
    log.warning("SYSTEM_START", f"received {sig.name} — shutting down")
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("SYSTEM_START", "=== Jarvis AI Trading Monitor iniciando ===")
    ai_key = bool(os.getenv("GROQ_API_KEY"))
    ai_status = "habilitada" if ai_key else "desabilitada — configure GROQ_API_KEY"
    log.info("SYSTEM_START", f"IA Groq: {ai_status}")
    app_state["started_at"] = _now_iso()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # ── 1. Database ──────────────────────────────────────────────────────
    try:
        await init_db()
    except RuntimeError as exc:
        log.critical("SYSTEM_START", f"DB startup failed: {exc}")
        return

    # ── 2. Performance tracker ───────────────────────────────────────────
    await ctx.tracker.startup()

    # ── 3. Adaptive engine ───────────────────────────────────────────────
    await ctx.adaptive.startup()
    await ctx.memory.startup()
    await ctx.macro.refresh()

    # ── 4. News engine (warm cache) ──────────────────────────────────────
    try:
        ctx.latest_articles = await ctx.news_engine.fetch_all()
        ctx.last_news_ts    = _now_iso()
        log.info("SYSTEM_READY", f"news cache warm: {len(ctx.latest_articles)} articles")
    except Exception as exc:
        log.warning("NEWS_PRIMARY_FAIL", f"initial news fetch failed: {exc}")

    # ── 5. Notifier ──────────────────────────────────────────────────────
    tg_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not tg_token:
        log.warning("SYSTEM_START", "TELEGRAM_BOT_TOKEN não configurada — alertas Telegram desabilitados")
    if not tg_chat_id:
        log.warning("SYSTEM_START", "TELEGRAM_CHAT_ID não configurada — alertas Telegram desabilitados")
    log.info("SYSTEM_START", f"Telegram config — token={bool(tg_token)} chat_id={bool(tg_chat_id)}")
    ctx.notifier = Notifier(telegram_token=tg_token, telegram_chat_id=tg_chat_id)
    await ctx.notifier.startup()
    ctx.notifier.set_news_engine(ctx.news_engine)
    await ctx.notifier.send_system_alert(
        "🟢 *Crypto Monitor online*\nAll subsystems initialised. Scanner active."
    )
    log.info("SYSTEM_READY", "startup Telegram alert dispatched")

    # ── 6. Health server ─────────────────────────────────────────────────
    await run_health_server()

    # ── 7. WebSocket client ──────────────────────────────────────────────
    ws_task = asyncio.create_task(run_websocket_client())

    # ── 8. Scheduler ─────────────────────────────────────────────────────
    sched = build_scheduler(
        scan_fn=        job_scan_cycle,
        news_fn=        job_news_fetch,
        regime_fn=      job_btc_regime,
        performance_fn= job_performance_checks,
        adaptive_fn=    job_adaptive_tune,
        market_report_fn= job_market_report,
        macro_refresh_fn= job_macro_refresh,
        btc_spike_fn=   job_btc_spike,
        daily_summary_fn= job_daily_summary,
        flush_fn=       job_flush_events,          # UPGRADE: batch DB writes
    )
    sched.start()

    await write_system_event(
        "SYSTEM_READY",
        "all subsystems running — scheduler active",
        level="INFO", module="main",
    )
    log.info("SYSTEM_READY", "=== Crypto Monitor fully operational ===")

    # ── 9. Run until signal ──────────────────────────────────────────────
    await _shutdown_event.wait()

    # ── 10. Graceful teardown ────────────────────────────────────────────
    log.info("SYSTEM_START", "initiating graceful shutdown")

    await ctx.notifier.send_system_alert("🔴 *Crypto Monitor offline*\nShutting down.")

  

    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

    await close_db()

    await write_system_event(
        "SYSTEM_START", "shutdown complete",
        level="INFO", module="main",
    )
    log.info("SYSTEM_START", "=== shutdown complete ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
