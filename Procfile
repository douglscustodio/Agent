"""
proactive_agent.py — Sistema de Alertas Proativos do Jarvis

Este módulo garante que o Jarvis seja um agente proativo que:
1. Mantém o usuário informado sobre o mercado automaticamente
2. Alerta sobre mudanças de regime
3. Aviso sobre notícias importantes
4. Pulso de mercado periódico
5. Alertas de volatilidade
6. Resumo de oportunidades detectadas
7. Alertas de funding extremo
8. Sinais de saída (quando mercado vira)
9. Dashboard de performance
10. APRENDIZADO AUTOMÁTICO - aprende com trades passados

O agente NÃO espera o usuário perguntar - ele ANTECIPA e INFORMA.
"""

import time
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from copy import deepcopy

from logger import get_logger
from notifier import _translate_news_title

log = get_logger("proactive")

REGIME_CHANGE_COOLDOWN = 300
NEWS_ALERT_COOLDOWN = 600
MARKET_PULSE_INTERVAL = 900
VOLATILITY_SPIKE_THRESHOLD = 0.03
FUNDING_ALERT_THRESHOLD = 0.01
EXIT_SIGNAL_COOLDOWN = 1800

TRADE_SUGGESTION_COOLDOWN = 3600


@dataclass
class MarketPulse:
    """Estado atual do mercado."""
    timestamp: float
    btc_price: float
    btc_change_1h: float
    regime: str
    regime_strength: float
    regime_direction: str
    top_opportunities: List[dict]
    sentiment: str
    risk_level: str
    macro_events: List[str]
    hot_sectors: List[str]
    cold_sectors: List[str]
    notable_news: List[str]
    funding_alerts: List[dict] = field(default_factory=list)


@dataclass
class ExitSignal:
    """Sinal de saída detectado."""
    symbol: str
    direction: str
    reason: str
    entry_score: float
    current_regime: str
    regime_changed: bool
    funding_extreme: bool
    time_in_trade_hours: float


@dataclass
class ProactiveState:
    """Estado interno do agente proativo."""
    last_regime: Optional[str] = None
    last_regime_time: float = 0.0
    last_pulse_time: float = 0.0
    last_news_alerts: Dict[str, float] = field(default_factory=dict)
    alerted_signals: Set[str] = field(default_factory=set)
    previous_btc_price: float = 0.0
    previous_regime: Optional[str] = None
    market_open_alerted: bool = False
    market_close_alerted: bool = False
    alerted_funding: Set[str] = field(default_factory=set)
    alerted_exits: Set[str] = field(default_factory=set)
    open_trades: Dict[str, dict] = field(default_factory=dict)
    last_sentiment: str = "neutral"
    last_trade_suggestion: float = 0.0
    learned_patterns: Dict[str, dict] = field(default_factory=dict)


