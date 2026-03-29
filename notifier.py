"""
notifier.py — Telegram dispatcher completo em Português
Mensagens:
  1. Sinal de trade (com entrada, stop, alvos, notícias)
  2. Relatório de mercado a cada hora
  3. Alerta de movimento brusco do BTC (>3%)
  4. Resumo diário com performance
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from alerts_dedup import AlertDedupStore
from database import write_system_event
from logger import get_logger
from news_engine import NewsContext
from ranking import RankedSignal, RankingResult
from scoring import ScoreBand
from sector_rotation import get_sector_label

log = get_logger("notifier")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
SEND_TIMEOUT = 10

# BTC spike alert threshold
BTC_SPIKE_PCT = 3.0
_last_btc_price: float = 0.0
_last_btc_alert: float = 0.0
BTC_ALERT_COOLDOWN = 1800   # 30 min entre alertas de BTC

# Daily summary tracker
_daily_signals: List[dict] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_br() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _get_price(symbol: str) -> float:
    """Get price from WS cache. Falls back to Hyperliquid meta cache."""
    try:
        from websocket_client import ws_price_cache
        price = ws_price_cache.get(symbol, 0.0)
        if price > 0:
            return price
    except Exception:
        pass
    # Fallback: try meta cache populated by scanner
    try:
        from scanner import _meta_cache
        meta = _meta_cache.get(symbol)
        if meta and meta.mark_price > 0:
            return meta.mark_price
    except Exception:
        pass
    return 0.0


def _calc_levels(price: float, direction: str) -> Tuple[float, float, float, float]:
    if direction == "LONG":
        sl  = round(price * 0.97, 6)
        tp1 = round(price * 1.03, 6)
        tp2 = round(price * 1.06, 6)
    else:
        sl  = round(price * 1.03, 6)
        tp1 = round(price * 0.97, 6)
        tp2 = round(price * 0.94, 6)
    return price, sl, tp1, tp2


def _direction_emoji(direction: str) -> str:
    return "📈" if direction.upper() == "LONG" else "📉"


def _band_label(band) -> str:
    if str(band) in ("HIGH_CONVICTION", "ScoreBand.HIGH_CONVICTION"):
        return "🔥 ALTA CONVICÇÃO"
    return "✅ VÁLIDO"


def _sentiment_pt(sentiment: str) -> str:
    return {"positive": "Positivo 🟢", "negative": "Negativo 🔴"}.get(sentiment, "Neutro ⚪")


# ---------------------------------------------------------------------------
# 1. Sinal de trade
# ---------------------------------------------------------------------------

def _build_signal_message(
    signals:  List[RankedSignal],
    news_map: Dict[str, Optional[NewsContext]],
) -> str:
    lines = [
        "🚨 *SINAL DE TRADE DETECTADO*",
        f"_{_now_br()}_",
        "",
    ]

    for sig in signals[:3]:
        sym       = sig.symbol
        direction = sig.direction.upper()
        score     = sig.score
        band      = sig.band
        sector    = get_sector_label(sym)
        news_ctx  = news_map.get(sym)
        price     = _get_price(sym)
        comp      = sig.components

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{_direction_emoji(direction)} *{sym}/USDT — {direction}*")
        lines.append(f"Força do sinal: `{score:.0f}/100` {_band_label(band)}")
        lines.append("")

        # Entry levels
        if price > 0:
            _, sl, tp1, tp2 = _calc_levels(price, direction)
            sl_pct  = abs(price - sl)  / price * 100
            tp1_pct = abs(tp1 - price) / price * 100
            tp2_pct = abs(tp2 - price) / price * 100
            lines.append("*📌 COMO OPERAR:*")
            lines.append(f"• Entrada:   `${price:,.5f}`")
            lines.append(f"• Stop Loss: `${sl:,.5f}` ⛔ ({sl_pct:.1f}% de risco)")
            lines.append(f"• Alvo 1:   `${tp1:,.5f}` 🎯 (+{tp1_pct:.1f}%)")
            lines.append(f"• Alvo 2:   `${tp2:,.5f}` 🎯 (+{tp2_pct:.1f}%)")
            lines.append(f"• Setor: {sector}")
            lines.append("")

        # Why this signal — plain language
        lines.append("*🧠 POR QUE ESTE SINAL?*")

        rs    = comp.get("relative_strength", 0)
        adx   = comp.get("adx_regime", 0)
        oi    = comp.get("oi_acceleration", 0)
        bb    = comp.get("bb_squeeze", 0)
        atr   = comp.get("atr_quality", 0)
        fund  = comp.get("funding", 0)

        # Tendência
        if rs >= 70 and adx >= 70:
            lines.append("• 📊 Tendência forte — superando o BTC com força")
        elif rs >= 65:
            lines.append("• 📊 Mais forte que o BTC no momento")
        elif adx >= 65:
            lines.append("• 📊 Tendência clara no gráfico")
        else:
            lines.append("• 📊 Movimento moderado")

        # Open Interest
        if oi >= 85:
            lines.append("• 💰 Muito dinheiro entrando agora — alta convicção")
        elif oi >= 65:
            lines.append("• 💰 Interesse crescente de traders")
        elif oi >= 45:
            lines.append("• 💰 Volume de contratos estável")
        else:
            lines.append("• ⚠️ Interesse dos traders caindo")

        # Funding
        if fund >= 75:
            lines.append("• 💸 Financiamento favorável para esta direção")
        elif fund <= 35:
            lines.append("• ⚠️ Financiamento desfavorável — atenção")

        # Entry timing
        if bb >= 75 and atr >= 75:
            lines.append("• ⏱ Entrada no início do movimento — ótimo timing")
        elif bb >= 60:
            lines.append("• ⏱ Bom momento para entrar")
        else:
            lines.append("• ⏱ Movimento já iniciado — entre com cautela")

        # News
        if news_ctx and news_ctx.articles and news_ctx.top_headline != "No recent news":
            lines.append(f"• 📰 Notícia ({_sentiment_pt(news_ctx.aggregate_sentiment)}): {news_ctx.top_headline[:80]}")
            if news_ctx.freshness_minutes < 999:
                lines[-1] += f" ({news_ctx.freshness_minutes:.0f}min atrás)"
        else:
            # Try to get any recent general news
            lines.append("• 📰 Adicione CRYPTOPANIC_TOKEN para notícias em tempo real")

        lines.append("")
        lines.append("*⚠️ GESTÃO DE RISCO:*")
        lines.append("• Arrisque no máximo 1–2% do capital por operação")
        lines.append("• Respeite o Stop Loss — ele protege sua conta")
        lines.append("• Este é um sinal, não uma garantia de lucro")
        lines.append("")

        # Track for daily summary
        _daily_signals.append({
            "symbol": sym, "direction": direction,
            "score": score, "price": price,
            "sl": sl if price > 0 else 0,
            "tp1": tp1 if price > 0 else 0,
            "time": _now_br(),
        })

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Relatório de mercado (a cada hora)
# ---------------------------------------------------------------------------

async def build_market_report(news_engine=None) -> str:
    """Relatório horário: BTC, regime, dominância e notícias."""
    btc_price = _get_price("BTC")
    eth_price = _get_price("ETH")
    sol_price = _get_price("SOL")

    lines = [
        "📊 *RELATÓRIO DE MERCADO*",
        f"_{_now_br()}_",
        "",
        "*💹 Preços atuais:*",
    ]

    if btc_price > 0:
        lines.append(f"• BTC: `${btc_price:,.2f}`")
    if eth_price > 0:
        lines.append(f"• ETH: `${eth_price:,.2f}`")
    if sol_price > 0:
        lines.append(f"• SOL: `${sol_price:,.2f}`")

    lines.append("")

    # BTC regime
    try:
        from hyperliquid_client import fetch_all_candles
        from btc_regime import compute_adx
        candle_map = await fetch_all_candles(["BTC"], interval="1h", count=50)
        candles    = candle_map.get("BTC", [])
        if len(candles) >= 30:
            highs  = [c.high  for c in candles]
            lows   = [c.low   for c in candles]
            closes = [c.close for c in candles]
            regime = compute_adx(highs, lows, closes)

            regime_pt = {
                "TRENDING": "📈 Em tendência",
                "RANGING":  "↔️ Lateral / sem direção",
                "WEAK":     "🔄 Tendência fraca",
            }.get(str(regime.regime).split(".")[-1], "Indefinido")

            dir_pt = {"UP": "Para cima ⬆️", "DOWN": "Para baixo ⬇️", "NEUTRAL": "Neutro"}.get(
                regime.trend_direction, "Neutro"
            )

            lines.append("*📉 Regime do BTC (1h):*")
            lines.append(f"• Situação: {regime_pt}")
            lines.append(f"• Direção: {dir_pt}")
            lines.append(f"• Força da tendência (ADX): `{regime.adx:.1f}`")
            lines.append("")
    except Exception as exc:
        log.warning("PERFORMANCE_LOGGED", f"market report regime error: {exc}")

    # Top notícias
    if news_engine:
        try:
            articles = news_engine._cache[:5] if news_engine._cache else []
            if articles:
                lines.append("*📰 Últimas notícias:*")
                for a in articles[:4]:
                    age_min = round((time.time() - a.published_at) / 60)
                    emoji   = "🟢" if a.sentiment == "positive" else ("🔴" if a.sentiment == "negative" else "⚪")
                    lines.append(f"• {emoji} {a.title[:70]}… ({age_min}min)")
                lines.append("")
        except Exception:
            pass

    # Dica de mercado
    lines.append("*💡 Dica do momento:*")
    hour = datetime.now(timezone.utc).hour
    if 0 <= hour < 6:
        lines.append("• Madrugada UTC — liquidez menor, cuidado com fakeouts")
    elif 8 <= hour < 12:
        lines.append("• Manhã europeia — mercado começando a movimentar")
    elif 13 <= hour < 17:
        lines.append("• Tarde americana — maior volume do dia, bons sinais")
    else:
        lines.append("• Monitore os níveis de suporte e resistência")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Próximo relatório em 1 hora_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Alerta de movimento brusco do BTC
# ---------------------------------------------------------------------------

async def check_btc_spike(notifier: "Notifier") -> None:
    """Verifica se BTC moveu mais de BTC_SPIKE_PCT% desde o último preço."""
    global _last_btc_price, _last_btc_alert

    current = _get_price("BTC")
    if current <= 0:
        return

    if _last_btc_price <= 0:
        _last_btc_price = current
        return

    pct_change = (current - _last_btc_price) / _last_btc_price * 100
    now = time.time()

    if abs(pct_change) >= BTC_SPIKE_PCT and (now - _last_btc_alert) > BTC_ALERT_COOLDOWN:
        direction = "SUBIU" if pct_change > 0 else "CAIU"
        emoji     = "🚀" if pct_change > 0 else "💥"
        impact    = "pode abrir oportunidades de LONG" if pct_change > 0 else "cuidado — pode arrastar altcoins para baixo"

        msg = (
            f"{emoji} *ALERTA BTC — MOVIMENTO BRUSCO!*\n"
            f"_{_now_br()}_\n\n"
            f"• BTC *{direction}* `{abs(pct_change):.1f}%` rapidamente\n"
            f"• Preço atual: `${current:,.2f}`\n"
            f"• Preço anterior: `${_last_btc_price:,.2f}`\n\n"
            f"*O que isso significa?*\n"
            f"• {impact}\n"
            f"• Aguarde confirmação antes de entrar em novas posições\n"
            f"• Se você tiver posição aberta, verifique seu stop loss\n\n"
            f"_Jarvis AI Trading Monitor_"
        )
        await notifier.send_system_alert(msg)
        _last_btc_alert = now
        _last_btc_price = current

        log.info("ALERT_SENT", f"BTC spike alert sent: {pct_change:.1f}%")
        await write_system_event(
            "ALERT_SENT", f"BTC spike {pct_change:.1f}%",
            level="INFO", module="notifier", symbol="BTC", score=abs(pct_change),
        )
    else:
        # Gradual price update
        _last_btc_price = current * 0.9 + _last_btc_price * 0.1


# ---------------------------------------------------------------------------
# 4. Resumo diário
# ---------------------------------------------------------------------------

async def send_daily_summary(notifier: "Notifier", tracker=None) -> None:
    """Resumo diário: quantos sinais, performance, notícias do dia."""
    global _daily_signals

    lines = [
        "📋 *RESUMO DO DIA*",
        f"_{_now_br()}_",
        "",
    ]

    # Signals of the day
    total = len(_daily_signals)
    if total == 0:
        lines.append("Nenhum sinal enviado hoje.")
    else:
        lines.append(f"*📊 Sinais enviados hoje: {total}*")
        for s in _daily_signals[-10:]:   # last 10
            dir_emoji = "📈" if s["direction"] == "LONG" else "📉"
            lines.append(
                f"• {dir_emoji} {s['symbol']} {s['direction']} — "
                f"Score `{s['score']:.0f}` às {s['time'][-8:]}"
            )
        lines.append("")

    # Performance from DB
    if tracker:
        try:
            stats = await tracker.get_recent_stats(days=1)
            tp1     = stats.get("tp1", 0)
            sl_hit  = stats.get("sl", 0)
            neutral = stats.get("neutral", 0)
            total_r = stats.get("total", 0)
            win_r   = stats.get("win_rate", 0.0)
            avg_pnl = stats.get("avg_pnl", 0.0)

            if total_r > 0:
                lines.append("*🏆 Performance de hoje (24h):*")
                lines.append(f"• ✅ Acertou (TP1): {tp1}")
                lines.append(f"• ❌ Stop Loss: {sl_hit}")
                lines.append(f"• ➖ Neutro: {neutral}")
                lines.append(f"• Taxa de acerto: `{win_r:.1f}%`")
                lines.append(f"• PnL médio: `{avg_pnl:+.2f}%`")
                lines.append("")
        except Exception as exc:
            log.warning("PERFORMANCE_LOGGED", f"daily summary stats error: {exc}")

    lines.append("*💡 Lembre-se:*")
    lines.append("• Gerencie bem o risco — 1 a 2% por operação")
    lines.append("• Sinais são probabilidades, não certezas")
    lines.append("• Amanhã é um novo dia de oportunidades 🚀")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Jarvis AI Trading Monitor_")

    await notifier.send_system_alert("\n".join(lines))

    # Reset daily counter
    _daily_signals.clear()
    log.info("ALERT_SENT", "daily summary sent")


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

async def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    url     = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=SEND_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    log.info("ALERT_SENT", "Telegram message delivered")
                    return True
                body = await resp.text()
                log.error("ALERT_SENT", f"Telegram error {resp.status}: {body[:200]}")
                return False
    except Exception as exc:
        log.error("ALERT_SENT", f"Telegram send failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Notifier class
# ---------------------------------------------------------------------------

class Notifier:
    def __init__(
        self,
        telegram_token:   Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
    ):
        self._token   = telegram_token   or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._dedup   = AlertDedupStore()
        self._news_engine = None    # set by main.py after startup

    async def startup(self) -> None:
        await self._dedup.ensure_table()
        log.info("SYSTEM_READY", "notifier ready")

    def set_news_engine(self, engine) -> None:
        self._news_engine = engine

    # ------------------------------------------------------------------
    # Trade signals
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        ranking:   RankingResult,
        news_map:  Dict[str, Optional[NewsContext]],
    ) -> None:
        if not ranking.top:
            log.info("ALERT_SUPPRESSED", "no valid signals to dispatch")
            return

        eligible = []
        for sig in ranking.top:
            should_send, reason = await self._dedup.should_send(
                sig.symbol, sig.direction, sig.score
            )
            if should_send:
                eligible.append(sig)
            else:
                log.info(
                    "ALERT_SUPPRESSED",
                    f"suppressed {sig.symbol} {sig.direction}: {reason}",
                    symbol=sig.symbol, direction=sig.direction, score=sig.score,
                )

        if not eligible:
            log.info("ALERT_SUPPRESSED", "all signals suppressed by dedup")
            return

        message = _build_signal_message(eligible, news_map)
        sent    = await _send_telegram(self._token, self._chat_id, message)

        if sent:
            for sig in eligible:
                await self._dedup.record_sent(sig.symbol, sig.direction, sig.score)
                await write_system_event(
                    "ALERT_SENT",
                    f"sinal enviado: {sig.symbol} {sig.direction}",
                    level="INFO", module="notifier",
                    symbol=sig.symbol, direction=sig.direction,
                    score=sig.score, alert_id=f"{sig.symbol}:{sig.direction}",
                )
        else:
            log.error("ALERT_SENT", "Telegram dispatch failed")

    # ------------------------------------------------------------------
    # Market report (called by scheduler every 1h)
    # ------------------------------------------------------------------

    async def send_market_report(self) -> None:
        msg  = await build_market_report(self._news_engine)
        sent = await _send_telegram(self._token, self._chat_id, msg)
        if sent:
            log.info("ALERT_SENT", "relatório de mercado enviado")

    # ------------------------------------------------------------------
    # BTC spike check (called by scheduler every 5 min)
    # ------------------------------------------------------------------

    async def check_btc_spike(self) -> None:
        await check_btc_spike(self)

    # ------------------------------------------------------------------
    # Daily summary (called by scheduler at midnight UTC)
    # ------------------------------------------------------------------

    async def send_daily_summary(self, tracker=None) -> None:
        await send_daily_summary(self, tracker)

    # ------------------------------------------------------------------
    # Generic system alert
    # ------------------------------------------------------------------

    async def send_system_alert(self, text: str) -> None:
        if not self._token or not self._chat_id:
            return
        await _send_telegram(self._token, self._chat_id, text)
