"""
analyst.py — Jarvis como Analista Profissional de Trading

Princípios:
1. PREVISIVO - Antecipa movimentos antes que aconteçam
2. CAUTELOSO - Sempre mostra os dois lados do mercado
3. DIDÁTICO - Ensina o raciocínio por trás de cada análise
4. ASSERTIVO - Diz claramente o que fazer, sem enrolação

O Jarvis não é só um bot de sinais - é um MENTOR que te ensina
a pensar como um trader profissional.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
import random

from logger import get_logger
from notifier import _translate_news_title, _translate_macro_title

log = get_logger("analyst")

REGIME_CHANGE_COOLDOWN = 300
NEWS_ALERT_COOLDOWN = 600
MARKET_PULSE_INTERVAL = 900


@dataclass
class TradeSetup:
    """Setup de trade com análise completa."""
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
    warnings: List[str] = field(default_factory=list)


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
    prediction: str
    prediction_confidence: float
    risk_factors: List[str]
    opportunities: List[str]


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
    market_predictions: List[str] = field(default_factory=list)


class TradingAnalyst:
    """
    Jarvis como Analista Profissional.
    
    CARACTERÍSTICAS:
    - PREVISIVO: Antecipa o que vai acontecer
    - CAUTELOSO: Sempre mostra riscos
    - DIDÁTICO: Explica o porquê de tudo
    - ASSERTIVO: Diz exatamente o que fazer
    """
    
    def __init__(self):
        self._state = AnalystState()
        self._market_wisdom = self._load_market_wisdom()
        self._trading_rules = self._load_trading_rules()
    
    def _now(self) -> float:
        return time.time()
    
    def _load_market_wisdom(self) -> List[Dict]:
        """Sabedoria de mercado para ensinar."""
        return [
            {
                "topic": "tendência",
                "lesson": "A tendência é sua amiga. Nunca lucre contra a tendência principal.",
                "action": "Identifique a tendência primeiro, depois procure entradas a favor."
            },
            {
                "topic": "suporte_resistência",
                "lesson": "Suporte é onde compradores entram. Resistência é onde vendedores entram.",
                "action": "Em tendências de alta,-buy em suportes. Em baixas, venden em resistências."
            },
            {
                "topic": "funding",
                "lesson": "Funding alto = muitos longos no mercado = risco de squeeze.",
                "action": "Quando funding > 0.01%, cuidado com posições longas."
            },
            {
                "topic": "volatilidade",
                "lesson": "Alta volatilidade = oportunidades E perigos.",
                "action": "Reduza tamanho em dias de alta volatilidade."
            },
            {
                "topic": "notícias",
                "lesson": "Não negocie notícias - espere o mercado digerir.",
                "action": "Após notícias grandes, espere 1-2h para entrar."
            },
            {
                "topic": "regime",
                "lesson": "Cada regime pede estratégia diferente.",
                "action": "Tendência = seguir. Lateral = range. Indeciso = esperar."
            },
        ]
    
    def _load_trading_rules(self) -> List[str]:
        """Regras inegociáveis de trading."""
        return [
            "1. Nunca arrisque mais de 2% do capital em um trade",
            "2. Stop loss é sagrado - SEMPRE defina antes de entrar",
            "3. Se o setup não está claro, NÃO ENTRE",
            "4. Deixe lucros correrem, corte perdas rápido",
            "5. Não adicione a posições perdedoras",
            "6. A tendência é maior que qualquer indicador",
            "7. Funding alto = perigo para longos",
            "8. Não persiga perdas - aceite e siga em frente",
        ]
    
    def set_current_position(self, symbol: str, direction: str, entry_price: float, 
                           entry_time: float = None, stop_loss: float = None,
                           take_profit: float = None) -> None:
        """Registra posição para acompanhamento."""
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
    
    def _is_high_impact_news(self, title: str) -> bool:
        """Notícias que mudam o jogo."""
        title_lower = title.lower()
        keywords = [
            "fed", "federal reserve", "rate hike", "rate cut",
            "sec", "etf approval", "etf rejection",
            "hack", "exploit", "ban", "regulation",
            "crash", "black swan", "bailout",
            "institutional", "blackrock", "massive",
        ]
        return any(kw in title_lower for kw in keywords)
    
    def analyze_market(self, btc_price: float, btc_closes: List[float],
                      regime_result, macro_snap = None) -> MarketContext:
        """Análise completa do mercado com PREVISÃO."""
        
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
        momentum = "LATERAL"
        
        if change_1h > 2:
            sentiment = "BULLISH"
            momentum = "SUBIDA"
        elif change_1h < -2:
            sentiment = "BEARISH"
            momentum = "QUEDA"
        
        if change_4h > 5:
            momentum = "FORTE ALTA"
        elif change_4h < -5:
            momentum = "FORTE QUEDA"
        
        risk_level = "BAIXO"
        risk_factors = []
        opportunities = []
        
        if macro_snap:
            risk_score = getattr(macro_snap, "risk_score", 50)
            if risk_score >= 75:
                risk_level = "ALTO"
                risk_factors.append("Risco macro elevado - cautela redobrada")
            elif risk_score >= 60:
                risk_level = "MODERADO"
                risk_factors.append("Risco moderado - gestão de risco essencial")
        
        if abs(change_1h) > 3:
            risk_factors.append("Movimento brusco - pode reverter ou continuar")
            opportunities.append("Aguardar confirmação após movimento")
        
        if regime_adx > 25:
            opportunities.append("Tendência definida - procurar entradas a favor")
        elif regime_adx < 20:
            risk_factors.append("Mercado sem direção - não force trades")
        
        prediction, confidence = self._make_prediction(
            change_1h, change_4h, change_24h, regime_str, regime_adx
        )
        
        key_levels = self._calculate_key_levels(btc_price, btc_closes)
        
        macro_events = []
        if macro_snap:
            for event in getattr(macro_snap, "events", [])[:3]:
                title = getattr(event, "title", "")
                if title:
                    macro_events.append(title)
        
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
            prediction=prediction,
            prediction_confidence=confidence,
            risk_factors=risk_factors,
            opportunities=opportunities,
        )
    
    def _make_prediction(self, change_1h: float, change_4h: float, 
                       change_24h: float, regime: str, adx: float) -> tuple:
        """Faz previsão baseada em múltiplos fatores."""
        
        predictions = []
        confidence = 0.5
        
        if regime == "TRENDING" and adx > 30:
            if change_1h > 0:
                predictions.append("Tendência de alta pode continuar")
                confidence = 0.7
            else:
                predictions.append("Possível pullback dentro da tendência de alta")
                confidence = 0.6
        
        elif regime == "RANGING":
            predictions.append("Mercado lateral - sem direção clara")
            predictions.append("Procurar setups em extremos do range")
            confidence = 0.5
        
        elif regime == "WEAK":
            predictions.append("Mercado indeciso - AGUARDAR")
            predictions.append("Não force entradas sem confirmação")
            confidence = 0.4
        
        if abs(change_1h) > 3:
            predictions.append("Movimento brusco detectado - cautela")
            confidence = min(confidence, 0.5)
        
        if change_24h > 10:
            predictions.append("BTC subiu muito em 24h - risco de correção")
            predictions.append("Não compre em alta extrema")
        elif change_24h < -10:
            predictions.append("BTC caiu muito em 24h - pode ter fundo")
            predictions.append("Aguarde confirmação antes de comprar")
        
        return " | ".join(predictions[:2]), confidence
    
    def _calculate_key_levels(self, price: float, closes: List[float]) -> Dict[str, float]:
        """Calcula níveis importantes."""
        if not closes:
            return {"current": price}
        
        highs = max(closes[-20:]) if len(closes) >= 20 else max(closes)
        lows = min(closes[-20:]) if len(closes) >= 20 else min(closes)
        avg = sum(closes[-20:]) / min(len(closes), 20)
        
        return {
            "resistance_1": highs,
            "support_1": lows,
            "current": price,
            "average_20": avg,
            "range_high": highs,
            "range_low": lows,
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
        """Gera setup CAUTELOSO com todos os riscos."""
        
        warnings = []
        
        if direction == "LONG":
            stop_pct = max(volatility * 2, 0.015)
            stop_price = current_price * (1 - stop_pct)
            tp1_price = current_price * (1 + stop_pct)
            tp2_price = current_price * (1 + stop_pct * 2.5)
            entry_zone = f"${current_price:,.2f} - ${current_price * 1.003:,.2f}"
        else:
            stop_pct = max(volatility * 2, 0.015)
            stop_price = current_price * (1 + stop_pct)
            tp1_price = current_price * (1 - stop_pct)
            tp2_price = current_price * (1 - stop_pct * 2.5)
            entry_zone = f"${current_price:,.2f} - ${current_price * 0.997:,.2f}"
        
        if volatility > 0.03:
            warnings.append("[WARN] Volatilidade ELEVADA - reduza tamanho da posição")
        
        reasoning = []
        if score >= 80:
            reasoning.append(" Setup de ALTA qualidade - edge confirmado")
            reasoning.append(" Condições idéais para entrada")
        elif score >= 65:
            reasoning.append(" Setup válido - possui edge")
        else:
            reasoning.append(" Setup moderado - aguarde melhor preço")
            warnings.append("[WARN] Score moderado - não force entrada")
        
        reasoning.append(f" Stop em {stop_pct*100:.1f}% - risco controlado")
        
        return TradeSetup(
            symbol=symbol,
            direction=direction,
            score=score,
            entry_zone=entry_zone,
            stop_loss=f"${stop_price:,.2f} ({stop_pct*100:.1f}%)",
            take_profit_1=f"${tp1_price:,.2f}",
            take_profit_2=f"${tp2_price:,.2f}",
            position_size_advice="Máx 2% do capital",
            reasoning=reasoning,
            risk_reward="1:2.5",
            warnings=warnings,
        )


# ============================================================================
# FORMATAÇÃO DE MENSAGENS - ESTILO ANALISTA PROFISSIONAL
# ============================================================================

def format_daily_briefing(context: MarketContext, top_signals: List = None) -> str:
    """RELATÓRIO COMPLETO - Como um analista profissional."""
    
    emoji = "[GREEN]" if context.sentiment == "BULLISH" else ("[RED]" if context.sentiment == "BEARISH" else "[NEUTRAL]")
    
    lines = [
        "[STAT] *RELATÓRIO DIÁRIO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m/%Y às %H:%M UTC')}_",
        "",
        "" * 32,
        "",
        f"*PREÇO ATUAL:* ${context.btc_price:,.2f}",
        "",
    ]
    
    lines.append("*VARIAÇÕES:*")
    lines.append(f"  1 hora:  {context.btc_change_1h:+.2f}%")
    lines.append(f"  4 horas: {context.btc_change_4h:+.2f}%")
    lines.append(f"  24 horas: {context.btc_change_24h:+.2f}%")
    lines.append("")
    
    regime_emoji = "[UP]" if context.regime == "TRENDING" else ("↔" if context.regime == "RANGING" else "")
    regime_pt = {"TRENDING": "TENDÊNCIA", "RANGING": "LATERAL", "WEAK": "INDECISÃO"}.get(context.regime, "???")
    
    lines.append("" * 32)
    lines.append("")
    lines.append(f"*{regime_emoji} SITUAÇÃO TÉCNICA*")
    lines.append(f"  Regime: {regime_pt}")
    lines.append(f"  Direção: {context.regime_direction}")
    lines.append(f"  Força (ADX): {context.regime_strength:.0f}/100")
    lines.append("")
    
    if context.key_levels:
        lines.append("* NÍVEIS IMPORTANTES:*")
        lines.append(f"  Resistência: ${context.key_levels.get('resistance_1', 0):,.2f}")
        lines.append(f"  Suporte: ${context.key_levels.get('support_1', 0):,.2f}")
        lines.append(f"  Média 20: ${context.key_levels.get('average_20', 0):,.2f}")
        lines.append("")
    
    lines.append("" * 32)
    lines.append("")
    lines.append("* MINHA PREVISÃO:*")
    lines.append(f"  {context.prediction}")
    lines.append(f"  Confiança: {context.prediction_confidence*100:.0f}%")
    lines.append("")
    
    if context.risk_factors:
        lines.append("*[WARN] FATORES DE RISCO:*")
        for rf in context.risk_factors:
            lines.append(f"  • {rf}")
        lines.append("")
    
    if context.opportunities:
        lines.append("*[TARGET] OPORTUNIDADES:*")
        for op in context.opportunities:
            lines.append(f"  • {op}")
        lines.append("")
    
    if context.macro_events:
        lines.append("" * 32)
        lines.append("")
        lines.append("*[WORLD] EVENTOS MACRO:*")
        for event in context.macro_events[:2]:
            if event:
                lines.append(f"  • {event[:60]}...")
        lines.append("")
    
    if top_signals:
        lines.append("" * 32)
        lines.append("")
        lines.append("*[UP] SETUPS IDENTIFICADOS:*")
        for sig in top_signals[:3]:
            emoji_s = "[UP]" if sig.direction == "LONG" else "[DOWN]"
            quality = "[HOT]" if sig.score >= 75 else ("[OK]" if sig.score >= 60 else "[WARN]")
            lines.append(f"{emoji_s} {sig.symbol}/USDT {sig.direction} {quality} {sig.score:.0f}")
        lines.append("")
    
    lines.append("" * 32)
    lines.append("")
    lines.append("*[IDEA] REGRA DE OURO:*")
    lines.append("  " + random.choice([
        "Se não tem certeza, NÃO ENTRE.",
        "Paciência é virtue. Aguarde o setup perfeito.",
        "Proteja o capital. Lucros vêm depois.",
        "A tendência é maior que qualquer indicador.",
        "Não persiga perdas. Aceitar é trading.",
    ]))
    lines.append("")
    lines.append("_Jarvis - Seu Analista de Trading_")
    
    return "\n".join(lines)


def format_market_pulse(context: MarketContext, signal_alert: str = None) -> str:
    """Pulso rápido - atualização periódica."""
    
    emoji = "[GREEN]" if context.sentiment == "BULLISH" else ("[RED]" if context.sentiment == "BEARISH" else "[NEUTRAL]")
    regime_emoji = "[UP]" if context.regime == "TRENDING" else ("↔" if context.regime == "RANGING" else "")
    
    lines = [
        f"[STAT] *PULSO* {emoji}",
        f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_",
        "",
        f"BTC: ${context.btc_price:,.2f}",
        f"1h: {context.btc_change_1h:+.1f}% | {regime_emoji} {context.regime}",
        f"ADX: {context.regime_strength:.0f} | Risco: {context.risk_level}",
    ]
    
    if context.prediction:
        lines.append("")
        lines.append(f" {context.prediction[:60]}")
    
    if signal_alert:
        lines.append("")
        lines.append(f" {signal_alert}")
    
    lines.append("")
    lines.append("_Jarvis_")
    
    return "\n".join(lines)


def format_regime_change_alert(previous: str, new: str, direction: str, 
                               strength: float, advice: str = None) -> str:
    """ ALERTA: Mudança de regime - MUITO IMPORTANTE."""
    
    new_pt = {"TRENDING": "[UP] TENDÊNCIA", "RANGING": "↔ LATERAL", "WEAK": " INDECISÃO"}.get(new, new)
    previous_pt = {"TRENDING": "tendência", "RANGING": "lateral", "WEAK": "indecisão"}.get(previous, previous)
    
    lines = [
        " *ALERTA: MERCADO MUDOU!*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"O mercado saiu de *{previous_pt}* para *{new_pt}*",
        f"Direção: *{direction}* | Força: *{strength:.0f}*",
        "",
    ]
    
    if new == "TRENDING":
        lines.append("*[UP] MINHA ANÁLISE:*")
        lines.append("Tendência definida = maior probabilidade.")
        lines.append("Procure entradas a FAVOR da tendência.")
        lines.append("Stops mais apertados podem funcionar.")
    elif new == "RANGING":
        lines.append("*↔ MINHA ANÁLISE:*")
        lines.append("Mercado sem direção clara.")
        lines.append("OPERE NOS EXTREMOS do range.")
        lines.append("Reduza tamanho das posições.")
    else:
        lines.append("* MINHA ANÁLISE:*")
        lines.append("Mercado indeciso = AGUARDAR.")
        lines.append("NÃO FORCE entradas agora.")
        lines.append("Paciência. O mercado vai dar sinal.")
    
    if advice:
        lines.append("")
        lines.append(f"*[TARGET] AÇÃO:* {advice}")
    
    lines.append("")
    lines.append("_Jarvis - Seu Analista_")
    
    return "\n".join(lines)


def format_signal_with_education(symbol: str, direction: str, score: float,
                                setup: TradeSetup, lesson: str = None) -> str:
    """Signal completo COM EDUCAÇÃO - estilo professor."""
    
    emoji = "[UP]" if direction == "LONG" else "[DOWN]"
    quality = "[HOT] QUALIDADE ALTA" if score >= 75 else ("[OK] BOA QUALIDADE" if score >= 60 else "[WARN] MODERADA")
    
    lines = [
        f"{emoji} *ANÁLISE: {symbol}/USDT*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*Direção:* {direction}",
        f"*Score:* {score:.0f}/100 ({quality})",
        "",
        "" * 28,
        "",
        "* PLANO DE TRADE:*",
        "",
        f"   Entrada: {setup.entry_zone}",
        f"  [KILL] Stop Loss: {setup.stop_loss}",
        f"  [TARGET] TP1: {setup.take_profit_1}",
        f"  [TARGET] TP2: {setup.take_profit_2}",
        f"   R/R: {setup.risk_reward}",
        f"  [MONEY] Size: {setup.position_size_advice}",
        "",
    ]
    
    if setup.warnings:
        lines.append("*[WARN] CUIDADO:*")
        for w in setup.warnings:
            lines.append(f"  {w}")
        lines.append("")
    
    lines.append("*[OK] PORQUÊ DESTE SETUP:*")
    for r in setup.reasoning:
        lines.append(f"  {r}")
    lines.append("")
    
    if lesson:
        lines.append("" * 28)
        lines.append("")
        lines.append(f"* LIÇÃO:* {lesson}")
    
    lines.append("")
    lines.append("_Jarvis - Seu Analista_")
    
    return "\n".join(lines)


def format_important_news_alert(title: str, sentiment: str, 
                               trading_advice: str = None) -> str:
    """Notícia importante com IMPACTO no trading."""
    
    emoji = "[GREEN]" if sentiment == "positive" else ("[RED]" if sentiment == "negative" else "[NEUTRAL]")
    translated_title = _translate_news_title(title)
    
    lines = [
        f"{emoji} *NOTÍCIA COM IMPACTO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"_{translated_title[:180]}_",
        "",
    ]
    
    if trading_advice:
        lines.append("*[TARGET] O QUE FAZER:*")
        lines.append(trading_advice)
    else:
        if sentiment == "negative":
            lines.append("*[TARGET] O QUE FAZER:*")
            lines.append("Notícia negativa = cautela.")
            lines.append("Proteja posições longas.")
            lines.append("Não compre agora.")
        elif sentiment == "positive":
            lines.append("*[TARGET] O QUE FAZER:*")
            lines.append("Notícia positiva = possível alta.")
            lines.append("Fique atento a entradas.")
        else:
            lines.append("*[TARGET] O QUE FAZER:*")
            lines.append("Aguarde o mercado digerir.")
            lines.append("Não entre imediatamente.")
    
    lines.append("")
    lines.append("_Jarvis - Seu Analista_")
    
    return "\n".join(lines)


def format_trade_management_alert(symbol: str, direction: str, 
                                  entry_price: float, current_price: float,
                                  trade_hours: float, advice: str) -> str:
    """Alerta de gestão - COMO UM MENTOR."""
    
    emoji = "[UP]" if direction == "LONG" else "[DOWN]"
    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    pnl_emoji = "[GREEN]" if pnl_pct >= 0 else "[RED]"
    
    lines = [
        f"[LIST] *GESTÃO: {symbol}*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"{emoji} {direction} | {trade_hours:.1f}h no trade",
        "",
        f"  Entrada: ${entry_price:,.2f}",
        f"  Atual:   ${current_price:,.2f}",
        f"  P&L:     {pnl_emoji} {pnl_pct:+.2f}%",
        "",
    ]
    
    lines.append("*[TARGET] MINHA ANÁLISE:*")
    lines.append(advice)
    
    lines.append("")
    lines.append("_Jarvis - Seu Analista_")
    
    return "\n".join(lines)


def format_exit_signal(symbol: str, direction: str, reason: str,
                      pnl_if_closed: float = None) -> str:
    """Sinal de saída - DIRETO E ASSERTIVO."""
    
    lines = [
        " *SAIA DESTE TRADE!*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*{symbol} - {direction}*",
        "",
    ]
    
    if pnl_if_closed is not None:
        emoji = "[GREEN]" if pnl_if_closed >= 0 else "[RED]"
        lines.append(f"P&L se sair: {emoji} {pnl_if_closed:+.2f}%")
        lines.append("")
    
    lines.append("*[WARN] MOTIVO:*")
    lines.append(reason)
    lines.append("")
    
    lines.append("*[TARGET] AÇÃO AGORA:*")
    if "regime" in reason.lower():
        lines.append("1. Pare de adicionar posição")
        lines.append("2. Ajuste stop para breakeven")
        lines.append("3. Considere realizar parcialmente")
    elif "funding" in reason.lower():
        lines.append("1. Saia de posições longas AGORA")
        lines.append("2. Risco de long squeeze é alto")
        lines.append("3. Aguarde funding normalizar")
    else:
        lines.append("1. Reveja seu plano")
        lines.append("2. Stop loss deve ser respeitado")
        lines.append("3. Não insista no erro")
    
    lines.append("")
    lines.append("_Jarvis - Seu Analista_")
    
    return "\n".join(lines)


def format_caution_alert(reason: str, advice: str) -> str:
    """Alerta de cautela - MUITO IMPORTANTE."""
    
    lines = [
        "[WARN] *ATENÇÃO!*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*MOTIVO:* {reason}",
        "",
        "*[TARGET] O QUE FAZER:*",
        advice,
        "",
        "_Jarvis - Seu Analista_",
    ]
    
    return "\n".join(lines)


def format_learning_moment(topic: str, lesson: str, action: str) -> str:
    """Momento de aprendizado - DIDÁTICO."""
    
    lines = [
        " *MOMENTO DE APRENDIZADO*",
        f"_{datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}_",
        "",
        f"*TÓPICO:* {topic.upper()}",
        "",
        f"*[IDEA] LIÇÃO:*",
        lesson,
        "",
        f"*[TARGET] NA PRÁTICA:*",
        action,
        "",
        "_Jarvis - Seu Analista_",
    ]
    
    return "\n".join(lines)


_analyst: Optional[TradingAnalyst] = None


def get_analyst() -> TradingAnalyst:
    global _analyst
    if _analyst is None:
        _analyst = TradingAnalyst()
    return _analyst