class ProactiveAgent:
    def __init__(self):
        self._state = ProactiveState()
        self._last_pulse: Optional[MarketPulse] = None
        
    def _now(self) -> float:
        return time.time()
    
    def _should_send_pulse(self) -> bool:
        elapsed = self._now() - self._state.last_pulse_time
        return elapsed >= MARKET_PULSE_INTERVAL
    
    def _is_news_worthy(self, title: str, sentiment: str) -> bool:
        title_lower = title.lower()
        high_impact_keywords = [
            "bitcoin etf", "sec ", "federal reserve", "rate hike", "rate cut",
            "hack", "exploit", "ban", "crash", "surge", "all-time", "ath",
            "institutional", "blackrock", "fidelity", "approval", "rejection",
            "inflation", "cpi", "gdp", "recession", "sanctions", "war",
            "partnership", "listing", "delisting", "major", "breaking",
            "liquidations", "binance", "coinbase", "ftx", "bankruptcy",
        ]
        
        for kw in high_impact_keywords:
            if kw in title_lower:
                return True
        
        if sentiment in ("positive", "negative"):
            return True
        
        return False
    
    def track_signal(self, symbol: str, direction: str, score: float, entry_price: float) -> None:
        """Registra um sinal enviado para rastrear saída futura."""
        key = f"{symbol}:{direction}"
        self._state.open_trades[key] = {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "entry_price": entry_price,
            "entry_time": self._now(),
            "entry_regime": self._state.last_regime or "UNKNOWN",
        }
        self._state.alerted_signals.add(key)
    
    def check_exit_conditions(
        self, 
        symbol: str, 
        direction: str, 
        current_price: float,
        current_regime: str,
        funding_rate: float = None
    ) -> Optional[ExitSignal]:
        """Verifica se deve alertar saída."""
        key = f"{symbol}:{direction}"
        
        if key not in self._state.open_trades:
            return None
        
        if key in self._state.alerted_exits:
            elapsed = self._now() - self._state.alerted_exits[key]
            if elapsed < EXIT_SIGNAL_COOLDOWN:
                return None
        
        trade = self._state.open_trades[key]
        entry_price = trade["entry_price"]
        entry_time = trade["entry_time"]
        time_in_trade = (self._now() - entry_time) / 3600
        
        reasons = []
        regime_changed = False
        funding_extreme = False
        
        if current_regime != trade["entry_regime"] and trade["entry_regime"] != "UNKNOWN":
            regime_changed = True
            reasons.append(f"Regime mudou de {trade['entry_regime']} para {current_regime}")
        
        if direction == "LONG" and entry_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price * 100
            if pnl_pct <= -2:
                reasons.append(f"Prejuízo de {pnl_pct:.1f}% - verifique stop loss")
            elif pnl_pct >= 5:
                reasons.append(f"Lucro de {pnl_pct:.1f}% - considere realizar parcialmente")
        
        if funding_rate and funding_rate > FUNDING_ALERT_THRESHOLD:
            if direction == "LONG":
                funding_extreme = True
                reasons.append(f"Funding extremamente alto ({funding_rate*100:.2f}%) - risco de squeeze")
        
        if reasons:
            self._state.alerted_exits[key] = self._now()
            return ExitSignal(
                symbol=symbol,
                direction=direction,
                reason=" | ".join(reasons),
                entry_score=trade["score"],
                current_regime=current_regime,
                regime_changed=regime_changed,
                funding_extreme=funding_extreme,
                time_in_trade_hours=time_in_trade,
            )
        
        return None
    
    def should_alert_funding(self, symbol: str, funding_rate: float) -> bool:
        """Verifica se deve alertar funding extremo."""
        if funding_rate < FUNDING_ALERT_THRESHOLD:
            return False
        
        if symbol in self._state.alerted_funding:
            elapsed = self._now() - self._state.alerted_funding[symbol]
            if elapsed < 1800:
                return False
        
        self._state.alerted_funding.add(symbol)
        self._state.alerted_funding = {s for s in self._state.alerted_funding if s == symbol}
        return True
    
    async def build_market_pulse(
        self,
        btc_price: float,
        btc_closes: List[float],
        regime_result,
        news_articles: List,
        macro_snap = None,
        ranking_result = None,
        funding_data: Dict[str, float] = None,
    ) -> MarketPulse:
        """Constrói um pulso completo do mercado."""
        
        btc_change_1h = 0.0
        if self._state.previous_btc_price > 0 and len(btc_closes) >= 2:
            btc_change_1h = (btc_price - self._state.previous_btc_price) / self._state.previous_btc_price * 100
        
        regime_str = str(regime_result.regime).split(".")[-1] if regime_result else "UNKNOWN"
        regime_dir = regime_result.trend_direction if regime_result else "NEUTRAL"
        regime_adx = regime_result.adx if regime_result else 0
        
        sentiment = "neutral"
        if btc_change_1h > 2:
            sentiment = "bullish"
        elif btc_change_1h < -2:
            sentiment = "bearish"
        
        if self._state.last_sentiment != "neutral" and sentiment != self._state.last_sentiment:
            if abs(btc_change_1h) > 1:
                log.info("PROACTIVE", f"Market sentiment changed: {self._state.last_sentiment} -> {sentiment}")
        
        self._state.last_sentiment = sentiment
        
        risk_level = "LOW"
        if macro_snap:
            risk_score = getattr(macro_snap, "risk_score", 50)
            if risk_score >= 80:
                risk_level = "EXTREME"
            elif risk_score >= 70:
                risk_level = "HIGH"
            elif risk_score >= 50:
                risk_level = "MEDIUM"
        
        opportunities = []
        if ranking_result and ranking_result.top:
            for sig in ranking_result.top[:3]:
                opportunities.append({
                    "symbol": sig.symbol,
                    "direction": sig.direction,
                    "score": sig.score,
                    "band": str(sig.band),
                })
        
        notable_news = []
        if news_articles:
            for article in news_articles[:3]:
                title = getattr(article, "title", "")
                sent = getattr(article, "sentiment", "neutral")
                if self._is_news_worthy(title, sent):
                    translated_title = _translate_news_title(title)
                    notable_news.append(translated_title[:80])
        
        macro_events = []
        if macro_snap:
            events = getattr(macro_snap, "events", [])
            for event in events[:2]:
                title = getattr(event, "title", "")
                if title:
                    from notifier import _translate_macro_title
                    translated_title = _translate_macro_title(title)
                    macro_events.append(translated_title[:60])
        
        hot_sectors = []
        cold_sectors = []
        if ranking_result:
            sectors_seen: Set[str] = set()
            for sig in ranking_result.top:
                if len(hot_sectors) >= 3:
                    break
                from sector_rotation import get_sector_label
                sector = get_sector_label(sig.symbol)
                if sector not in sectors_seen:
                    sectors_seen.add(sector)
                    hot_sectors.append(sector)
        
        funding_alerts = []
        if funding_data:
            for sym, rate in funding_data.items():
                if rate > FUNDING_ALERT_THRESHOLD:
                    funding_alerts.append({
                        "symbol": sym,
                        "rate": rate,
                        "direction": "LONG" if rate > 0 else "SHORT",
                    })
        
        self._state.previous_btc_price = btc_price
        self._state.last_regime = regime_str
        
        pulse = MarketPulse(
            timestamp=self._now(),
            btc_price=btc_price,
            btc_change_1h=btc_change_1h,
            regime=regime_str,
            regime_strength=regime_adx,
            regime_direction=regime_dir,
            top_opportunities=opportunities,
            sentiment=sentiment,
            risk_level=risk_level,
            macro_events=macro_events,
            hot_sectors=hot_sectors,
            cold_sectors=cold_sectors,
            notable_news=notable_news,
            funding_alerts=funding_alerts,
        )
        
        self._last_pulse = pulse
        return pulse
    
    def should_alert_regime_change(self, new_regime: str) -> bool:
        if self._state.last_regime is None:
            self._state.last_regime = new_regime
            return False
        
        if new_regime == self._state.last_regime:
            return False
        
        elapsed = self._now() - self._state.last_regime_time
        if elapsed < REGIME_CHANGE_COOLDOWN:
            return False
        
        previous = self._state.last_regime
        self._state.last_regime = new_regime
        self._state.last_regime_time = self._now()
        self._state.previous_regime = previous
        return True
    
    def should_alert_news(self, news_key: str) -> bool:
        if news_key in self._state.last_news_alerts:
            elapsed = self._now() - self._state.last_news_alerts[news_key]
            if elapsed < NEWS_ALERT_COOLDOWN:
                return False
        
        self._state.last_news_alerts[news_key] = self._now()
        return True
    
    def reset_daily(self) -> None:
        self._state.last_news_alerts.clear()
        self._state.alerted_signals.clear()
        self._state.alerted_funding.clear()
        self._state.alerted_exits.clear()
        self._state.open_trades.clear()
        self._state.last_regime_time = 0.0
        self._state.last_pulse_time = 0.0
        log.info("PROACTIVE", "Daily reset complete")
    
    async def learn_from_outcomes(self, performance_records: List[dict]) -> dict:
        """Aprende com os resultados dos trades passados e retorna insights."""
        if not performance_records:
            return {}
        
        insights = {
            "best_symbols": {},
            "best_hours": {},
            "best_regimes": {},
            "best_score_range": {},
            "win_rate_by_direction": {"LONG": {"wins": 0, "total": 0}, "SHORT": {"wins": 0, "total": 0}},
        }
        
        wins = 0
        total = len(performance_records)
        
        for record in performance_records:
            symbol = record.get("symbol", "UNKNOWN")
            direction = record.get("direction", "LONG")
            outcome = record.get("outcome", "NEUTRAL")
            score = record.get("score", 50)
            regime = record.get("regime", "UNKNOWN")
            hour = record.get("hour", 12)
            
            is_win = outcome in ("TP1", "NEUTRAL")
            if is_win:
                wins += 1
            
            if symbol not in insights["best_symbols"]:
                insights["best_symbols"][symbol] = {"wins": 0, "total": 0}
            insights["best_symbols"][symbol]["total"] += 1
            if is_win:
                insights["best_symbols"][symbol]["wins"] += 1
            
            hour_key = f"{hour}h"
            if hour_key not in insights["best_hours"]:
                insights["best_hours"][hour_key] = {"wins": 0, "total": 0}
            insights["best_hours"][hour_key]["total"] += 1
            if is_win:
                insights["best_hours"][hour_key]["wins"] += 1
            
            if regime not in insights["best_regimes"]:
                insights["best_regimes"][regime] = {"wins": 0, "total": 0}
            insights["best_regimes"][regime]["total"] += 1
            if is_win:
                insights["best_regimes"][regime]["wins"] += 1
            
            score_bucket = "low" if score < 50 else ("medium" if score < 70 else "high")
            if score_bucket not in insights["best_score_range"]:
                insights["best_score_range"][score_bucket] = {"wins": 0, "total": 0}
            insights["best_score_range"][score_bucket]["total"] += 1
            if is_win:
                insights["best_score_range"][score_bucket]["wins"] += 1
            
            if direction not in insights["win_rate_by_direction"]:
                insights["win_rate_by_direction"][direction] = {"wins": 0, "total": 0}
            insights["win_rate_by_direction"][direction]["total"] += 1
            if is_win:
                insights["win_rate_by_direction"][direction]["wins"] += 1
        
        insights["overall_win_rate"] = (wins / total * 100) if total > 0 else 0
        
        for key in ["best_symbols", "best_hours", "best_regimes", "best_score_range"]:
            for k, v in insights[key].items():
                total_k = v.get("total", 1)
                wins_k = v.get("wins", 0)
                v["win_rate"] = (wins_k / total_k * 100) if total_k > 0 else 0
        
        for direction in ["LONG", "SHORT"]:
            if direction in insights["win_rate_by_direction"]:
                d_data = insights["win_rate_by_direction"][direction]
                d_data["win_rate"] = (d_data.get("wins", 0) / d_data.get("total", 1) * 100) if d_data.get("total", 0) > 0 else 0
        
        self._state.learned_patterns = insights
        log.info("PROACTIVE", f"Learned from {total} trades, win rate: {insights['overall_win_rate']:.1f}%")
        
        return insights
    
    def should_suggest_trade(self) -> bool:
        """Verifica se deve fazer uma sugestão de trade proativa."""
        elapsed = self._now() - self._state.last_trade_suggestion
        return elapsed >= TRADE_SUGGESTION_COOLDOWN
    
    def get_best_conditions(self) -> dict:
        """Retorna as melhores condições aprendidas para fazer trades."""
        insights = self._state.learned_patterns
        
        if not insights:
            return {"message": "Ainda aprendendo... aguarde mais trades para eu analisar padrões."}
        
        best_symbol = None
        best_wr = 0
        for sym, data in insights.get("best_symbols", {}).items():
            if data.get("total", 0) >= 3:
                wr = data.get("win_rate", 0)
                if wr > best_wr:
                    best_wr = wr
                    best_symbol = sym
        
        best_hour = None
        best_hour_wr = 0
        for hour, data in insights.get("best_hours", {}).items():
            if data.get("total", 0) >= 2:
                wr = data.get("win_rate", 0)
                if wr > best_hour_wr:
                    best_hour_wr = wr
                    best_hour = hour
        
        best_regime = None
        best_regime_wr = 0
        for regime, data in insights.get("best_regimes", {}).items():
            if data.get("total", 0) >= 3:
                wr = data.get("win_rate", 0)
                if wr > best_regime_wr:
                    best_regime_wr = wr
                    best_regime = regime
        
        return {
            "best_symbol": best_symbol,
            "best_symbol_wr": best_wr,
            "best_hour": best_hour,
            "best_hour_wr": best_hour_wr,
            "best_regime": best_regime,
            "best_regime_wr": best_regime_wr,
            "overall_wr": insights.get("overall_win_rate", 0),
            "long_wr": insights.get("win_rate_by_direction", {}).get("LONG", {}).get("win_rate", 0),
            "short_wr": insights.get("win_rate_by_direction", {}).get("SHORT", {}).get("win_rate", 0),
        }
    
    def format_ai_trade_suggestion(self, conditions: dict, current_opportunities: List[dict]) -> str:
        """Formata uma sugestão de trade baseada no aprendizado."""
        if not current_opportunities:
            return ""
        
        current_hour = datetime.now(timezone.utc).hour
        current_hour_key = f"{current_hour}h"
        
        best_conditions = self.get_best_conditions()
        
        suggestions = []
        
        for opp in current_opportunities[:2]:
            symbol = opp.get("symbol", "")
            direction = opp.get("direction", "LONG")
            score = opp.get("score", 50)
            
            confidence_bonus = 0
            reason_parts = []
            
            sym_wr = best_conditions.get("best_symbol_wr", 0)
            if best_conditions.get("best_symbol") == symbol and sym_wr > 50:
                confidence_bonus += 15
                reason_parts.append(f"✅ {symbol} tem histórico bom ({sym_wr:.0f}% win rate)")
            
            hour_wr = best_conditions.get("best_hour_wr", 0)
            if best_conditions.get("best_hour") == current_hour_key and hour_wr > 50:
                confidence_bonus += 10
                reason_parts.append(f"⏰ Horário propício ({hour_wr:.0f}% win rate)")
            
            regime_wr = best_conditions.get("best_regime_wr", 0)
            if best_conditions.get("best_regime"):
                reason_parts.append(f"📊 Regime: {best_conditions.get('best_regime')} ({regime_wr:.0f}% win rate)")
            
            adjusted_score = min(100, score + confidence_bonus)
            
            emoji_dir = "📈" if direction == "LONG" else "📉"
            
            suggestion_text = (
                f"{emoji_dir} *SUGESTÃO: {symbol}/USDT*\n"
                f"   Direção: {direction} | Score: `{adjusted_score:.0f}/100`\n"
                f"   Baseado em: {', '.join(reason_parts) if reason_parts else 'Análise atual'}"
            )
            suggestions.append(suggestion_text)
        
        if suggestions:
            overall_wr = best_conditions.get("overall_wr", 0)
            intro = (
                f"🧠 *MINHA ANÁLISE APRENDIDA*\n"
                f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_\n\n"
                f"📈 Win rate geral: `{overall_wr:.1f}%`\n"
                f"📈 WIN LONGs: `{best_conditions.get('long_wr', 0):.1f}%` | "
                f"📉 WIN SHORTs: `{best_conditions.get('short_wr', 0):.1f}%`\n\n"
                f"Oportunidades que identifico:\n"
            )
            return intro + "\n\n".join(suggestions)
        
        return ""
    
    def mark_suggestion_sent(self) -> None:
        """Marca que uma sugestão de trade foi enviada."""
        self._state.last_trade_suggestion = self._now()


