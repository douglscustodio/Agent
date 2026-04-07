"""
analyst.py — Market analysis and formatting for Jarvis AI
Provides analyst functions used by main.py for market pulse and briefings.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone


@dataclass
class MarketContext:
    btc_price: float
    btc_change_1h: float
    regime: str
    regime_direction: str
    regime_strength: float
    risk_level: str
    funding_alerts: List[Dict]
    notable_news: List[str]
    top_opportunities: List[Dict]
    hot_sectors: List[str]
    macro_events: List[str]
    sentiment: str


class SimpleAnalyst:
    def analyze_market(
        self,
        btc_price: float,
        btc_closes: List[float],
        regime_result: Any,
        macro_snap: Dict,
    ) -> MarketContext:
        btc_change = 0.0
        if len(btc_closes) >= 2 and btc_closes[-1] > 0:
            btc_change = ((btc_price - btc_closes[-2]) / btc_closes[-2]) * 100

        regime = getattr(regime_result, 'regime', 'NEUTRAL') if regime_result else 'NEUTRAL'
        direction = getattr(regime_result, 'direction', 'NEUTRAL') if regime_result else 'NEUTRAL'
        strength = getattr(regime_result, 'strength', 0) if regime_result else 0

        sentiment = "neutral"
        if regime == "TRENDING" and direction == "UP":
            sentiment = "bullish"
        elif regime == "TRENDING" and direction == "DOWN":
            sentiment = "bearish"

        risk = "LOW"
        if macro_snap and macro_snap.get("risk_level"):
            risk = macro_snap["risk_level"]

        return MarketContext(
            btc_price=btc_price,
            btc_change_1h=btc_change,
            regime=regime,
            regime_direction=direction,
            regime_strength=strength,
            risk_level=risk,
            funding_alerts=macro_snap.get("funding_alerts", []) if macro_snap else [],
            notable_news=macro_snap.get("notable_news", []) if macro_snap else [],
            top_opportunities=macro_snap.get("top_opportunities", []) if macro_snap else [],
            hot_sectors=macro_snap.get("hot_sectors", []) if macro_snap else [],
            macro_events=macro_snap.get("macro_events", []) if macro_snap else [],
            sentiment=sentiment,
        )


_analyst_instance = SimpleAnalyst()


def get_analyst():
    return _analyst_instance


def format_market_pulse(context: MarketContext) -> str:
    lines = [
        "📊 *PULSO DO MERCADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
    ]
    
    emoji = {"bullish": "🟢", "bearish": "🔴"}.get(context.sentiment, "⚪")
    sentiment_pt = {"bullish": "ALTISTA", "bearish": "BAIXISTA", "neutral": "NEUTRO"}.get(context.sentiment, "NEUTRO")
    
    lines.append(f"*Mercado:* {emoji} {sentiment_pt}")
    lines.append(f"*BTC:* `${context.btc_price:,.2f}` ({context.btc_change_1h:+.2f}%)")
    
    regime_pt = {"TRENDING": "📈 Em Tendência", "RANGING": "↔️ Lateral", "WEAK": "🔄 Fraco"}.get(context.regime, "❓ Indefinido")
    dir_pt = {"UP": "Para cima ⬆️", "DOWN": "Para baixo ⬇️", "NEUTRAL": "Sem direção ➡️"}.get(context.regime_direction, "➡️")
    
    lines.append(f"*Regime:* {regime_pt}")
    lines.append(f"*Direção:* {dir_pt}")
    lines.append(f"*Força:* `{context.regime_strength:.1f}`")
    
    risk_emoji = {"HIGH": "🔴", "MEDIUM": "🟡"}.get(context.risk_level, "🟢")
    lines.append(f"*Risco:* {risk_emoji} {context.risk_level}")
    
    if context.funding_alerts:
        lines.append("")
        lines.append("*⚠️ Funding Extremo:*")
        for fa in context.funding_alerts[:2]:
            lines.append(f"• {fa.get('symbol', 'N/A')}: {fa.get('rate', 0)*100:.2f}%")
    
    if context.top_opportunities:
        lines.append("")
        lines.append("*🎯 Oportunidades:*")
        for opp in context.top_opportunities[:2]:
            emoji_dir = "📈" if opp.get("direction") == "LONG" else "📉"
            lines.append(f"• {emoji_dir} {opp.get('symbol', 'N/A')} Score {opp.get('score', 0):.0f}")
    
    if context.hot_sectors:
        lines.append(f"*Setores quentes:* {', '.join(context.hot_sectors[:3])}")
    
    lines.append("")
    lines.append("_Próximo pulso em 15min_")
    return "\n".join(lines)


def format_daily_briefing(context: MarketContext, signals: Any) -> str:
    lines = [
        "📋 *BRIEFING DIÁRIO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
    ]
    
    emoji = {"bullish": "🟢", "bearish": "🔴"}.get(context.sentiment, "⚪")
    lines.append(f"*Sentimento:* {emoji} {context.sentiment.upper()}")
    lines.append(f"*BTC:* `${context.btc_price:,.2f}`")
    lines.append(f"*Regime:* {context.regime} ({context.regime_direction})")
    lines.append(f"*Risco:* {context.risk_level}")
    
    if signals:
        lines.append("")
        lines.append("*📊 Top Sinais:*")
        for s in signals[:5] if hasattr(signals, '__iter__') else []:
            sym = getattr(s, 'symbol', 'N/A')
            sc = getattr(s, 'score', 0)
            d = getattr(s, 'direction', 'N/A')
            lines.append(f"• {sym} {d} — Score {sc:.0f}")
    
    lines.append("")
    lines.append("_Feedback ajuda a melhorar o sistema_")
    return "\n".join(lines)
