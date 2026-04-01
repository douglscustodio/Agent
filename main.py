"""
main.py — Jarvis AI Trading Monitor - Sistema Proativo

O Jarvis é um agente que:
- Monitora 24/7 e te mantém antenado SEM você precisar perguntar
- Alerta sobre mudanças de mercado, regime, notícias importantes
- Mostra oportunidades automaticamente
- Envia pulso de mercado periódico
- Detecta squeezes e volatilidade

Startup order:
  1. DB pool + schema
  2. Performance tracker
  3. Adaptive engine
  4. News engine
  5. Notifier + Chatbot
  6. Proactive agent
  7. Health server
  8. WebSocket client
  9. APScheduler (all loops)
  10. Graceful shutdown
"""

import asyncio
import os
import signal
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from adaptive import AdaptiveEngine
from chatbot import JarvisChatbot
from macro_intelligence import MacroEngine
from memory_engine import MemoryEngine
from btc_regime import RegimeResult
from config import config
from database import init_db, close_db, write_system_event, flush_event_buffer
from data_quality import get_current_quality
from health_server import run_health_server, app_state
from logger import get_logger
from news_engine import NewsEngine, NewsArticle, NewsContext
from notifier import Notifier
from performance_tracker import PerformanceTracker
from portfolio_risk import PortfolioRiskManager
from ranking import RankingResult
from kill_switch import KillSwitch
from proactive_agent import (
    get_proactive_agent,
    format_market_pulse_message,
    format_regime_change_message,
    format_important_news_alert,
    format_opportunity_summary,
    format_funding_alert,
    format_exit_signal_alert,
    format_performance_dashboard,
    format_sentiment_summary,
)
from scanner import run_scan_cycle, get_symbols
from scheduler import build_scheduler
from sector_rotation import compute_sector_rotation
from websocket_client import run_websocket_client, ws_state

log = get_logger("main")

_state_lock = asyncio.Lock()

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
        self.risk_manager:         PortfolioRiskManager = PortfolioRiskManager()
        self.kill_switch:          KillSwitch          = KillSwitch()
        
        self.latest_articles:      List[NewsArticle]       = []
        self.latest_ranking:       Optional[RankingResult]  = None
        self.btc_closes:           List[float]              = []
        self.btc_highs:            List[float]              = []
        self.btc_lows:             List[float]              = []
        self.latest_regime:        Optional[RegimeResult]  = None
        self.last_scan_ts:         Optional[str]            = None
        self.last_news_ts:         Optional[str]            = None
        self.last_regime_ts:       Optional[str]            = None
        self.btc_price:            float = 0.0
        self._last_pulse_sent:     float = 0.0


ctx = AppContext()
_chatbot: Optional[JarvisChatbot] = None
_proactive = get_proactive_agent()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _get_btc_price() -> float:
    """Busca preço do BTC de forma segura."""
    try:
        from websocket_client import ws_price_cache
        return ws_price_cache.get("BTC") or 0.0
    except Exception:
        return 0.0


# ============================================================================
# JOBS PROATIVOS
# ============================================================================

async def job_btc_regime() -> None:
    """Atualiza regime do BTC e detecta mudanças."""
    from btc_regime import compute_adx
    from hyperliquid_client import fetch_all_candles
    
    try:
        candle_map = await fetch_all_candles(["BTC"], interval="15m", count=100)
        candles    = candle_map.get("BTC", [])
        
        if len(candles) < 30:
            log.debug("BTC_REGIME_UPDATED", "btc_regime: not enough BTC candles")
            return
        
        highs  = [c.high  for c in candles]
        lows   = [c.low   for c in candles]
        closes = [c.close for c in candles]
        regime = compute_adx(highs, lows, closes)
        
        ctx.latest_regime   = regime
        ctx.last_regime_ts  = _now_iso()
        ctx.btc_closes      = closes[-20:]
        ctx.btc_highs       = highs[-20:]
        ctx.btc_lows        = lows[-20:]
        ctx.btc_price       = closes[-1]
        
        regime_str = str(regime.regime).split(".")[-1]
        
        if _proactive.should_alert_regime_change(regime_str) and _chatbot:
            msg = format_regime_change_message(
                new_regime=regime_str,
                direction=regime.trend_direction,
                strength=regime.adx,
                previous=regime_str,
            )
            await _chatbot.send_alert(msg)
            log.info("PROACTIVE_ALERT", f"regime change alerted: {regime_str}")
        
        log.info(
            "BTC_REGIME_UPDATED",
            f"BTC regime: {regime_str} ADX={regime.adx:.2f} dir={regime.trend_direction}",
        )
    except Exception as exc:
        log.error("BTC_REGIME_ERROR", f"failed to update regime: {exc}")