# ============================================================================
# FORMATAÇÃO DE MENSAGENS
# ============================================================================

def format_market_pulse_message(pulse: MarketPulse) -> str:
    lines = [
        "📊 *PULSO DO MERCADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
    ]
    
    emoji = "🟢" if pulse.sentiment == "bullish" else ("🔴" if pulse.sentiment == "bearish" else "⚪")
    sentiment_pt = {
        "bullish": "ALTISTA", "bearish": "BAIXISTA", "neutral": "NEUTRO"
    }.get(pulse.sentiment, "NEUTRO")
    
    lines.append(f"*Mercado:* {emoji} {sentiment_pt}")
    lines.append(f"*BTC:* `${pulse.btc_price:,.2f}` ({pulse.btc_change_1h:+.2f}%)")
    
    regime_pt = {
        "TRENDING": "📈 Em Tendência",
        "RANGING": "↔️ Lateral",
        "WEAK": "🔄 Fraco",
    }.get(pulse.regime, "❓ Indefinido")
    
    dir_pt = {
        "UP": "Para cima ⬆️",
        "DOWN": "Para baixo ⬇️",
        "NEUTRAL": "Sem direção ➡️",
    }.get(pulse.regime_direction, "➡️")
    
    lines.append(f"*Regime:* {regime_pt}")
    lines.append(f"*Direção:* {dir_pt}")
    lines.append(f"*Força:* `{pulse.regime_strength:.1f}`")
    
    risk_emoji = "🔴" if pulse.risk_level == "HIGH" else ("🟡" if pulse.risk_level == "MEDIUM" else "🟢")
    lines.append(f"*Risco:* {risk_emoji} {pulse.risk_level}")
    
    if pulse.funding_alerts:
        lines.append("")
        lines.append("*⚠️ Funding Extremo:*")
        for fa in pulse.funding_alerts[:2]:
            lines.append(f"• {fa['symbol']}: {fa['rate']*100:.2f}% (risco squeeze)")
    
    lines.append("")
    
    if pulse.notable_news:
        lines.append("*📰 Destaque:*")
        for news in pulse.notable_news[:2]:
            lines.append(f"• {news}...")
        lines.append("")
    
    if pulse.top_opportunities:
        lines.append("*🎯 Oportunidades:*")
        for opp in pulse.top_opportunities[:2]:
            emoji_dir = "📈" if opp["direction"] == "LONG" else "📉"
            lines.append(f"• {emoji_dir} {opp['symbol']} {opp['direction']} — Score {opp['score']:.0f}")
        lines.append("")
    elif pulse.hot_sectors:
        lines.append(f"*Setores quentes:* {', '.join(pulse.hot_sectors[:3])}")
        lines.append("")
    
    if pulse.macro_events:
        lines.append("*🌍 Macro:*")
        for event in pulse.macro_events[:2]:
            lines.append(f"• {event}...")
        lines.append("")
    
    lines.append("_Próximo pulso em 15min_")
    return "\n".join(lines)


