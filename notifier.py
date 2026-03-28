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


def _build_reason_bullets(
    signal:      RankedSignal,
    news_ctx:    Optional[NewsContext],
    sector:      str,
) -> List[str]:
    """Build the 4 mandatory reason bullets."""
    comp = signal.components

    # 1. Strength (RS + ADX)
    rs_score  = comp.get("relative_strength", 0)
    adx_score = comp.get("adx_regime", 0)
    strength_label = (
        "Strong RS vs BTC + trending" if rs_score >= 70 and adx_score >= 70
        else "Outperforming BTC" if rs_score >= 65
        else "Trending regime" if adx_score >= 65
        else "Moderate strength"
    )
    strength_line = f"Strength: {strength_label} (RS={rs_score:.0f} ADX={adx_score:.0f})"

    # 2. OI
    oi_score = comp.get("oi_acceleration", 0)
    oi_label = (
        "OI surging — strong conviction" if oi_score >= 85
        else "OI building steadily"      if oi_score >= 65
        else "OI neutral"                if oi_score >= 45
        else "OI declining — caution"
    )
    oi_line = f"OI: {oi_label} (score={oi_score:.0f})"

    # 3. News
    if news_ctx and news_ctx.articles:
        sentiment_emoji = "🟢" if news_ctx.aggregate_sentiment == "positive" else (
                          "🔴" if news_ctx.aggregate_sentiment == "negative" else "⚪")
        freshness = f"{news_ctx.freshness_minutes:.0f}m ago"
        headline  = news_ctx.top_headline[:80] + ("…" if len(news_ctx.top_headline) > 80 else "")
        news_line = f"News {sentiment_emoji}: {headline} ({freshness})"
    else:
        news_line = "News: No significant recent news"

    # 4. Freshness / sector
    bb_score  = comp.get("bb_squeeze", 0)
    atr_score = comp.get("atr_quality", 0)
    fresh_label = (
        "Early entry — BB squeeze + ATR optimal" if bb_score >= 75 and atr_score >= 75
        else "Early-stage setup"                  if bb_score >= 60
        else "Acceptable entry window"
    )
    freshness_line = f"Entry: {fresh_label} | Sector: {sector}"

    return [strength_line, oi_line, news_line, freshness_line]


def _build_alert_message(
    signals:     List[RankedSignal],
    news_map:    Dict[str, Optional[NewsContext]],
) -> str:
    """
    Build the full Telegram message for up to 3 signals.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines   = [
        "🚨 *TOP 3 TRADE OPPORTUNITIES*",
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

        reasons   = _build_reason_bullets(sig, news_ctx, sector)
        dir_emoji = _direction_emoji(direction)
        band_emoji= _band_emoji(band)

        lines.append(
            f"*{sig.rank}. {sym}* — {dir_emoji} *{direction}* — "
            f"Score: `{score:.1f}` {band_emoji} [{band}]"
        )
        lines.append("Reason:")
        for r in reasons:
            lines.append(f"• {r}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("_Powered by Phase 3 Scanner_")
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
