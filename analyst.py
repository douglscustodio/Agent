"""
analyst.py — Jarvis como Analista Profissional de Trading

Este módulo transforma o Jarvis em um verdadeiro ANALISTA PROFISSIONAL
que opera ao seu lado 24/7, como se tivesse um mentor de trading expert.

Características:
- Comunicação de analista profissional (estilo Bloomberg/TradingView)
- Explica o "PORQUÊ" por trás de cada decisão
- Ensina gestão de risco e position sizing
- Mantém perspectiva macro sempre presente
- Oferece psicológico apoio durante volatilidade
- Sugere pontos de entrada, saída e gestão de posição
- Daily briefing estilo relatório de analistas
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
import random

from logger import get_logger

log = get_logger("analyst")

REGIME_CHANGE_COOLDOWN = 300
NEWS_ALERT_COOLDOWN = 600
MARKET_PULSE_INTERVAL = 900


@dataclass
class TradeSetup:
    """Setup de trade identificado."""
    symbol: str
    direction: str
    score: float
    entry_zone: str
    stop_loss: str
    take_profit_1: str
    take_profit_2: str
    position_size_advice: str
    reasoning: List[str]
    risk_reward: str


@dataclass
class MarketContext:
    """Contexto completo do mercado."""
    btc_price: float
    btc_change_1h: float
    btc_change_4h: float
    btc_change_24h: float
    regime: str
    regime_direction: str
    regime_strength: float
    sentiment: str
    risk_level: str
    key_levels: Dict[str, float]
    macro_events: List[str]
    notable_news: List[str]


@dataclass
class AnalystState:
    """Estado interno do analista."""
    last_regime: Optional[str] = None
    last_regime_time: float = 0.0
    last_pulse_time: float = 0.0
    last_briefing_time: float = 0.0
    current_positions: Dict[str, dict] = field(default_factory=dict)
    alerted_signals: Set[str] = field(default_factory=set)
    last_news_alerts: Dict[str, float] = field(default_factory=dict)
    trading_lessons_delivered: Set[str] = field(default_factory=set)


class TradingAnalyst:
    """
    Jarvis como Analista Profissional de Trading.
    
    Este módulo transforma alertas técnicos em lições de trading,
    explicando o raciocínio por trás de cada recomendação.
    """
    
    def __init__(self):
        self._state = AnalystState()
        self._lessons = self._load_trading_lessons()
        self._market_sayings = self._load_market_sayings()
    
    def _now(self) -> float:
        return time.time()
    
    def _load_trading_lessons(self) -> List[str]:
        """Lições de trading para entregar organicamente."""
        return [
            "Lembre-se: o mercado paga para dar a razão aos pacientes.",
            "Proteja seu capital primeiro. Lucros vêm depois.",
            "Não é sobre acertar todos os trades - é sobre gerenciar risco.",
            "A tendência é sua amiga até o final. - Jesse Livermore",
            "Quando você não tem certeza, o melhor trade é não fazer nenhum.",
            "Corte perdas rapidamente, deixe lucros correrem.",
            "Volatilidade não é risco - não gerenciar risco é risco.",
            "O pior inimigo do trader é ele mesmo.",
            "Não opere por medo de perder. Opera por convicção.",
            "Cada trade é uma lição. Aprenda com os erros.",
        ]
    
    def _load_market_sayings(self) -> List[str]:
        """Provérbios de mercado."""
        return [
            "Bulls make money, bears make money, pigs get slaughtered.",
            "Buy the rumor, sell the news.",
            "Don't fight the tape.",
            "The trend is your friend until the end.",
            "Markets can remain irrational longer than you can remain solvent.",
            "Cut your losses, let your winners ride.",
            "Never risk more than 1-2% on a single trade.",
            "If you can't afford to lose, you can't afford to win.",
        ]
    
    def set_current_position(self, symbol: str, direction: str, entry_price: float, 
                           entry_time: float = None, stop_loss: float = None,
                           take_profit: float = None) -> None:
        """Registra uma posição atual para acompanhamento."""
        self._state.current_positions[symbol] = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "entry_time": entry_time or self._now(),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
    
    def _should_send_pulse(self) -> bool:
        elapsed = self._now() - self._state.last_pulse_time
        return elapsed >= MARKET_PULSE_INTERVAL
    
    def _is_high_impact_news(self, title: str, sentiment: str) -> bool:
        """Determina se notícia merece atenção de analista."""
        title_lower = title.lower()
        high_impact = [
            "fed", "federal reserve", "rate", "interest",
            "sec", "etf", "approval", "institutional",
            "hack", "exploit", "ban", "regulation",
            "crash", "surge", "breakout", "breakdown",
            "blackrock", "fidelity", "massive", "critical",
        ]
        return any(kw in title_lower for kw in high_impact)
    
    def analyze_market(self, btc_price: float, btc_closes: List[float],
                      regime_result, macro_snap = None) -> MarketContext:
        """Analisa contexto completo do mercado."""
        
        change_1h = 0.0
        change_4h = 0.0
        change_24h = 0.0
        
        if len(btc_closes) >= 2:
            change_1h = (btc_price - btc_closes[-2]) / btc_closes[-2] * 100
        
        if len(btc_closes) >= 5:
            change_4h = (btc_price - btc_closes[-5]) / btc_closes[-5] * 100
        
        if len(btc_closes) >= 25:
            change_24h = (btc_price - btc_closes[-25]) / btc_closes[-25] * 100
        
        regime_str = str(regime_result.regime).split(".")[-1] if regime_result else "UNKNOWN"
        regime_dir = regime_result.trend_direction if regime_result else "NEUTRAL"
        regime_adx = regime_result.adx if regime_result else 0
        
        sentiment = "NEUTRAL"
        if change_1h > 1.5:
            sentiment = "BULLISH"
        elif change_1h < -1.5:
            sentiment = "BEARISH"
        
        risk_level = "LOW"
        if macro_snap:
            risk_score = getattr(macro_snap, "risk_score", 50)
            if risk_score >= 75:
                risk_level = "HIGH"
            elif risk_score >= 60:
                risk_level = "MEDIUM"
        
        key_levels = self._calculate_key_levels(btc_price, btc_closes)
        
        macro_events = []
        notable_news = []
        if macro_snap:
            for event in getattr(macro_snap, "events", [])[:3]:
                macro_events.append(getattr(event, "title", ""))
        
        return MarketContext(
            btc_price=btc_price,
            btc_change_1h=change_1h,
            btc_change_4h=change_4h,
            btc_change_24h=change_24h,
            regime=regime_str,
            regime_direction=regime_dir,
            regime_strength=regime_adx,
            sentiment=sentiment,
            risk_level=risk_level,
            key_levels=key_levels,
            macro_events=macro_events,
            notable_news=notable_news,
        )
    
    def _calculate_key_levels(self, price: float, closes: List[float]) -> Dict[str, float]:
        """Calcula níveis técnicos importantes."""
        if not closes:
            return {}
        
        highs = max(closes) if closes else price
        lows = min(closes) if closes else price
        
        return {
            "resistance_1": highs * 0.99 if highs > price else price * 1.02,
            "support_1": lows * 1.01 if lows < price else price * 0.98,
            "current": price,
        }
    
    def should_alert_regime_change(self, new_regime: str) -> bool:
        if self._state.last_regime is None:
            self._state.last_regime = new_regime
            return False
        
        if new_regime == self._state.last_regime:
            return False
        
        elapsed = self._now() - self._state.last_regime_time
        if elapsed < REGIME_CHANGE_COOLDOWN:
            return False
        
        self._state.last_regime = new_regime
        self._state.last_regime_time = self._now()
        return True
    
    def get_trade_setup(self, symbol: str, direction: str, score: float,
                       current_price: float, volatility: float = 0.02) -> TradeSetup:
        """Gera setup completo de trade com gestão."""
        
        risk_pct = 2.0
        
        if direction == "LONG":
            stop_pct = volatility * 2
            stop_price = current_price * (1 - stop_pct)
            tp1_price = current_price * (1 + stop_pct)
            tp2_price = current_price * (1 + stop_pct * 2.5)
            entry_zone = f"${current_price:,.2f} - ${current_price * 1.005:,.2f}"
        else:
            stop_pct = volatility * 2
            stop_price = current_price * (1 + stop_pct)
            tp1_price = current_price * (1 - stop_pct)
            tp2_price = current_price * (1 - stop_pct * 2.5)
            entry_zone = f"${current_price:,.2f} - ${current_price * 0.995:,.2f}"
        
        risk_reward = "1:2.5"
        
        reasoning = []
        if score >= 80:
            reasoning.append("Setup de alta qualidade -(edge confirmado)")
        elif score >= 65:
            reasoning.append("Setup válido - aguarde confirmação")
        
        if volatility > 0.03:
            reasoning.append("⚠️ Volatilidade elevada - reduza tamanho")
        
        reasoning.append(f"Risco máximo: {risk_pct}% do capital")
        
        return TradeSetup(
            symbol=symbol,
            direction=direction,
            score=score,
            entry_zone=entry_zone,
            stop_loss=f"${stop_price:,.2f} ({stop_pct*100:.1f}%)",
            take_profit_1=f"${tp1_price:,.2f}",
            take_profit_2=f"${tp2_price:,.2f}",
            position_size_advice=f"Máx {risk_pct}% do capital por trade",
            reasoning=reasoning,
            risk_reward=risk_reward,
        )


# ============================================================================
# FORMATAÇÃO DE MENSAGENS - ESTILO ANALISTA PROFISSIONAL
# ============================================================================

def format_daily_briefing(context: MarketContext, top_signals: List = None) -> str:
    """Relatório diário estilo analista profissional."""
    
    lines = [
        "📊 *RELATÓRIO DIÁRIO DO MERCADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y às %H:%M UTC')}_",
        "",
        "━" * 30,
        "",
    ]
    
    emoji_dir = "🟢" if context.sentiment == "BULLISH" else ("🔴" if context.sentiment == "BEARISH" else "⚪")
    sentiment_pt = {"BULLISH": "ALTISTA", "BEARISH": "BAIXISTA", "NEUTRAL": "NEUTRO"}.get(context.sentiment, "NEUTRO")
    
    lines.append(f"*{emoji_dir} VISÃO GERAL*")
    lines.append(f"BTC: ${context.btc_price:,.2f}")
    lines.append(f"Variação 1h: {context.btc_change_1h:+.2f}%")
    lines.append(f"Variação 4h: {context.btc_change_4h:+.2f}%")
    lines.append(f"Variação 24h: {context.btc_change_24h:+.2f}%")
    lines.append(f"Sentimento: {sentiment_pt}")
    lines.append("")
    
    regime_pt = {
        "TRENDING": "EM TENDÊNCIA 📈",
        "RANGING": "LATERALIZANDO ↔️",
        "WEAK": "SEM DIREÇÃO 🔄",
    }.get(context.regime, context.regime)
    
    dir_pt = {"UP": "Alta", "DOWN": "Queda", "NEUTRAL": "Neutro"}.get(context.regime_direction, "")
    
    lines.append("━" * 30)
    lines.append("")
    lines.append("*📐 ANÁLISE TÉCNICA*")
    lines.append(f"Regime: {regime_pt}")
    lines.append(f"Direção: {dir_pt}")
    lines.append(f"Força (ADX): {context.regime_strength:.1f}")
    
    if context.key_levels:
        lines.append("")
        lines.append("*Níveis Importantes:*")
        lines.append(f"  Resistência: ${context.key_levels.get('resistance_1', 0):,.2f}")
        lines.append(f"  Suporte: ${context.key_levels.get('support_1', 0):,.2f}")
    
    lines.append("")
    lines.append("━" * 30)
    lines.append("")
    
    risk_emoji = "🔴" if context.risk_level == "HIGH" else ("🟡" if context.risk_level == "MEDIUM" else "🟢")
    lines.append(f"*{risk_emoji} NÍVEL DE RISCO:* {context.risk_level}")
    
    if context.macro_events:
        lines.append("")
        lines.append("*🌍 CONTEXTO MACRO:*")
        for event in context.macro_events[:2]:
            if event:
                lines.append(f"• {event[:60]}...")
    
    if top_signals:
        lines.append("")
        lines.append("━" * 30)
        lines.append("")
        lines.append("*🎯 OPORTUNIDADES IDENTIFICADAS*")
        for sig in top_signals[:3]:
            emoji = "📈" if sig.direction == "LONG" else "📉"
            lines.append(f"{emoji} *{sig.symbol}/USDT* - {sig.direction}")
            lines.append(f"   Score: {sig.score:.0f}/100")
            lines.append("")
    
    lines.append("━" * 30)
    lines.append("")
    lines.append("*💡 DICA DO ANALISTA:*")
    lines.append(f"_{random.choice([
        'Lembre-se: paciência é virtue no trading.',
        'Não persiga perdas - aceite e siga em frente.',
        'A tendência é sua amiga até o final.',
        'Proteja seu capital antes de buscar lucros.',
        'O mercado estará lá amanhã - não force operações.',
    ])}_")
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    
    return "\n".join(lines)


def format_market_pulse(context: MarketContext, signal_alert: str = None) -> str:
    """Pulso rápido do mercado - atualização periódica."""
    
    emoji = "🟢" if context.sentiment == "BULLISH" else ("🔴" if context.sentiment == "BEARISH" else "⚪")
    
    lines = [
        f"📊 *PULSO DO MERCADO* {emoji}",
        f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
        "",
        f"BTC: ${context.btc_price:,.2f} ({context.btc_change_1h:+.2f}%)",
        f"Regime: {context.regime} | ADX: {context.regime_strength:.0f}",
    ]
    
    if signal_alert:
        lines.append("")
        lines.append(f"*{signal_alert}*")
    
    lines.append("")
    lines.append("_Próximo pulso em 15min_")
    
    return "\n".join(lines)


def format_regime_change_alert(previous: str, new: str, direction: str, 
                               strength: float, advice: str = None) -> str:
    """Alerta de mudança de regime com análise."""
    
    new_pt = {
        "TRENDING": "📈 TENDÊNCIA ESTABELECIDA",
        "RANGING": "↔️ LATERALIZAÇÃO",
        "WEAK": "🔄 INDECISÃO",
    }.get(new, new)
    
    dir_pt = {"UP": "para ALTA", "DOWN": "para BAIXA", "NEUTRAL": "sem direção"}.get(direction, "")
    
    lines = [
        "⚡ *ANÁLISE: MUDANÇA DE REGIME*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"O mercado mudou de *{previous}* para *{new_pt}*",
        f"Direção identificada: *{dir_pt}*",
        f"Força da tendência (ADX): *{strength:.1f}*",
        "",
    ]
    
    if new == "TRENDING":
        lines.append("*📚 MINHA ANÁLISE:*")
        lines.append("Tendência definida = maior probabilidade de sucesso.")
        lines.append("Procure entradas no sentido da tendência.")
        lines.append("Stops podem ser mais apertados.")
    elif new == "RANGING":
        lines.append("*📚 MINHA ANÁLISE:*")
        lines.append("Mercado lateral = sem direção clara.")
        lines.append("Melhor operar nos extremos do range.")
        lines.append("Considere reduz o tamanho das posições.")
    else:
        lines.append("*📚 MINHA ANÁLISE:*")
        lines.append("Mercado indeciso = aguardar.")
        lines.append("Não force entradas sem confirmação.")
        lines.append("Paciência é fundamental aqui.")
    
    if advice:
        lines.append("")
        lines.append(f"*🎯 RECOMENDAÇÃO:* {advice}")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    
    return "\n".join(lines)


def format_signal_with_education(symbol: str, direction: str, score: float,
                                setup: TradeSetup, news_context: str = None) -> str:
    """Signal formatado com educação de trading."""
    
    emoji = "📈" if direction == "LONG" else "📉"
    quality = "ALTA QUALIDADE 🔥" if score >= 75 else ("QUALIDADE BOA ✅" if score >= 60 else "MODERADO ⚠️")
    
    lines = [
        f"{emoji} *ANÁLISE: {symbol}/USDT*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*Direção:* {direction}",
        f"*Score:* {score:.0f}/100 ({quality})",
        "",
        "━" * 25,
        "",
        "*📐 SETUP COMPLETO:*",
        "",
        f"📍 *Zona de Entrada:* {setup.entry_zone}",
        f"🛑 *Stop Loss:* {setup.stop_loss}",
        f"🎯 *TP1:* {setup.take_profit_1}",
        f"🎯 *TP2:* {setup.take_profit_2}",
        f"⚖️ *Risk/Reward:* {setup.risk_reward}",
        "",
        "*💰 GESTÃO DE POSIÇÃO:*")
    
    for reason in setup.reasoning:
        lines.append(f"• {reason}")
    
    if news_context:
        lines.append("")
        lines.append(f"*📰 CONTEXTO:* {news_context[:80]}...")
    
    lines.append("")
    lines.append("━" * 25)
    lines.append("")
    lines.append("*📚 PORQUÊ DESTE SETUP:*")
    
    if direction == "LONG":
        if score >= 75:
            lines.append("Este ativo está demonstrando força contra o BTC.")
            lines.append("Parabéns por identificar esta oportunidade!")
        else:
            lines.append("Setup válido mas aguarde melhor entrada.")
    else:
        if score >= 75:
            lines.append("Este ativo está mostrando fraqueza relativa.")
            lines.append("Cuidado com shorts - gerencie risco!")
        else:
            lines.append("Setup de curto - não force.")
    
    lines.append("")
    lines.append("_Use /ai para perguntar sobre este setup_")
    
    return "\n".join(lines)


def format_important_news_alert(title: str, sentiment: str, impact: str,
                               trading_advice: str = None) -> str:
    """Alerta de notícia com impacto no trading."""
    
    emoji = "🟢" if sentiment == "positive" else ("🔴" if sentiment == "negative" else "⚪")
    impact_emoji = "🔴" if impact == "HIGH" else ("🟡" if impact == "MEDIUM" else "⚪")
    
    lines = [
        f"{emoji} *NOTÍCIA RELEVANTE* {impact_emoji}",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"_{title[:200]}_",
        "",
    ]
    
    if trading_advice:
        lines.append("*🎯 IMPACTO NO TRADING:*")
        lines.append(trading_advice)
    else:
        if sentiment == "negative":
            lines.append("*🎯 IMPACTO:*")
            lines.append("Notícia negativa pode pressionar preços.")
            lines.append("Considere proteção adicional em posições.")
        elif sentiment == "positive":
            lines.append("*🎯 IMPACTO:*")
            lines.append("Notícia positiva pode impulsionar alta.")
            lines.append("Fique atento a entradas no viés comprador.")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    
    return "\n".join(lines)


def format_trade_management_alert(symbol: str, direction: str, 
                                  entry_price: float, current_price: float,
                                  trade_hours: float, advice: str) -> str:
    """Alerta de gestão de trade ativo."""
    
    emoji = "📈" if direction == "LONG" else "📉"
    
    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
    
    lines = [
        f"📋 *GESTÃO DE TRADE*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"{emoji} *{symbol}/USDT* - {direction}",
        "",
        f"*Entrada:* ${entry_price:,.2f}",
        f"*Atual:* ${current_price:,.2f}",
        f"*P&L:* {pnl_emoji} {pnl_pct:+.2f}%",
        f"*Tempo:* {trade_hours:.1f}h",
        "",
    ]
    
    lines.append("*🎯 MINHA ANÁLISE:*")
    lines.append(advice)
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    
    return "\n".join(lines)


def format_psychology_reminder(context: str = None) -> str:
    """Lembrete de psicologia de trading."""
    
    reminders = [
        "*🧠 LEMBRETE:*\n\nO mercado vai fazer tudo para tirar você de sua posição. Mantenha a calma e siga seu plano.",

        "*🧠 CONTROLE EMOCIONAL:*\n\nNão deixe o medo ou ganância guiar suas decisões. Trading é gerenciamento de probabilidade.",

        "*🧠 DISCIPLINA:*\n\nVocê não precisa operar todos os dias. As melhores oportunidades aparecem - esteja lá quando aparecerem.",

        "*🧠 PERSPECTIVA:*\n\nUma perda não define sua carreira como trader. O que importa é Consistency + Risk Management.",

        "*🧠 PACIÊNCIA:*\n\nOs maiores lucros vêm de esperar a configuração perfeita. Não force trades.",
    ]
    
    return random.choice(reminders)


def format_exit_signal(symbol: str, direction: str, reason: str,
                      pnl_if_closed: float = None) -> str:
    """Signal de saída com explicação."""
    
    lines = [
        "🚨 *SINAL DE SAÍDA DETECTADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*{symbol}/USDT* - {direction}",
        "",
        "*⚠️ MOTIVO:*",
        reason,
        "",
    ]
    
    if pnl_if_closed is not None:
        emoji = "🟢" if pnl_if_closed >= 0 else "🔴"
        lines.append(f"*P&L se sair agora:* {emoji} {pnl_if_closed:+.2f}%")
        lines.append("")
    
    lines.append("*🎯 MINHA RECOMENDAÇÃO:*")
    
    if "regime" in reason.lower() or "mudou" in reason.lower():
        lines.append("O regime mudou contra sua posição.")
        lines.append("Considere realizar ou ajustar stop.")
        lines.append("Não insista contra a tendência.")
    elif "funding" in reason.lower() or "squeeze" in reason.lower():
        lines.append("Funding extremo detectado.")
        lines.append("Risco de liquidação de posições.")
        lines.append("Proteja seu capital.")
    else:
        lines.append("Condições originais do setup mudaram.")
        lines.append("守る (shinu) - know when to fold.")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    
    return "\n".join(lines)


_analyst: Optional[TradingAnalyst] = None


def get_analyst() -> TradingAnalyst:
    global _analyst
    if _analyst is None:
        _analyst = TradingAnalyst()
    return _analyst