def format_regime_change_message(new_regime: str, direction: str, strength: float, previous: str) -> str:
    regime_pt = {
        "TRENDING": "📈 TENDÊNCIA ESTABELECIDA",
        "RANGING": "↔️ MERCADO LATERAL",
        "WEAK": "🔄 TENDÊNCIA FRACA",
    }.get(new_regime, new_regime)
    
    dir_pt = {
        "UP": "Para cima ⬆️",
        "DOWN": "Para baixo ⬇️",
        "NEUTRAL": "Sem direção clara ➡️",
    }.get(direction, direction)
    
    previous_pt = {
        "TRENDING": "tendência", "RANGING": "lateral", "WEAK": "fraco"
    }.get(previous, previous)
    
    lines = [
        "⚡ *MUDANÇA DE REGIME DETECTADA!*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*De:* Mercado {previous_pt}",
        f"*Para:* {regime_pt}",
        f"*Direção:* {dir_pt}",
        f"*Força:* `{strength:.1f}`",
        "",
    ]
    
    if new_regime == "TRENDING":
        lines.append("💡 *O que fazer:*")
        lines.append("• Trades com tendência têm mais chance de sucesso")
        lines.append("• Procure confirmações no sentido da tendência")
        lines.append("• Stops mais apertados podem funcionar")
    elif new_regime == "RANGING":
        lines.append("💡 *O que fazer:*")
        lines.append("• Mercados laterais pedem paciência")
        lines.append("• Procure setups em extremos do range")
        lines.append("• Considere estratégias de range-bound")
    else:
        lines.append("💡 *O que fazer:*")
        lines.append("• Mercado sem direção clara")
        lines.append("• Reduza tamanho de posições")
        lines.append("• Aguarde confirmação")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


