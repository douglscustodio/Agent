"""
notifier.py — Telegram alert dispatcher
Alert format (exact):
  🚨 TOP 3 TRADE OPPORTUNITIES
  1. SYMBOL — LONG/SHORT — XX%
  Reason:
  • strength
  • OI
  • news
  • freshness

Dedup gate: AlertDedupStore (2h cooldown, score-delta override)
Full JSON event log on every send/suppress.
"""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

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


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

@dataclass
class AlertPayload:
    symbol:    str
    direction: str
    score:     float
    band:      ScoreBand
    reasons:   List[str]
    rank:      int


def _band_emoji(band: ScoreBand) -> str:
    return "🔥" if band == ScoreBand.HIGH_CONVICTION else "✅"


def _direction_emoji(direction: str) -> str:
    return "📈" if direction.upper() == "LONG" else "📉"


def _calc_levels(price: float, direction: str) -> tuple:
    """Calculate entry, stop-loss and take-profit levels."""
    if direction == "LONG":
        entry = price
        sl    = round(price * 0.97, 6)   # -3%
        tp1   = round(price * 1.03, 6)   # +3%
        tp2   = round(price * 1.06, 6)   # +6%
    else:
        entry = price
        sl    = round(price * 1.03, 6)   # +3%
        tp1   = round(price * 0.97, 6)   # -3%
        tp2   = round(price * 0.94, 6)   # -6%
    return entry, sl, tp1, tp2


def _build_reason_bullets(
    signal:      RankedSignal,
    news_ctx:    Optional[NewsContext],
    sector:      str,
) -> List[str]:
    """Build simple, beginner-friendly reason bullets."""
    comp = signal.components

    # 1. Market direction (RS + ADX in plain language)
    rs_score  = comp.get("relative_strength", 0)
    adx_score = comp.get("adx_regime", 0)
    if rs_score >= 70 and adx_score >= 70:
        strength_line = "📊 Tendência forte e superando o BTC"
    elif rs_score >= 65:
        strength_line = "📊 Moeda mais forte que o BTC agora"
    elif adx_score >= 65:
        strength_line = "📊 Tendência clara no gráfico"
    else:
        strength_line = "📊 Movimento moderado, sem tendência forte"

    # 2. OI in plain language
    oi_score = comp.get("oi_acceleration", 0)
    if oi_score >= 85:
        oi_line = "💰 Muito dinheiro entrando no mercado agora"
    elif oi_score >= 65:
        oi_line = "💰 Interesse crescente de traders"
    elif oi_score >= 45:
        oi_line = "💰 Volume de contratos estável"
    else:
        oi_line = "⚠️ Interesse dos traders caindo"

    # 3. News in plain language
    if news_ctx and news_ctx.articles:
        sentiment_emoji = "🟢" if news_ctx.aggregate_sentiment == "positive" else (
                          "🔴" if news_ctx.aggregate_sentiment == "negative" else "⚪")
        headline = news_ctx.top_headline[:80] + ("…" if len(news_ctx.top_headline) > 80 else "")
        news_line = f"📰 Notícia {sentiment_emoji}: {headline}"
    else:
        news_line = "📰 Sem notícias relevantes no momento"

    # 4. Entry timing in plain language
    bb_score  = comp.get("bb_squeeze", 0)
    atr_score = comp.get("atr_quality", 0)
    if bb_score >= 75 and atr_score >= 75:
        entry_line = "⏱ Entrada no início do movimento — ótimo timing"
    elif bb_score >= 60:
        entry_line = "⏱ Bom momento para entrar"
    else:
        entry_line = "⏱ Movimento já iniciado — entre com cautela"

    return [strength_line, oi_line, news_line, entry_line]