async def job_news_fetch() -> None:
    """Atualiza notícias e macro."""
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
        
        if _chatbot and articles:
            for article in articles[:5]:
                title = getattr(article, "title", "")
                sentiment = getattr(article, "sentiment", "neutral")
                url = getattr(article, "url", "") or ""
                news_key = f"{title[:50]}:{sentiment}"
                
                if _proactive.should_alert_news(news_key):
                    from proactive_agent import format_important_news_alert
                    if "sec" in title.lower() or "etf" in title.lower() or "federal" in title.lower():
                        msg = format_important_news_alert(title, sentiment, url)
                        await _chatbot.send_alert(msg)
                        log.info("PROACTIVE_NEWS", f"high impact news alerted: {title[:50]}")

    ctx.last_news_ts = _now_iso()
    app_state["last_scan_timestamp"] = ctx.last_news_ts
    log.timed("NEWS_FETCH_COMPLETE", f"news+macro: {len(ctx.latest_articles)} articles", t0)


async def job_scan_cycle() -> None:
    """Scan completo + ranking + alertas proativos."""
    t0 = time.monotonic()

    current_ws_status = ws_state.get("status", "DISCONNECTED")
    if current_ws_status != "CONNECTED":
        log.warning("SCAN_SKIP", f"WS status={current_ws_status} — skipping scan")
        return

    quality = get_current_quality()
    if quality.should_block_signals:
        log.error("SCAN_SKIP", f"Data quality blocked: {quality.quality_label}")
        if _chatbot and quality.warnings:
            from proactive_agent import format_volatility_alert
            warning_msg = "\n".join([f"• {w}" for w in quality.warnings[:2]])
            await _chatbot.send_alert(f"⚠️ *QUALIDADE DOS DADOS*\n\n{warning_msg}\n\nSinais bloqueados temporariamente.")
        return

    if not ctx.kill_switch.can_trade():
        status = ctx.kill_switch.get_status()
        log.critical("KILL_SWITCH", f"Trading blocked: {status.reason}")
        if _chatbot:
            await _chatbot.send_alert(
                f"🛑 *KILL SWITCH ATIVO*\n\n{status.reason}\n\n"
                f"P&L Diário: {status.daily_pnl_pct*100:+.2f}%\n"
                f"Perdas consecutivas: {status.consecutive_losses}\n\n"
                "Trades bloqueados até reset."
            )
        return

    live_weights = ctx.adaptive.get_weights()

    sector_heat_map = ctx.news_engine.get_sector_heat_scores(
        ctx.latest_articles
    ) if ctx.latest_articles else {}

    try:
        ranking = await asyncio.wait_for(
            run_scan_cycle(
                adaptive_weights=live_weights,
                sector_heat_map=sector_heat_map,
            ),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        log.error("SCAN_TIMEOUT", "scan cycle timed out after 25s")
        return
    
    ctx.latest_ranking = ranking
    ctx.last_scan_ts   = _now_iso()
    app_state["last_scan_timestamp"] = ctx.last_scan_ts

    news_map: Dict[str, Optional[NewsContext]] = {}
    for sig in ranking.top:
        news_map[sig.symbol] = ctx.news_engine.get_context_for_symbol(
            sig.symbol, ctx.latest_articles
        )

    macro_snap = ctx.macro.get_snapshot()

    approved_signals, rejected_signals = ctx.risk_manager.filter_signals(
        ranking.top, macro_snap
    )
    
    if len(approved_signals) < len(ranking.top):
        log.warning("SCAN_RISK", f"Risk blocked {len(ranking.top) - len(approved_signals)} signals")
        ranking.top = approved_signals
        ranking.total_valid = len(approved_signals)

    if ranking.top and _chatbot:
        log.info("PROACTIVE_SIGNALS", f"sending opportunity summary for {len(ranking.top)} signals")
        msg = format_opportunity_summary(ranking.top, news_map)
        if msg:
            await _chatbot.send_alert(msg)
        
        for sig in ranking.top[:1]:
            reason_parts = []
            if sig.components:
                if sig.components.get("relative_strength", 0) >= 70:
                    reason_parts.append(f"Força vs BTC: {sig.components['relative_strength']:.0f}")
                if sig.components.get("funding", 0) >= 75:
                    reason_parts.append("Funding favorável")
                if sig.components.get("oi_acceleration", 0) >= 75:
                    reason_parts.append("Entrada de dinheiro")
            
            await _chatbot.alert_signal(
                symbol=sig.symbol,
                direction=sig.direction,
                score=sig.score,
                reason=" | ".join(reason_parts) if reason_parts else "",
            )

    await ctx.notifier.dispatch(ranking, news_map, macro_snap=macro_snap, memory=ctx.memory)

    for sig in ranking.top:
        price = await _get_btc_price()
        if price:
            await ctx.tracker.register_alert(
                alert_id=f"{sig.symbol}:{sig.direction}:{ctx.last_scan_ts}",
                symbol=sig.symbol,
                direction=sig.direction,
                score=sig.score,
                entry_price=price,
                dominant_component=sig.result.dominant_component,
            )

    log.timed("SCAN_COMPLETE", f"scan done: {len(ranking.top)} signals", t0)


async def job_market_pulse() -> None:
    """Pulso de mercado a cada 15 minutos - mantém usuário antenado."""
    if not _chatbot or not _proactive._should_send_pulse():
        return
    
    try:
        btc_price = await _get_btc_price()
        
        pulse = await _proactive.build_market_pulse(
            btc_price=btc_price,
            btc_closes=ctx.btc_closes,
            regime_result=ctx.latest_regime,
            news_articles=ctx.latest_articles,
            macro_snap=ctx.macro.get_snapshot(),
            ranking_result=ctx.latest_ranking,
        )
        
        msg = format_market_pulse_message(pulse)
        await _chatbot.send_alert(msg)
        
        _proactive._state.last_pulse_time = time.time()
        log.info("PROACTIVE_PULSE", f"market pulse sent - BTC: {btc_price:,.2f}")
        
    except Exception as exc:
        log.error("PROACTIVE_PULSE_ERROR", f"failed to send market pulse: {exc}")


async def job_performance_checks() -> None:
    """Verifica performance dos sinais."""
    await ctx.tracker.run_checks()


async def job_performance_dashboard() -> None:
    """Envia dashboard de performance a cada 4 horas."""
    if not _chatbot:
        return
    
    try:
        stats = await ctx.tracker.get_recent_stats(days=7)
        if stats and stats.get("total", 0) > 0:
            msg = format_performance_dashboard(stats, period="7 dias")
            await _chatbot.send_alert(msg)
            log.info("PROACTIVE_DASHBOARD", "performance dashboard sent")
    except Exception as exc:
        log.error("PERFORMANCE_DASHBOARD_ERROR", f"failed to send dashboard: {exc}")


async def job_sentiment_summary() -> None:
    """Envia resumo de sentimento do mercado a cada 2 horas."""
    if not _chatbot or not ctx.btc_closes or len(ctx.btc_closes) < 20:
        return
    
    try:
        from hyperliquid_client import fetch_all_candles
        from btc_regime import compute_adx
        
        candle_map = await fetch_all_candles(["BTC"], interval="1h", count=24)
        candles = candle_map.get("BTC", [])
        
        if len(candles) < 4:
            return
        
        current_price = candles[-1].close
        price_1h = candles[-2].close if len(candles) >= 2 else current_price
        price_4h = candles[-5].close if len(candles) >= 5 else current_price
        price_24h = candles[-25].close if len(candles) >= 25 else current_price
        
        change_1h = (current_price - price_1h) / price_1h * 100
        change_4h = (current_price - price_4h) / price_4h * 100
        change_24h = (current_price - price_24h) / price_24h * 100
        
        sentiment = "neutral"
        if change_1h > 1:
            sentiment = "bullish"
        elif change_1h < -1:
            sentiment = "bearish"
        
        msg = format_sentiment_summary(
            btc_change_1h=change_1h,
            btc_change_4h=change_4h,
            btc_change_24h=change_24h,
            market_sentiment=sentiment,
        )
        await _chatbot.send_alert(msg)
        log.info("PROACTIVE_SENTIMENT", f"sentiment summary sent: {sentiment}")
        
    except Exception as exc:
        log.error("SENTIMENT_SUMMARY_ERROR", f"failed to send sentiment: {exc}")


async def job_macro_refresh() -> None:
    """Atualiza dados macro e detecta riscos."""
    await ctx.macro.refresh()
    snap = ctx.macro.get_snapshot()
    
    if snap and _chatbot:
        risk_score = getattr(snap, "risk_score", 50)
        if risk_score >= 75:
            events = getattr(snap, "events", [])
            high_neg = [e for e in events if getattr(e, 'impact', '') == 'HIGH' and getattr(e, 'sentiment', '') == 'negative']
            if high_neg:
                await _chatbot.alert_macro_risk(
                    risk_score=risk_score,
                    event=high_neg[0].title[:100],
                )


async def job_market_report() -> None:
    """Relatório de mercado horário."""
    await ctx.notifier.send_market_report()


async def job_btc_spike() -> None:
    """Verifica movimentos bruscos do BTC."""
    await ctx.notifier.check_btc_spike()
    
    if _chatbot and ctx.btc_closes and len(ctx.btc_closes) >= 2:
        btc_price = await _get_btc_price()
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
    """Resumo diário à meia-noite UTC."""
    await ctx.notifier.send_daily_summary(tracker=ctx.tracker)
    if _chatbot:
        _chatbot.reset_daily_alerts()
        _proactive.reset_daily()
        try:
            stats = await ctx.tracker.get_recent_stats(days=1)
            await _chatbot.alert_daily_summary(stats)
        except Exception:
            pass


async def job_adaptive_tune() -> None:
    """Re-tuna pesos a cada 24h."""
    await ctx.adaptive.adapt(ctx.tracker)


async def job_flush_events() -> None:
    """Flush eventos DB."""
    await flush_event_buffer()


# ============================================================================
# HELPERS
# ============================================================================

def _patch_scoring_weights(weights: Dict[str, float]) -> None:
    try:
        import scoring
        for k, v in weights.items():
            if k in scoring.WEIGHTS:
                scoring.WEIGHTS[k] = v
    except Exception as exc:
        log.error("WEIGHT_UPDATE_FAIL", f"weight patch failed: {exc}")


async def _send_startup_market_briefing() -> None:
    """Envia briefing de mercado na inicialização."""
    if not _chatbot:
        return
    
    await asyncio.sleep(3)
    
    lines = [
        "🤖 *JARVIS ONLINE — BRIEFING INICIAL*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
        "",
        "*📡 Status do Sistema:*",
        "• ✅ Scanner ativo",
        "• ✅ WebSocket conectado" if ws_state.get("status") == "CONNECTED" else "• 🔄 WebSocket conectando",
        "• ✅ IA Groq pronta" if os.getenv("GROQ_API_KEY") else "• ⚠️ IA desabilitada",
        "",
    ]
    
    btc_price = await _get_btc_price()
    if btc_price > 0:
        lines.append(f"*💹 BTC:* `${btc_price:,.2f}`")
    
    if ctx.latest_regime:
        regime_str = str(ctx.latest_regime.regime).split(".")[-1]
        regime_pt = {"TRENDING": "Em Tendência 📈", "RANGING": "Lateral ↔️", "WEAK": "Fraco 🔄"}.get(regime_str, regime_str)
        lines.append(f"*📊 Regime:* {regime_pt}")
        lines.append(f"*📐 ADX:* `{ctx.latest_regime.adx:.1f}`")
    
    news_count = len(ctx.latest_articles)
    lines.append(f"*📰 Notícias:* {news_count} disponíveis")
    
    macro_snap = ctx.macro.get_snapshot()
    if macro_snap:
        risk_score = getattr(macro_snap, "risk_score", 50)
        risk_emoji = "🔴" if risk_score >= 70 else ("🟡" if risk_score >= 50 else "🟢")
        lines.append(f"*🌍 Risco Macro:* {risk_emoji} {risk_score:.0f}/100")
    
    lines.append("")
    lines.append("*🔔 O que eu faço:*")
    lines.append("• Alerto sobre oportunidades de trade automaticamente")
    lines.append("• Notifico mudanças de regime do mercado")
    lines.append("• Aviso sobre notícias importantes")
    lines.append("• Envio pulso de mercado a cada 15 minutos")
    lines.append("• Monitorei 24/7 para você")
    lines.append("")
    lines.append("💡 Digite /help para ver comandos disponíveis")
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    
    msg = "\n".join(lines)
    await _chatbot.send_alert(msg)
    log.info("STARTUP_BRIEFING", "initial market briefing sent")


# ============================================================================
# GRACEFUL SHUTDOWN
# ============================================================================

_shutdown_event = asyncio.Event()


def _handle_signal(sig) -> None:
    log.warning("SYSTEM_SHUTDOWN", f"received {sig.name} — shutting down")
    _shutdown_event.set()


# ============================================================================
# MAIN
# ============================================================================

async def main() -> None:
    log.info("SYSTEM_START", "=== Jarvis AI Trading Monitor initializing ===")
    
    ai_key = bool(os.getenv("GROQ_API_KEY"))
    ai_status = "habilitada" if ai_key else "desabilitada"
    log.info("SYSTEM_START", f"Groq AI: {ai_status}")
    app_state["started_at"] = _now_iso()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    try:
        await init_db()
    except RuntimeError as exc:
        log.critical("SYSTEM_START", f"DB startup failed: {exc}")
        return

    await ctx.tracker.startup()
    await ctx.adaptive.startup()
    await ctx.memory.startup()
    await ctx.macro.refresh()

    try:
        ctx.latest_articles = await ctx.news_engine.fetch_all()
        ctx.last_news_ts = _now_iso()
        log.info("SYSTEM_READY", f"news cache: {len(ctx.latest_articles)} articles")
    except Exception as exc:
        log.warning("NEWS_PRIMARY_FAIL", f"initial news fetch failed: {exc}")

    tg_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    
    if not tg_token or not tg_chat_id:
        log.warning("SYSTEM_START", "Telegram not configured — alerts disabled")
    
    ctx.notifier = Notifier(telegram_token=tg_token, telegram_chat_id=tg_chat_id)
    await ctx.notifier.startup()
    ctx.notifier.set_news_engine(ctx.news_engine)

    await run_health_server()
    ws_task = asyncio.create_task(run_websocket_client())

    sched = build_scheduler(
        scan_fn=job_scan_cycle,
        news_fn=job_news_fetch,
        regime_fn=job_btc_regime,
        performance_fn=job_performance_checks,
        adaptive_fn=job_adaptive_tune,
        market_report_fn=job_market_report,
        macro_refresh_fn=job_macro_refresh,
        btc_spike_fn=job_btc_spike,
        daily_summary_fn=job_daily_summary,
        flush_fn=job_flush_events,
    )
    sched.start()

    await write_system_event(
        "SYSTEM_READY",
        "all subsystems running — scheduler active",
        level="INFO", module="main",
    )
    log.info("SYSTEM_READY", "=== Crypto Monitor fully operational ===")

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
        log.info("CHATBOT_READY", f"chatbot configured")
        
        asyncio.create_task(_send_startup_market_briefing())

    sched.add_interval_job(job_market_pulse, minutes=15, name="market_pulse", jitter=60)
    sched.add_interval_job(job_performance_dashboard, minutes=0, name="perf_dashboard", hours=4, jitter=300)
    sched.add_interval_job(job_sentiment_summary, minutes=0, name="sentiment_summary", hours=2, jitter=60)
    log.info("SYSTEM_READY", "proactive agent enabled")

    async def chat_loop():
        if not _chatbot:
            log.warning("CHATBOT_LOOP", "no chatbot configured")
            return
        log.info("CHATBOT_LOOP", "chat loop started")
        
        while not _shutdown_event.is_set():
            try:
                result = await asyncio.wait_for(_chatbot.poll(), timeout=35)
                if result is None:
                    continue
                
                text = result.get("text", "")
                user_chat_id = result.get("chat_id", "")
                non_text = result.get("non_text", False)
                
                if not user_chat_id:
                    continue
                
                log.info("CHATBOT_MSG", f"user={user_chat_id} text='{text[:30]}'")
                
                if non_text:
                    response = "📎 Desculpe, só consigo ler mensagens de texto!\n\nDigite /help para ver comandos."
                else:
                    _chatbot.set_system_refs(last_ranking=ctx.latest_ranking)
                    response = await _chatbot.handle_message(text, user_chat_id)
                
                if response:
                    await _chatbot._send_message(user_chat_id, response)
                    
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                log.error("CHATBOT_ERROR", f"error: {exc}")
                await asyncio.sleep(5)

    chat_task = asyncio.create_task(chat_loop()) if _chatbot else None

    await _shutdown_event.wait()

    log.info("SYSTEM_SHUTDOWN", "initiating graceful shutdown")
    
    if ctx.notifier:
        await ctx.notifier.send_system_alert("🔴 *Jarvis offline*\nVoltando em breve.")

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
    log.info("SYSTEM_SHUTDOWN", "=== shutdown complete ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