def format_important_news_alert(title: str, sentiment: str, url: str = "") -> str:
    emoji = "🟢" if sentiment == "positive" else ("🔴" if sentiment == "negative" else "⚪")
    translated_title = _translate_news_title(title)
    
    lines = [
        f"{emoji} *NOTÍCIA RELEVANTE*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"_{translated_title[:200]}_",
    ]
    
    if url:
        lines.append(f"[Ver详情]({url})")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


def format_volatility_alert(symbol: str, direction: str, change_pct: float, reason: str = "") -> str:
    emoji = "🚀" if change_pct > 0 else "💥"
    
    lines = [
        f"{emoji} *MOVIMENTO BRUSCO — {symbol}*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*Direção:* {direction}",
        f"*Variação:* `{change_pct:+.2f}%`",
    ]
    
    if reason:
        lines.append(f"*Motivo:* {reason}")
    
    lines.append("")
    lines.append("💡 Verifique seu stop loss e gestão de risco!")
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


def format_funding_alert(symbol: str, funding_rate: float, direction: str = "LONG") -> str:
    lines = [
        "⚠️ *FUNDING EXTREMO DETECTADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*Ativo:* {symbol}",
        f"*Funding 8h:* `{funding_rate*100:.3f}%`",
        "",
    ]
    
    if funding_rate > 0.015:
        lines.append("🔴 *EXTREMAMENTE ALTO*")
        lines.append("• Risco muito alto de long squeeze")
        lines.append("• Evite entradas LONG")
        lines.append("• Se tem posição LONG, considere sair")
    elif funding_rate > 0.01:
        lines.append("🟡 *MUITO ALTO*")
        lines.append("• Cuidado com posições LONG")
        lines.append("• Funding pode indicar reversão")
    
    lines.append("")
    lines.append("💡 *Histórico:* Funding acima de 1% frequentemente precede squeeze")
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