def _build_alert_message(
    signals:     List[RankedSignal],
    news_map:    Dict[str, Optional[NewsContext]],
) -> str:
    """
    Build beginner-friendly Telegram alert with entry, stop and targets.
    """
    now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    lines   = [
        "🚨 *SINAL DE TRADE DETECTADO*",
        f"_{now_str}_",
        "",
    ]

    for sig in signals[:3]:
        sym       = sig.symbol
        direction = sig.direction.upper()
        score     = sig.score
        band      = sig.band
        sector    = get_sector_label(sym)
        news_ctx  = news_map.get(sym)

        # Get live price
        try:
            from websocket_client import ws_price_cache
            price = ws_price_cache.get(sym, 0.0)
        except Exception:
            price = 0.0

        dir_emoji  = _direction_emoji(direction)
        band_emoji = _band_emoji(band)
        conviction = "🔥 ALTA CONVICÇÃO" if band == "HIGH_CONVICTION" else "✅ VÁLIDO"

        # Header
        lines.append(f"{'━'*22}")
        lines.append(f"{dir_emoji} *{sym}/USDT* — *{direction}*")
        lines.append(f"Força do sinal: `{score:.0f}/100` {band_emoji} {conviction}")
        lines.append("")

        # Entry levels
        if price > 0:
            entry, sl, tp1, tp2 = _calc_levels(price, direction)
            sl_pct  = abs(price - sl)  / price * 100
            tp1_pct = abs(tp1 - price) / price * 100
            tp2_pct = abs(tp2 - price) / price * 100

            lines.append("*📌 COMO OPERAR:*")
            lines.append(f"• Entrada:  `${entry:,.4f}`")
            lines.append(f"• Stop Loss: `${sl:,.4f}` ({sl_pct:.1f}% de risco)")
            lines.append(f"• Alvo 1:   `${tp1:,.4f}` (+{tp1_pct:.1f}%)")
            lines.append(f"• Alvo 2:   `${tp2:,.4f}` (+{tp2_pct:.1f}%)")
            lines.append(f"• Setor: {sector}")
            lines.append("")

        # Reasons in plain language
        reasons = _build_reason_bullets(sig, news_ctx, sector)
        lines.append("*🧠 POR QUE ESTE SINAL?*")
        for r in reasons:
            lines.append(f"• {r}")
        lines.append("")

        # Risk warning
        lines.append("*⚠️ GESTÃO DE RISCO:*")
        lines.append("• Nunca arrisque mais de 1-2% do seu capital")
        lines.append("• Respeite sempre o Stop Loss")
        lines.append("• Este é um sinal, não uma garantia")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

async def _send_telegram(
    token:   str,
    chat_id: str,
    text:    str,
) -> bool:
    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=SEND_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    log.info("ALERT_SENT", "Telegram message delivered")
                    return True
                body = await resp.text()
                log.error(
                    "ALERT_SENT",
                    f"Telegram API error {resp.status}: {body[:200]}",
                )
                return False
    except Exception as exc:
        log.error("ALERT_SENT", f"Telegram send failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------

class Notifier:
    """
    Orchestrates dedup → message build → Telegram dispatch.
    """

    def __init__(
        self,
        telegram_token:   Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
    ):
        self._token   = telegram_token   or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._dedup   = AlertDedupStore()

    async def startup(self) -> None:
        """Call once at app startup to bootstrap dedup table."""
        await self._dedup.ensure_table()
        log.info("SYSTEM_READY", "notifier ready")

    async def dispatch(
        self,
        ranking:   RankingResult,
        news_map:  Dict[str, Optional[NewsContext]],
    ) -> None:
        """
        Evaluate top signals through dedup, then send one batched Telegram message
        containing only the signals that passed the dedup gate.
        """
        if not ranking.top:
            log.info("ALERT_SUPPRESSED", "no valid signals to dispatch")
            return

        eligible: List[RankedSignal] = []

        for sig in ranking.top:
            should_send, reason = await self._dedup.should_send(
                sig.symbol, sig.direction, sig.score
            )
            if should_send:
                eligible.append(sig)
            else:
                log.info(
                    "ALERT_SUPPRESSED",
                    f"suppressed {sig.symbol} {sig.direction} score={sig.score:.1f}: {reason}",
                    symbol=sig.symbol,
                    direction=sig.direction,
                    score=sig.score,
                    alert_id=f"{sig.symbol}:{sig.direction}",
                )
                await write_system_event(
                    "ALERT_SUPPRESSED",
                    f"{sig.symbol} {sig.direction} suppressed: {reason}",
                    level="INFO",
                    module="notifier",
                    symbol=sig.symbol,
                    direction=sig.direction,
                    score=sig.score,
                    alert_id=f"{sig.symbol}:{sig.direction}",
                )

        if not eligible:
            log.info("ALERT_SUPPRESSED", "all signals suppressed by dedup")
            return

        # Build and send
        message = _build_alert_message(eligible, news_map)
        sent    = await _send_telegram(self._token, self._chat_id, message)

        if sent:
            for sig in eligible:
                alert_id = f"{sig.symbol}:{sig.direction}"
                await self._dedup.record_sent(sig.symbol, sig.direction, sig.score)
                log.info(
                    "ALERT_SENT",
                    f"alert sent: {sig.symbol} {sig.direction} score={sig.score:.1f}",
                    symbol=sig.symbol,
                    direction=sig.direction,
                    score=sig.score,
                    alert_id=alert_id,
                )
                await write_system_event(
                    "ALERT_SENT",
                    f"alert dispatched: {sig.symbol} {sig.direction}",
                    level="INFO",
                    module="notifier",
                    symbol=sig.symbol,
                    direction=sig.direction,
                    score=sig.score,
                    alert_id=alert_id,
                )
        else:
            log.error(
                "ALERT_SENT",
                f"Telegram dispatch failed for {len(eligible)} signals",
            )
            await write_system_event(
                "ALERT_SENT",
                "Telegram dispatch failed",
                level="ERROR",
                module="notifier",
            )

    async def send_system_alert(self, text: str) -> None:
        """Send a plain system notification (startup, crash, recovery)."""
        if not self._token or not self._chat_id:
            return
        await _send_telegram(self._token, self._chat_id, text)
