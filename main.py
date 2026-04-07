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
from analyst import get_analyst
from chatbot import JarvisChatbot
from macro_intelligence import MacroEngine
from btc_regime import RegimeResult
from database import init_db, close_db, write_system_event, flush_event_buffer
from data_quality import get_current_quality
from logger import get_logger
from news_engine import NewsEngine, NewsArticle, NewsContext
from notifier import Notifier
from performance_tracker import PerformanceTracker
from portfolio_risk import PortfolioRiskManager
from ranking import RankingResult
from kill_switch import KillSwitch
from proactive_agent import ProactiveAgent
from scanner import run_scan_cycle, get_symbols
from scheduler import build_scheduler
from websocket_client import run_websocket_client, ws_state

log = get_logger("main")

_state_lock = asyncio.Lock()

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
        self.risk_manager:          PortfolioRiskManager = PortfolioRiskManager()
        self.kill_switch:           KillSwitch          = KillSwitch()
        self.proactive_agent:       Optional[ProactiveAgent] = ProactiveAgent()

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
_chatbot: Optional[JarvisChatbot] = None


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
    ctx.btc_closes = closes[-20:]
    ctx.btc_highs = highs[-20:]
    ctx.btc_lows = lows[-20:]
    
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

    results = await asyncio.gather(
        _fetch_news(),
        ctx.macro.refresh(),
        return_exceptions=True,
    )
    articles = results[0] if isinstance(results[0], list) else ctx.latest_articles or []
    if isinstance(articles, list):
        ctx.latest_articles = articles
    if results[1] and isinstance(results[1], Exception):
        log.warning("MACRO_REFRESH_FAIL", f"macro refresh failed: {results[1]}")
    ctx.last_news_ts = _now_iso()
    log.timed("NEWS_FETCH_COMPLETE", f"news+macro parallel: {len(ctx.latest_articles)} articles", t0)


async def job_scan_cycle() -> None:
    """Full scan + ranking + notify every 5 min."""
    t0 = time.monotonic()

    current_ws_status = ws_state.get("status", "DISCONNECTED")
    if current_ws_status != "CONNECTED":
        log.warning("SCAN_SKIP", f"WS status={current_ws_status} — continuing with REST API data")

    if not ctx.kill_switch.can_trade():
        status = ctx.kill_switch.get_status()
        log.critical("KILL_SWITCH", f"Trading blocked: {status.reason}")
        if _chatbot and _chatbot._alert_chat_id:
            await _chatbot.send_alert(f"🛑 *KILL SWITCH ATIVO*\n\n{status.reason}\n\nTrades bloqueados até reset.")
        return

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

    # Get macro snapshot
    macro_snap = ctx.macro.get_snapshot()

    # PORTFOLIO RISK: Filter signals through risk manager
    approved_signals, rejected_signals = ctx.risk_manager.filter_signals(
        ranking.top, macro_snap
    )
    
    # Update ranking with approved signals only
    if len(approved_signals) < len(ranking.top):
        log.warning("SCAN_RISK", f"Risk manager blocked {len(ranking.top) - len(approved_signals)} signals")
        ranking.top = approved_signals
        ranking.total_valid = len(approved_signals)

    if not ranking.top:
        log.info("SCAN_COMPLETE", "No signals after risk filtering")
        return

    # Dispatch alerts
    await ctx.notifier.dispatch(ranking, {}, macro_snap=macro_snap)

    # PROATIVO: Enviar alertas via chatbot se tiver sinais
    if ranking.top and _chatbot:
        log.info("CHATBOT_ALERT", f"sending proactive alerts for {len(ranking.top)} signals")
        for sig in ranking.top[:2]:
            reason = ""
            if "relative_strength" in sig.components:
                reason = f"Força relativa: {sig.components['relative_strength']:.0f}"
            try:
                await _chatbot.alert_signal(
                    symbol=sig.symbol,
                    direction=sig.direction,
                    score=sig.score,
                    reason=reason,
                )
            except Exception as exc:
                log.error("CHATBOT_ALERT", f"failed to send signal alert: {exc}")

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
    snap = ctx.macro.get_snapshot()
    if snap:
        high_neg = [e for e in snap.events if e.impact == 'HIGH' and e.sentiment == 'negative']
        if high_neg and _chatbot:
            from macro_intelligence import _translate_event_title
            translated_event = _translate_event_title(high_neg[0].title[:100])
            await _chatbot.alert_macro_risk(
                risk_score=snap.risk_score,
                event=translated_event,
            )