def format_exit_signal_alert(exit_signal: ExitSignal) -> str:
    emoji = "🚨"
    
    lines = [
        f"{emoji} *SINAL DE SAÍDA DETECTADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*Ativo:* {exit_signal.symbol}",
        f"*Direção:* {exit_signal.direction}",
        f"*Tempo no trade:* {exit_signal.time_in_trade_hours:.1f}h",
        "",
        "*⚠️ Motivos:*",
        f"{exit_signal.reason}",
        "",
    ]
    
    if exit_signal.regime_changed:
        lines.append("🔄 *Regime mudou* — estratégia original pode não funcionar mais")
    
    if exit_signal.funding_extreme:
        lines.append("💸 *Funding extremo* — risco de liquidação de longs")
    
    lines.append("")
    lines.append("💡 *Recomendação:*")
    lines.append("• Reveja seu stop loss")
    lines.append("• Considere realização parcial")
    lines.append("• Aguarde confirmação antes de reentrar")
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


def format_squeeze_warning(symbol: str, reasons: List[str]) -> str:
    lines = [
        "⚠️ *AVISO: SQUEEZE DETECTADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*Ativo:* {symbol}",
        "",
        "*Fatores de risco:*",
    ]
    
    for reason in reasons[:3]:
        lines.append(f"• {reason}")
    
    lines.append("")
    lines.append("💡 *Recomendação:*")
    lines.append("• Evite entradas na direção do funding alto")
    lines.append("• Se já tem posição, considere proteção adicional")
    lines.append("• Aguarde limpeza de posições antes de entrar")
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


