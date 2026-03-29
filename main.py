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
from btc_regime import compute_adx, RegimeResult, Regime
from config import config
from database import init_db, close_db, write_system_event
from health_server import run_health_server, app_state
from logger import get_logger
from news_engine import NewsEngine, NewsArticle, NewsContext
from notifier import Notifier
from performance_tracker import PerformanceTracker
from ranking import RankingResult
from scanner import run_scan_cycle, MarketSnapshot
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

        # Live data caches (populated by WS + scan loops)
        self.latest_articles:      List[NewsArticle]    = []
        self.latest_ranking:       Optional[RankingResult] = None
        self.btc_closes:           List[float]          = []
        self.btc_highs:            List[float]          = []
        self.btc_lows:             List[float]          = []
        self.latest_regime:        Optional[RegimeResult]  = None
        self.snapshots:            List[MarketSnapshot] = []
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
    """Refresh BTC regime every 2 min."""
    if len(ctx.btc_closes) < 30:
        log.debug("PERFORMANCE_LOGGED", "btc_regime: not enough candles yet")
        return

    regime = compute_adx(ctx.btc_highs, ctx.btc_lows, ctx.btc_closes)
    ctx.latest_regime  = regime
    ctx.last_regime_ts = _now_iso()

    log.info(
        "PERFORMANCE_LOGGED",
        f"BTC regime: {regime.regime} ADX={regime.adx:.2f} dir={regime.trend_direction}",
    )


async def job_news_fetch() -> None:
    """Refresh news cache every 3 min."""
    t0       = time.monotonic()
    articles = await ctx.news_engine.fetch_all()
    ctx.latest_articles = articles
    ctx.last_news_ts    = _now_iso()
    app_state["last_scan_timestamp"] = ctx.last_news_ts

    log.timed(
        "PERFORMANCE_LOGGED",
        f"news fetch: {len(articles)} articles",
        t0,
    )


async def job_scan_cycle() -> None:
    """Full scan + ranking + notify every 5 min."""
    if not ctx.snapshots:
        log.debug("PERFORMANCE_LOGGED", "scan_cycle: no snapshots yet")
        return

    t0 = time.monotonic()

    # Inject adaptive weights into scoring module
    live_weights = ctx.adaptive.get_weights()
    _patch_scoring_weights(live_weights)

    # Run full scan
    ranking = await run_scan_cycle(ctx.snapshots, ctx.btc_closes)
    ctx.latest_ranking       = ranking
    ctx.last_scan_ts         = _now_iso()
    app_state["last_scan_timestamp"] = ctx.last_scan_ts

    # Build news context map for top signals
    news_map: Dict[str, Optional[NewsContext]] = {}
    for sig in ranking.top:
        news_map[sig.symbol] = ctx.news_engine.get_context_for_symbol(
            sig.symbol, ctx.latest_articles
        )

    # Sector rotation bonus (modifies scores before notify)
    if ranking.top:
        symbol_scores = {s.symbol: s.score for s in ranking.top}
        symbol_news   = {s.symbol: (news_map.get(s.symbol) or _empty_news(s.symbol)).impact_score
                         for s in ranking.top}
        sector_result = compute_sector_rotation(symbol_scores, symbol_news)

        # Apply sector bonus to scores
        for sig in ranking.top:
            bonus = sector_result.sector_bonus.get(sig.symbol, 0.0)
            if bonus > 0:
                sig.result.total = min(sig.result.total + bonus, 100.0)
                log.debug(
                    "PERFORMANCE_LOGGED",
                    f"sector bonus +{bonus} applied to {sig.symbol}",
                    symbol=sig.symbol, score=sig.result.total,
                )

    # Dispatch alerts
    await ctx.notifier.dispatch(ranking, news_map)

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
            )

    log.timed("PERFORMANCE_LOGGED", f"scan cycle done: {len(ranking.top)} signals", t0)


async def job_performance_checks() -> None:
    """Check alert outcomes every hour."""
    await ctx.tracker.run_checks()


async def job_adaptive_tune() -> None:
    """Re-tune scoring weights every 24 hours."""
    await ctx.adaptive.adapt(ctx.tracker)


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
        log.error("PERFORMANCE_LOGGED", f"weight patch failed: {exc}")


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
    log.info("SYSTEM_START", "=== Crypto Monitor starting (Phase 4) ===")
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
    log.info("SYSTEM_START", f"Telegram config — token={bool(tg_token)} chat_id={bool(tg_chat_id)}")
    ctx.notifier = Notifier(telegram_token=tg_token, telegram_chat_id=tg_chat_id)
    await ctx.notifier.startup()
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

    await sched.shutdown()

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