async def job_market_report() -> None:
    """Send hourly market report to Telegram."""
    await ctx.notifier.send_market_report()


async def job_btc_spike() -> None:
    """Check BTC for sudden moves every 5 min."""
    await ctx.notifier.check_btc_spike()
    if _chatbot and _chatbot._alert_chat_id and ctx.btc_closes and len(ctx.btc_closes) >= 2:
        btc_price = _get_price("BTC")
        if btc_price > 0:
            prev_price = ctx.btc_closes[0]
            change_pct = ((btc_price - prev_price) / prev_price) * 100
            if abs(change_pct) >= 3:
                direction = "SUBIU" if change_pct > 0 else "CAIU"
                await _chatbot.alert_btc_spike(
                    direction=direction,
                    pct=change_pct,
                    price=btc_price,
                )


async def job_daily_summary() -> None:
    """Send daily summary at midnight UTC."""
    await ctx.notifier.send_daily_summary(tracker=ctx.tracker)
    if _chatbot:
        _chatbot.reset_daily_alerts()
        try:
            stats = await ctx.tracker.get_recent_stats(days=1)
            await _chatbot.alert_daily_summary(stats)
        except Exception:
            pass


async def job_adaptive_tune() -> None:
    """Re-tune scoring weights every 24 hours."""
    await ctx.adaptive.adapt(ctx.tracker)


async def job_analyst_pulse() -> None:
    """Send analyst market pulse every 15 min."""
    try:
        analyst = get_analyst()
        btc_price = _get_price("BTC") or 0
        
        if ctx.latest_regime:
            context = analyst.analyze_market(
                btc_price=btc_price,
                btc_closes=ctx.btc_closes[-20:] if ctx.btc_closes else [],
                regime_result=ctx.latest_regime,
                macro_snap=ctx.macro.get_snapshot(),
            )
            
            from analyst import format_market_pulse
            msg = format_market_pulse(context)
            
            if _chatbot and _chatbot._alert_chat_id:
                await _chatbot.send_alert(msg)
                log.info("ANALYST", "market pulse sent")
    except Exception as exc:
        log.error("ANALYST_ERROR", f"pulse failed: {exc}")


async def job_analyst_briefing() -> None:
    """Send analyst briefing every 4 hours."""
    try:
        analyst = get_analyst()
        btc_price = _get_price("BTC") or 0
        
        if ctx.latest_regime:
            context = analyst.analyze_market(
                btc_price=btc_price,
                btc_closes=ctx.btc_closes[-20:] if ctx.btc_closes else [],
                regime_result=ctx.latest_regime,
                macro_snap=ctx.macro.get_snapshot(),
            )
            
            from analyst import format_daily_briefing
            signals = ctx.latest_ranking.top if ctx.latest_ranking else None
            msg = format_daily_briefing(context, signals)
            
            if _chatbot and _chatbot._alert_chat_id:
                await _chatbot.send_alert(msg)
                log.info("ANALYST", "briefing sent")
    except Exception as exc:
        log.error("ANALYST_ERROR", f"briefing failed: {exc}")