def format_opportunity_summary(signals: List, news_map: dict = None) -> str:
    if not signals:
        return ""
    
    lines = [
        "🎯 *OPORTUNIDADES DO CICLO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
    ]
    
    for sig in signals[:5]:
        emoji_dir = "📈" if sig.direction == "LONG" else "📉"
        band_emoji = "🔥" if "HIGH" in str(sig.band) else "✅"
        
        lines.append(f"{emoji_dir} *{sig.symbol}/USDT* {band_emoji}")
        lines.append(f"   Direção: {sig.direction} | Score: `{sig.score:.0f}/100`")
        
        if news_map and sig.symbol in news_map:
            news_ctx = news_map[sig.symbol]
            if news_ctx and news_ctx.articles:
                title = news_ctx.articles[0].title[:50]
                translated_title = _translate_news_title(title)
                lines.append(f"   📰 {translated_title}...")
        lines.append("")
    
    lines.append("_Use /sinais para detalhes completos_")
    return "\n".join(lines)


def format_performance_dashboard(stats: dict, period: str = "24h") -> str:
    total = stats.get("total", 0)
    tp1 = stats.get("tp1", 0)
    sl = stats.get("sl", 0)
    neutral = stats.get("neutral", 0)
    win_rate = stats.get("win_rate", 0.0)
    avg_pnl = stats.get("avg_pnl", 0.0)
    best = stats.get("best_trade", 0.0)
    worst = stats.get("worst_trade", 0.0)
    
    win_emoji = "🟢" if win_rate >= 60 else ("🟡" if win_rate >= 40 else "🔴")
    
    lines = [
        "📈 *DASHBOARD DE PERFORMANCE*",
        f"_{period}_",
        "",
        f"*Total de Sinais:* `{total}`",
        "",
        "*📊 Taxa de Acerto:*",
        f"{win_emoji} `{win_rate:.1f}%`",
        f"✅ Acertos (TP1): `{tp1}`",
        f"❌ Erros (SL): `{sl}`",
        f"➖ Neutros: `{neutral}`",
        "",
        "*💰 PnL:*",
        f"Média: `{avg_pnl:+.2f}%`",
        f"Melhor: `{best:+.2f}%`" if best else "",
        f"Pior: `{worst:+.2f}%`" if worst else "",
        "",
    ]
    
    if total > 0:
        if win_rate >= 65:
            lines.append("🟢 *Excelente!* Sistema performing acima da média")
        elif win_rate >= 50:
            lines.append("🟡 *Bom!* Continue assim")
        else:
            lines.append("🔴 *Atenção:* Taxa de acerto abaixo de 50%")
            lines.append("• Sistema pode precisar de ajustes")
            lines.append("• Verifique contexto macro")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


