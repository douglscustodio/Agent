"""
score_system.py — Sistema de Pontuação do Jarvis

Sistema de pontuação baseado em múltiplos fatores:
- Score final de 0-100
- Classificação: Sem sinal / Fraco / Moderado / Forte
- Decisão baseada no score

LÓGICA:
score = 0

# Tendência (máx 30 pts)
if trend_bull: score += 30
elif trend_neutral: score += 15
else: score -= 10

# Força (máx 20 pts)
if adx > 25: score += 20
elif adx > 15: score += 10

# Volume (máx 15 pts)
if volume_up: score += 15
elif volume_normal: score += 8

# Momentum (máx 20 pts)
if momentum_strong: score += 20
elif momentum_weak: score += 10

# Funding/Squeeze (máx 15 pts)
if funding_dangerous: score -= 15
elif funding_ok: score += 10

# DECISÃO FINAL:
# 0-30: WAIT - Sem sinal
# 31-50: WATCH - Sinal fraco, monitorar
# 51-70: CONSIDER - Sinal moderado, considerar
# 71-100: ACT - Sinal forte, agir
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

from logger import get_logger

log = get_logger("score")


class SignalStrength(Enum):
    WAIT = "WAIT"           # 0-30 - Aguardar
    WATCH = "WATCH"         # 31-50 - Monitorar
    CONSIDER = "CONSIDER"   # 51-70 - Considerar
    ACT = "ACT"             # 71-100 - Agir


@dataclass
class ScoreBreakdown:
    """Detalhamento do score por categoria."""
    trend: float = 0.0
    strength: float = 0.0
    volume: float = 0.0
    momentum: float = 0.0
    funding: float = 0.0
    regime: float = 0.0
    total: float = 0.0
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "trend": self.trend,
            "strength": self.strength,
            "volume": self.volume,
            "momentum": self.momentum,
            "funding": self.funding,
            "regime": self.regime,
            "total": self.total,
        }


@dataclass
class SignalDecision:
    """Decisão final baseada no score."""
    symbol: str
    direction: str  # LONG ou SHORT
    score: float
    strength: SignalStrength
    breakdown: ScoreBreakdown
    reasons: List[str]
    warnings: List[str]
    action: str
    confidence: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "score": self.score,
            "strength": self.strength.value,
            "breakdown": self.breakdown.to_dict(),
            "reasons": self.reasons,
            "warnings": self.warnings,
            "action": self.action,
            "confidence": self.confidence,
        }


class ScoreSystem:
    """
    Sistema de pontuação multi-fator.
    
    Calcula score de 0-100 baseado em:
    1. Tendência (trend)
    2. Força (strength/ADX)
    3. Volume
    4. Momentum
    5. Funding
    6. Regime
    """
    
    # Limites de cada categoria
    MAX_TREND = 30
    MAX_STRENGTH = 20
    MAX_VOLUME = 15
    MAX_MOMENTUM = 20
    MAX_FUNDING = 15
    MAX_REGIME = 0  # Regime já é levado em conta na decisão
    
    # Limiar de decisão
    THRESHOLD_WAIT = 30
    THRESHOLD_WATCH = 50
    THRESHOLD_CONSIDER = 70
    
    def __init__(self):
        self._last_scores: Dict[str, float] = {}
    
    def calculate(
        self,
        symbol: str,
        direction: str,
        trend_score: float,      # -30 a +30 (negativo = baixa)
        adx: float,             # 0-100
        volume_ratio: float,     # 0-2 (1 = normal)
        momentum: float,         # -100 a +100
        funding_rate: float,     # -0.1 a +0.1 (decimal)
        regime: str,             # TRENDING, RANGING, WEAK
        btc_change: float = 0,  # -100 a +100
    ) -> SignalDecision:
        """
        Calcula score completo para um símbolo.
        
        Returns:
            SignalDecision com score e decisão
        """
        breakdown = ScoreBreakdown()
        reasons = []
        warnings = []
        
        # 1. TREND (máx 30 pts)
        # Direção alinhada com tendencia do BTC
        if direction == "LONG":
            if trend_score > 10:
                breakdown.trend = self.MAX_TREND
                reasons.append(f"Tendencia bullish confirmada (+{trend_score:.0f})")
            elif trend_score > 0:
                breakdown.trend = self.MAX_TREND * 0.6
                reasons.append(f"Tendencia positiva ({trend_score:.0f})")
            elif trend_score > -10:
                breakdown.trend = self.MAX_TREND * 0.3
                warnings.append("Tendencia incerta")
            else:
                breakdown.trend = 0
                warnings.append("Tendencia bearish - cuidado com LONG")
        else:  # SHORT
            if trend_score < -10:
                breakdown.trend = self.MAX_TREND
                reasons.append(f"Tendencia bearish confirmada ({trend_score:.0f})")
            elif trend_score < 0:
                breakdown.trend = self.MAX_TREND * 0.6
                reasons.append(f"Tendencia negativa ({trend_score:.0f})")
            else:
                breakdown.trend = self.MAX_TREND * 0.3
                warnings.append("Tendencia bullish - cuidado com SHORT")
        
        # 2. STRENGTH (máx 20 pts)
        # Baseado no ADX
        if adx > 30:
            breakdown.strength = self.MAX_STRENGTH
            reasons.append(f"Força forte (ADX={adx:.0f})")
        elif adx > 20:
            breakdown.strength = self.MAX_STRENGTH * 0.7
            reasons.append(f"Força moderada (ADX={adx:.0f})")
        elif adx > 10:
            breakdown.strength = self.MAX_STRENGTH * 0.4
            warnings.append(f"ADX fraco ({adx:.0f})")
        else:
            breakdown.strength = self.MAX_STRENGTH * 0.1
            warnings.append(f"ADX muito fraco ({adx:.0f}) - sem tendência")
        
        # 3. VOLUME (máx 15 pts)
        if volume_ratio > 1.5:
            breakdown.volume = self.MAX_VOLUME
            reasons.append(f"Volume elevado ({volume_ratio:.1f}x)")
        elif volume_ratio > 1.0:
            breakdown.volume = self.MAX_VOLUME * 0.7
        elif volume_ratio > 0.7:
            breakdown.volume = self.MAX_VOLUME * 0.4
            warnings.append("Volume abaixo da média")
        else:
            breakdown.volume = self.MAX_VOLUME * 0.1
            warnings.append("Volume muito baixo")
        
        # 4. MOMENTUM (máx 20 pts)
        if direction == "LONG":
            if momentum > 50:
                breakdown.momentum = self.MAX_MOMENTUM
                reasons.append(f"Momentum bullish forte ({momentum:.0f})")
            elif momentum > 20:
                breakdown.momentum = self.MAX_MOMENTUM * 0.7
                reasons.append(f"Momentum positivo ({momentum:.0f})")
            elif momentum > -20:
                breakdown.momentum = self.MAX_MOMENTUM * 0.4
            else:
                breakdown.momentum = self.MAX_MOMENTUM * 0.2
                warnings.append("Momentum bearish")
        else:  # SHORT
            if momentum < -50:
                breakdown.momentum = self.MAX_MOMENTUM
                reasons.append(f"Momentum bearish forte ({momentum:.0f})")
            elif momentum < -20:
                breakdown.momentum = self.MAX_MOMENTUM * 0.7
                reasons.append(f"Momentum negativo ({momentum:.0f})")
            elif momentum < 20:
                breakdown.momentum = self.MAX_MOMENTUM * 0.4
            else:
                breakdown.momentum = self.MAX_MOMENTUM * 0.2
                warnings.append("Momentum bullish - cuidado com SHORT")
        
        # 5. FUNDING (máx 15 pts)
        # Funding alto é ruim para LONG
        funding_pct = funding_rate * 100  # Converter para %
        
        if direction == "LONG":
            if funding_pct > 0.1:
                breakdown.funding = -self.MAX_FUNDING
                warnings.append(f"⚠️ FUNDING EXTREMO ({funding_pct:.2f}%) - risco long squeeze!")
            elif funding_pct > 0.03:
                breakdown.funding = -self.MAX_FUNDING * 0.5
                warnings.append(f"Funding alto ({funding_pct:.2f}%)")
            elif funding_pct > 0:
                breakdown.funding = self.MAX_FUNDING * 0.5
                reasons.append(f"Funding normal ({funding_pct:.2f}%)")
            else:
                breakdown.funding = self.MAX_FUNDING * 0.8
                reasons.append(f"Funding negativo ({funding_pct:.2f}%) - bom para LONG")
        else:  # SHORT
            if funding_pct < -0.1:
                breakdown.funding = -self.MAX_FUNDING
                warnings.append(f"⚠️ FUNDING EXTREMO ({funding_pct:.2f}%) - risco short squeeze!")
            elif funding_pct < -0.03:
                breakdown.funding = -self.MAX_FUNDING * 0.5
                warnings.append(f"Funding muito baixo ({funding_pct:.2f}%)")
            elif funding_pct < 0:
                breakdown.funding = self.MAX_FUNDING * 0.5
                reasons.append(f"Funding negativo ({funding_pct:.2f}%)")
            else:
                breakdown.funding = self.MAX_FUNDING * 0.8
                reasons.append(f"Funding positivo ({funding_pct:.2f}%) - bom para SHORT")
        
        # 6. REGIME (bônus/penalidade)
        if regime == "TRENDING":
            breakdown.regime = 5
            reasons.append("Regime de tendência - melhor probabilidade")
        elif regime == "RANGING":
            breakdown.regime = 0
            warnings.append("Regime lateral - reduza expectativa")
        else:
            breakdown.regime = -5
            warnings.append("Mercado sem direção")
        
        # CALCULAR TOTAL
        breakdown.total = (
            breakdown.trend +
            breakdown.strength +
            breakdown.volume +
            breakdown.momentum +
            breakdown.funding +
            breakdown.regime
        )
        
        # Limitar entre 0 e 100
        breakdown.total = max(0, min(100, breakdown.total))
        
        # DETERMINAR FORÇA
        if breakdown.total >= self.THRESHOLD_CONSIDER:
            strength = SignalStrength.ACT
        elif breakdown.total >= self.THRESHOLD_WATCH:
            strength = SignalStrength.CONSIDER
        elif breakdown.total >= self.THRESHOLD_WAIT:
            strength = SignalStrength.WATCH
        else:
            strength = SignalStrength.WAIT
        
        # DECIDIR AÇÃO
        action = self._get_action(strength, direction, warnings)
        confidence = self._get_confidence(breakdown.total, warnings)
        
        # Registrar para histórico
        self._last_scores[symbol] = breakdown.total
        
        # Log
        log.info(
            "SCORE_SYSTEM",
            f"{symbol} {direction}: score={breakdown.total:.0f} strength={strength.value} "
            f"action={action}",
            extra={"breakdown": breakdown.to_dict()}
        )
        
        return SignalDecision(
            symbol=symbol,
            direction=direction,
            score=breakdown.total,
            strength=strength,
            breakdown=breakdown,
            reasons=reasons,
            warnings=warnings,
            action=action,
            confidence=confidence,
        )
    
    def _get_action(self, strength: SignalStrength, direction: str, warnings: List[str]) -> str:
        """Determina ação baseada na força."""
        if strength == SignalStrength.WAIT:
            return "WAIT"
        
        if warnings and any("⚠️" in w for w in warnings):
            return "REDUCE_SIZE"
        
        if strength == SignalStrength.WATCH:
            return "WATCH_ONLY"
        
        if strength == SignalStrength.CONSIDER:
            if direction == "LONG":
                return "CONSIDER_LONG"
            else:
                return "CONSIDER_SHORT"
        
        # ACT
        if direction == "LONG":
            return "BUY"
        else:
            return "SELL"
    
    def _get_confidence(self, score: float, warnings: List[str]) -> float:
        """Calcula confiança baseada no score e warnings."""
        confidence = score / 100
        
        # Reduzir confiança por warnings
        warning_penalty = len(warnings) * 0.05
        confidence = max(0.1, confidence - warning_penalty)
        
        return round(confidence, 2)
    
    def get_score_color(self, score: float) -> str:
        """Retorna emoji baseado no score."""
        if score >= 75:
            return "🟢"
        elif score >= 50:
            return "🟡"
        elif score >= 30:
            return "⚠️"
        else:
            return "🔴"


def format_signal_message(decision: SignalDecision) -> str:
    """Formata mensagem do sinal para display."""
    color = ScoreSystem().get_score_color(decision.score)
    
    emoji_dir = "📈" if decision.direction == "LONG" else "📉"
    
    lines = [
        f"{emoji_dir} *{decision.symbol}/USDT*",
        "",
        f"Score: {color} *{decision.score:.0f}*/100",
        f"Força: {decision.strength.value}",
        f"Ação: *{decision.action}*",
        f"Confiança: {decision.confidence*100:.0f}%",
        "",
    ]
    
    if decision.reasons:
        lines.append("*✅ Motivos:*")
        for r in decision.reasons[:3]:
            lines.append(f"  • {r}")
        lines.append("")
    
    if decision.warnings:
        lines.append("*⚠️ Cuidados:*")
        for w in decision.warnings[:3]:
            lines.append(f"  • {w}")
        lines.append("")
    
    return "\n".join(lines)


# Singleton
_score_system: Optional[ScoreSystem] = None


def get_score_system() -> ScoreSystem:
    global _score_system
    if _score_system is None:
        _score_system = ScoreSystem()
    return _score_system