async def job_ai_learn_suggest() -> None:
    """Aprende com trades passados e envia sugestões proativas."""
    try:
        proactive = ctx.proactive_agent if hasattr(ctx, 'proactive_agent') else ProactiveAgent()
        
        records = await ctx.tracker.get_recent_performance(days=7)
        
        if records:
            await proactive.learn_from_outcomes(records)
            
            if proactive.should_suggest_trade() and ctx.latest_ranking and ctx.latest_ranking.top:
                opportunities = [
                    {
                        "symbol": s.symbol,
                        "direction": s.direction,
                        "score": s.score,
                        "band": str(s.band),
                    }
                    for s in ctx.latest_ranking.top[:3]
                ]
                
                msg = proactive.format_ai_trade_suggestion({}, opportunities)
                if msg and _chatbot and _chatbot._alert_chat_id:
                    await _chatbot.send_alert(msg)
                    proactive.mark_suggestion_sent()
                    log.info("PROACTIVE", "AI trade suggestion sent")
    except Exception as exc:
        log.error("PROACTIVE_ERROR", f"AI learning suggestion failed: {exc}")


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

    # ── 6. WebSocket client ──────────────────────────────────────────────
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
        ai_learn_fn=    job_ai_learn_suggest,     # UPGRADE: AI learning + suggestions
    )
    log.info("SCHEDULER_JOBS", f"Jobs registered: {sched.get_job_ids()}")
    sched.start()
    
    # REMOVIDO: Jobs repetitivos - muito frequentes e repetitivos
    # sched.add_interval_job(job_analyst_pulse, minutes=15, name="analyst_pulse", jitter=30)
    # sched.add_interval_job(job_analyst_briefing, minutes=0, name="analyst_briefing", hours=4, jitter=300)

    await write_system_event(
        "SYSTEM_READY",
        "all subsystems running — scheduler active",
        level="INFO", module="main",
    )
    log.info("SYSTEM_READY", "=== Crypto Monitor fully operational ===")

    # ── 9. Chatbot (Telegram) ───────────────────────────────────────────
    global _chatbot
    _chatbot = None
    if tg_token and tg_chat_id:
        _chatbot = JarvisChatbot(tg_token)
        _chatbot.set_alert_chat_id(tg_chat_id)
        _chatbot.set_system_refs(
            scanner_module=run_scan_cycle,
            news_engine=ctx.news_engine,
            macro_engine=ctx.macro,
            tracker=ctx.tracker,
            risk_manager=ctx.risk_manager,
            kill_switch=ctx.kill_switch,
            last_ranking=ctx.latest_ranking,
        )
        log.info("CHATBOT_READY", f"chatbot init - token={bool(tg_token)} chat_id={bool(tg_chat_id)}")
        
        await asyncio.sleep(2)
        welcome = await _chatbot.handle_message("/start", tg_chat_id)
        log.info("CHATBOT_WELCOME", f"sending welcome: {len(welcome)} chars")
        sent = await _chatbot._send_message(tg_chat_id, welcome)
        log.info("CHATBOT_INIT", f"welcome sent: {sent}")

    # ── 10. Message handling loop ────────────────────────────────────────
    async def chat_loop():
        if not _chatbot:
            log.warning("CHATBOT_LOOP", "no chatbot configured")
            return
        log.info("CHATBOT_LOOP", "chat loop started - waiting for messages")
        poll_count = 0
        while not _shutdown_event.is_set():
            try:
                poll_count += 1
                if poll_count % 10 == 0:
                    log.debug("CHATBOT_POLL", f"still polling... count={poll_count}")
                
                result = await asyncio.wait_for(_chatbot.poll(), timeout=35)
                
                if result is None:
                    continue
                
                text = result.get("text", "")
                user_chat_id = result.get("chat_id", "")
                non_text = result.get("non_text", False)
                
                if not user_chat_id:
                    log.warning("CHATBOT_MSG", "no chat_id in result")
                    continue
                
                log.info("CHATBOT_MSG", f"user={user_chat_id} text='{text[:30]}...' non_text={non_text}")
                
                if non_text:
                    response = "📎 Desculpe, só consigo ler mensagens de texto!\n\nDigite /help para ver comandos."
                else:
                    from scanner import run_scan_cycle as _scan
                    ctx.latest_ranking = await _scan() if ctx.latest_ranking is None else ctx.latest_ranking
                    _chatbot.set_system_refs(last_ranking=ctx.latest_ranking)
                    response = await _chatbot.handle_message(text, user_chat_id)
                
                if response:
                    sent = await _chatbot._send_message(user_chat_id, response)
                    log.info("CHATBOT_RESPONSE", f"sent={sent} to {user_chat_id}")
                    
            except asyncio.TimeoutError:
                log.debug("CHATBOT_POLL", "timeout - no messages, continuing")
            except Exception as exc:
                log.error("CHATBOT_ERROR", f"error: {exc}")
                await asyncio.sleep(5)

    chat_task = asyncio.create_task(chat_loop()) if _chatbot else None

    # ── 11. Run until signal ─────────────────────────────────────────────
    await _shutdown_event.wait()

    # ── 10. Graceful teardown ────────────────────────────────────────────
    log.info("SYSTEM_START", "initiating graceful shutdown")

    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

    if chat_task:
        chat_task.cancel()
        try:
            await chat_task
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