def format_sentiment_summary(
    btc_change_1h: float,
    btc_change_4h: float,
    btc_change_24h: float,
    market_sentiment: str,
    fear_greed_value: int = None,
) -> str:
    emoji = "🟢" if market_sentiment == "bullish" else ("🔴" if market_sentiment == "bearish" else "⚪")
    
    sentiment_pt = {
        "bullish": "ALTISTA", "bearish": "BAIXISTA", "neutral": "NEUTRO"
    }.get(market_sentiment, "NEUTRO")
    
    lines = [
        f"{emoji} *SENTIMENTO DO MERCADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*Direção:* {sentiment_pt}",
        "",
        "*BTC Performance:*",
        f"• 1h:  `{btc_change_1h:+.2f}%`",
        f"• 4h:  `{btc_change_4h:+.2f}%`",
        f"• 24h: `{btc_change_24h:+.2f}%`",
    ]
    
    if fear_greed_value is not None:
        fg_emoji = "🟢" if fear_greed_value >= 55 else ("🔴" if fear_greed_value <= 45 else "🟡")
        fg_label = "Medo" if fear_greed_value <= 40 else ("Ganância" if fear_greed_value >= 60 else "Neutro")
        lines.append("")
        lines.append(f"*Fear & Greed:* {fg_emoji} {fear_greed_value} ({fg_label})")
    
    lines.append("")
    
    if market_sentiment == "bullish" and btc_change_24h > 5:
        lines.append("💡 Momento comprador forte — тенденция是你的朋友")
    elif market_sentiment == "bearish" and btc_change_24h < -5:
        lines.append("💡 Pressão vendedora — cautela com entradas")
    elif market_sentiment == "neutral":
        lines.append("💡 Mercado indeciso — aguarde direção clara")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    return "\n".join(lines)


_agent: Optional[ProactiveAgent] = None


def get_proactive_agent() -> ProactiveAgent:
    global _agent
    if _agent is None:
        _agent = ProactiveAgent()
    return _agent
