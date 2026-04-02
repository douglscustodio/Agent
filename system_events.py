"""
squeeze_detector.py — Liquidation Squeeze and Crowded Trade Detector

Detecta setups perigosos onde:
1. Funding extremamente alto (long squeeze esperado)
2. Open Interest em spike (movimento iminente)
3. Preço perto de máxima histórica (liquidação de longa)
4. Correlação com posições populares

Isso evita entrar em trades que parecem bons mas são armadilhas.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from logger import get_logger

log = get_logger("squeeze")

FUNDING_DANGER_HIGH = 0.01
FUNDING_WARNING = 0.003
OI_SPIKE_THRESHOLD = 1.5
PRICE_NEAR_ATH_THRESHOLD = 0.95


@dataclass
class SqueezeResult:
    is_squeeze: bool
    is_crowded: bool
    danger_level: str
    reasons: list
    recommendation: str


def detect_squeeze(
    funding_rate: Optional[float],
    oi_change_pct: Optional[float],
    current_price: float,
    ath_price: float,
    position_direction: str,
) -> SqueezeResult:
    """
    Analisa se o trade está em squeeze/crowded territory.
    
    Args:
        funding_rate: Taxa de funding atual (ex: 0.001 = 0.1%)
        oi_change_pct: Mudança % no Open Interest
        current_price: Preço atual do ativo
        ath_price: Máxima história do ativo
        position_direction: "LONG" ou "SHORT"
    
    Returns:
        SqueezeResult com análise completa
    """
    reasons = []
    danger_level = "LOW"
    
    if funding_rate is None or oi_change_pct is None:
        return SqueezeResult(
            is_squeeze=False,
            is_crowded=False,
            danger_level="UNKNOWN",
            reasons=["Dados de funding/OI não disponíveis"],
            recommendation="CUIDADO - dados incompletos",
        )
    
    if funding_rate >= FUNDING_DANGER_HIGH:
        reasons.append(f"Funding EXTREMO: {funding_rate*100:.2f}% (armadilha de longa)")
        danger_level = "HIGH"
    elif funding_rate >= FUNDING_WARNING:
        reasons.append(f"Funding ELEVADO: {funding_rate*100:.2f}%")
        if danger_level != "HIGH":
            danger_level = "MEDIUM"
    
    if oi_change_pct >= OI_SPIKE_THRESHOLD * 100:
        reasons.append(f"OI em SPIKE: +{oi_change_pct:.0f}% (movimento iminente)")
        if danger_level == "LOW":
            danger_level = "MEDIUM"
    
    if current_price > 0 and ath_price > 0:
        ath_ratio = current_price / ath_price
        if ath_ratio >= PRICE_NEAR_ATH_THRESHOLD:
            reasons.append(f"Preço perto da ATH: {ath_ratio:.1%} (risco de liquidação)")
            if danger_level == "LOW":
                danger_level = "MEDIUM"
    
    is_squeeze = danger_level in ("HIGH", "MEDIUM") and funding_rate > 0 and position_direction == "LONG"
    is_crowded = len(reasons) >= 2
    
    if is_squeeze:
        recommendation = "EVITAR LONG - funding alto = squeeze iminente"
    elif is_crowded:
        recommendation = "CUIDADO - múltiplos fatores de risco"
    elif danger_level == "MEDIUM":
        recommendation = "Entrada possível mas com stop apertado"
    else:
        recommendation = "Setup limpo"
    
    log.info(
        "SQUEEZE_DETECTOR",
        f"funding={funding_rate*100:.3f}% oi={oi_change_pct:+.0f}% "
        f"danger={danger_level} squeeze={is_squeeze} crowded={is_crowded}"
    )
    
    return SqueezeResult(
        is_squeeze=is_squeeze,
        is_crowded=is_crowded,
        danger_level=danger_level,
        reasons=reasons,
        recommendation=recommendation,
    )


def score_adjustment_for_squeeze(squeeze: SqueezeResult, base_score: float) -> float:
    """
    Ajusta score do sinal baseado no squeeze.
    
    Returns:
        Score ajustado (pode ser menor ou maior dependendo do setup)
    """
    if squeeze.is_squeeze:
        return base_score - 10  # Penalidade fixa de 10 pontos
    
    if squeeze.is_crowded:
        return base_score - 5   # Penalidade fixa de 5 pontos
    
    if squeeze.danger_level == "MEDIUM":
        return base_score - 3   # Penalidade fixa de 3 pontos
    
    return base_score


def annotate_squeeze_to_signal(signal_dict: dict, squeeze: SqueezeResult) -> dict:
    """
    Adiciona informações de squeeze a um sinal.
    """
    signal_dict["squeeze"] = {
        "is_squeeze": squeeze.is_squeeze,
        "is_crowded": squeeze.is_crowded,
        "danger_level": squeeze.danger_level,
        "reasons": squeeze.reasons,
        "adjusted_score": score_adjustment_for_squeeze(squeeze, signal_dict.get("score", 0)),
    }
    return signal_dict
