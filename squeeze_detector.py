"""
squeeze_detector.py — Liquidation Squeeze and Crowded Trade Detector

Detecta setups perigosos onde:
1. Funding extremamente alto (long squeeze esperado)
2. Open Interest em spike (movimento iminente)
3. Preço perto de máxima histórica (liquidação de longa)
4. Correlação com posições populares

Isso evita entrar em trades que parecem bons mas são armadilhas.

NOVO: Detecção de Long Squeeze e Short Squeeze com sinais direcionais:
- LONG_SQUEEZE_RISK: Funding alto + OI subindo + preço lateral → sinal SHORT
- SHORT_SQUEEZE_RISK: Funding baixo + OI subindo + preço segurando → sinal LONG

NOVO: Stochastic Oscillator de 4h para scalp com mão pesada:
- STOCH < 20: Sobrevendido →_LONG_ (mão pesada)
- STOCH > 80: Sobrecomprado → _SHORT_ (mão pesada)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

from logger import get_logger

log = get_logger("squeeze")

FUNDING_DANGER_HIGH = 0.01
FUNDING_WARNING = 0.003
OI_SPIKE_THRESHOLD = 1.5
PRICE_NEAR_ATH_THRESHOLD = 0.95

STOCH_PERIOD = 14
STOCH_OVERBOUGHT = 80.0
STOCH_OVERSOLD = 20.0
STOCH_SIGNAL_PERIOD = 3


class SqueezeType(str, Enum):
    LONG_SQUEEZE = "LONG_SQUEEZE_RISK"
    SHORT_SQUEEZE = "SHORT_SQUEEZE_RISK"
    NO_SIGNAL = "NO_SIGNAL"


class ConfidenceLevel(str, Enum):
    BAIXA = "baixa"
    MODERADA = "moderada"
    ALTA = "alta"
    MUITO_ALTA = "muito alta"


class ScoreBand(str, Enum):
    SEM_SINAL = "SEM_SINAL"
    SINAL_FRACO = "SINAL_FRACO"
    SINAL_MODERADO = "SINAL_MODERADO"
    SINAL_FORTE = "SINAL_FORTE"


class StochasticSignal(str, Enum):
    OVERSOLD = "SOBREVENDIDO"      # < 20 → LONG
    OVERBOUGHT = "SOBRECOMPRADO"   # > 80 → SHORT
    NEUTRAL = "NEUTRO"


@dataclass
class SqueezeResult:
    is_squeeze: bool
    is_crowded: bool
    danger_level: str
    reasons: list
    recommendation: str


@dataclass
class StochasticResult:
    k: float
    d: float
    signal: StochasticSignal
    crossover: str
    divergence: str


@dataclass
class SqueezeSignal:
    token: str
    sinal: str
    score: float
    motivos: List[str]
    acao_sugerida: str
    confianca: str
    funding_info: str = ""
    oi_info: str = ""
    price_info: str = ""
    volume_confirmed: bool = False
    wick_confirmed: bool = False
    rsi_divergence: bool = False
    score_band: str = ""
    stoch_signal: str = ""
    
    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "sinal": self.sinal,
            "score": self.score,
            "motivo": self.motivos,
            "acao_sugerida": self.acao_sugerida,
            "confianca": self.confianca,
        }


def _compute_stochastic(highs: List[float], lows: List[float], closes: List[float], 
                        period: int = STOCH_PERIOD, signal_period: int = STOCH_SIGNAL_PERIOD) -> StochasticResult:
    """
    Computa Stochastic Oscillator.
    
    Returns:
        StochasticResult com %K, %D e sinal
    """
    import numpy as np
    
    n = len(closes)
    if n < period:
        return StochasticResult(k=50.0, d=50.0, signal=StochasticSignal.NEUTRAL, crossover="", divergence="")
    
    k_values = []
    for i in range(period, n + 1):
        window_high = max(highs[i-period:i])
        window_low = min(lows[i-period:i])
        current_close = closes[i-1]
        
        if window_high != window_low:
            k = (current_close - window_low) / (window_high - window_low) * 100
        else:
            k = 50.0
        k_values.append(k)
    
    if len(k_values) < signal_period:
        k = k_values[-1] if k_values else 50.0
        return StochasticResult(k=k, d=50.0, signal=StochasticSignal.NEUTRAL, crossover="", divergence="")
    
    k = k_values[-1]
    d = np.mean(k_values[-signal_period:]) if len(k_values) >= signal_period else k
    
    if k < STOCH_OVERSOLD:
        signal = StochasticSignal.OVERSOLD
    elif k > STOCH_OVERBOUGHT:
        signal = StochasticSignal.OVERBOUGHT
    else:
        signal = StochasticSignal.NEUTRAL
    
    crossover = ""
    if len(k_values) >= 2:
        prev_k = k_values[-2]
        if prev_k < d and k >= d and k < STOCH_OVERSOLD:
            crossover = "GOLDEN_CROSS_ATE_BOTTOM"
        elif prev_k > d and k <= d and k > STOCH_OVERBOUGHT:
            crossover = "DEATH_CROSS_FROM_TOP"
        elif prev_k < d and k >= d:
            crossover = "CROSS_UP"
        elif prev_k > d and k <= d:
            crossover = "CROSS_DOWN"
    
    divergence = ""
    if len(closes) >= period * 2:
        recent_closes = closes[-period:]
        older_closes = closes[-period*2:-period]
        if max(recent_closes) > max(older_closes) and k < 50:
            divergence = "HIDDEN_BULLISH"
        elif min(recent_closes) < min(older_closes) and k > 50:
            divergence = "HIDDEN_BEARISH"
    
    return StochasticResult(k=k, d=d, signal=signal, crossover=crossover, divergence=divergence)


def _compute_rsi(closes: List[float], period: int = 14) -> float:
    """Computa RSI simples."""
    import numpy as np
    c = np.array(closes, dtype=float)
    if len(c) < period + 1:
        return 50.0
    deltas = np.diff(c)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 4)


def _check_rsi_divergence(closes: List[float], period: int = 14) -> bool:
    """Detecta divergência RSI: preço faz topo mas RSI não confirma."""
    if len(closes) < period * 3:
        return False
    
    rsi_values = [_compute_rsi(closes[:i+1], period) for i in range(period, len(closes))]
    
    if len(rsi_values) < 6:
        return False
    
    recent_closes = closes[-6:]
    recent_rsi = rsi_values[-6:]
    
    price_trend = recent_closes[-1] - recent_closes[0]
    rsi_trend = recent_rsi[-1] - recent_rsi[0]
    
    if price_trend > 0 and rsi_trend < -5:
        return True
    if price_trend < 0 and rsi_trend > 5:
        return True
    
    return False


def _check_volume_decline(volumes: List[float], lookback: int = 2) -> bool:
    """Verifica se volume está caindo nas últimas velas."""
    if len(volumes) < lookback + 2:
        return False
    
    recent_vol = sum(volumes[-lookback:]) / lookback
    prev_vol = sum(volumes[-lookback-2:-2]) / lookback
    
    return recent_vol < prev_vol * 0.8


def _check_large_wick(candles_ohlcv: List[dict], threshold: float = 1.5) -> bool:
    """Verifica se há pavios grandes (> 1.5% do corpo)."""
    if not candles_ohlcv or len(candles_ohlcv) < 2:
        return False
    
    for candle in candles_ohlcv[-3:]:
        high = candle.get("high", 0)
        low = candle.get("low", 0)
        close = candle.get("close", 0)
        open_price = candle.get("open", close)
        
        if close == 0:
            continue
        
        body = abs(close - open_price)
        upper_wick = high - max(close, open_price)
        lower_wick = min(close, open_price) - low
        
        if body > 0:
            wick_ratio = max(upper_wick, lower_wick) / body
            if wick_ratio > threshold:
                return True
    
    return False


def _get_price_change(closes: List[float], lookback: int = 4) -> float:
    """Calcula variação percentual nas últimas velas."""
    if len(closes) < lookback + 1:
        return 0.0
    return (closes[-1] - closes[-lookback]) / closes[-lookback] * 100


def detect_squeeze_signal(
    symbol: str,
    funding_rate: Optional[float],
    funding_8h: Optional[float],
    oi_change_pct: Optional[float],
    current_price: float,
    closes: List[float],
    volumes: List[float] = None,
    candles_ohlcv: List[dict] = None,
    recent_low: float = None,
    recent_high: float = None,
    highs: List[float] = None,
    lows: List[float] = None,
) -> SqueezeSignal:
    """
    Detecta sinais de LONG_SQUEEZE ou SHORT_SQUEEZE.
    
    Args:
        symbol: Símbolo do token
        funding_rate: Funding rate atual (ex: 0.001 = 0.1%)
        funding_8h: Funding rate das últimas 8h (se disponível)
        oi_change_pct: Mudança % no Open Interest
        current_price: Preço atual
        closes: Lista de preços de fechamento
        volumes: Lista de volumes (opcional)
        candles_ohlcv: Lista de candles OHLCV (opcional)
        recent_low: Mínima recente (opcional)
        recent_high: Máxima recente (opcional)
        highs: Lista de preços máximos (para Stochastic)
        lows: Lista de preços mínimos (para Stochastic)
    
    Returns:
        SqueezeSignal com análise completa
    """
    score = 0.0
    motivos = []
    squeeze_type = SqueezeType.NO_SIGNAL
    acao_sugerida = "NEUTRO"
    stoch_signal = ""
    
    has_funding = funding_rate is not None
    has_oi = oi_change_pct is not None
    has_closes = closes and len(closes) >= 5
    
    if not has_funding and not has_oi:
        return SqueezeSignal(
            token=symbol,
            sinal="NO_SIGNAL",
            score=0,
            motivos=["Dados incompletos - funding/OI não disponíveis"],
            acao_sugerida="NEUTRO",
            confianca="baixa",
        )
    
    funding_short_term = funding_rate is not None and (funding_rate > 0.0001 or funding_rate < -0.0001)
    funding_long_term = funding_8h is not None and (funding_8h > 0.0005 or funding_8h < -0.0005)
    
    oi_subindo = oi_change_pct is not None and oi_change_pct > 0
    
    price_change_4h = _get_price_change(closes, 4) if has_closes else 0.0
    
    if volumes:
        volume_declining = _check_volume_decline(volumes)
    else:
        volume_declining = False
    
    has_wick = _check_large_wick(candles_ohlcv) if candles_ohlcv else False
    
    has_rsi_div = _check_rsi_divergence(closes) if has_closes else False
    
    if has_funding and (funding_rate > 0.0005 or (funding_8h and funding_8h > 0.0001)):
        if funding_rate > 0.0001 or (funding_8h and funding_8h > 0.0005):
            if squeeze_type == SqueezeType.NO_SIGNAL:
                squeeze_type = SqueezeType.LONG_SQUEEZE
    
    if has_funding and (funding_rate < -0.0005 or (funding_8h and funding_8h < -0.0001)):
        if funding_rate < -0.0001 or (funding_8h and funding_8h < -0.0005):
            if squeeze_type == SqueezeType.NO_SIGNAL:
                squeeze_type = SqueezeType.SHORT_SQUEEZE
    
    if squeeze_type == SqueezeType.LONG_SQUEEZE:
        score = 50.0
        
        if has_funding and funding_rate is not None:
            motivos.append(f"Funding +{funding_rate*100:.2f}%")
            if funding_rate > 0.0005:
                score += 15
            elif funding_rate > 0.0001:
                score += 10
        
        if has_oi and oi_subindo:
            motivos.append(f"OI subindo +{oi_change_pct:.1f}%")
            score += 10
        
        if has_closes and abs(price_change_4h) < 2.0:
            motivos.append(f"Preço lateral ({price_change_4h:+.1f}% em 4h)")
            score += 10
        
        if volume_declining:
            motivos.append("Volume caindo")
            score += 10
        
        if has_wick:
            motivos.append("Pavios grandes")
            score += 15
        
        if has_rsi_div:
            motivos.append("Divergência RSI")
            score += 20
        
        acao_sugerida = "SHORT"
        
    elif squeeze_type == SqueezeType.SHORT_SQUEEZE:
        score = 50.0
        
        if has_funding and funding_rate is not None:
            motivos.append(f"Funding {funding_rate*100:.2f}%")
            if funding_rate < -0.0005:
                score += 15
            elif funding_rate < -0.0001:
                score += 10
        
        if has_oi and oi_subindo:
            motivos.append(f"OI subindo +{oi_change_pct:.1f}%")
            score += 10
        
        if has_closes:
            if price_change_4h > -3.0:
                motivos.append(f"Preço segurando ({price_change_4h:+.1f}% em 4h)")
                score += 10
            elif recent_low and current_price > recent_low * 0.97:
                motivos.append("Não rompeu mínima recente")
                score += 15
        
        if volume_declining:
            motivos.append("Volume caindo")
            score += 10
        
        if has_wick:
            motivos.append("Pavios grandes")
            score += 15
        
        if has_rsi_div:
            motivos.append("Divergência RSI")
            score += 20
        
        acao_sugerida = "LONG"
    
    if squeeze_type == SqueezeType.NO_SIGNAL and has_closes and highs and lows:
        stoch_result = _compute_stochastic(highs, lows, closes)
        stoch_signal = stoch_result.signal.value
        
        if stoch_result.signal == StochasticSignal.OVERSOLD:
            squeeze_type = SqueezeType.SHORT_SQUEEZE
            score = 50.0
            motivos.append(f"STOCH {stoch_result.k:.0f} - SOBREVENDIDO")
            score += 20
            if stoch_result.crossover == "GOLDEN_CROSS_ATE_BOTTOM":
                motivos.append("Golden Cross no fundo")
                score += 15
            if stoch_result.divergence == "HIDDEN_BULLISH":
                motivos.append("Divergência oculta bullish")
                score += 10
            acao_sugerida = "LONG"
        
        elif stoch_result.signal == StochasticSignal.OVERBOUGHT:
            squeeze_type = SqueezeType.LONG_SQUEEZE
            score = 50.0
            motivos.append(f"STOCH {stoch_result.k:.0f} - SOBRECOMPRADO")
            score += 20
            if stoch_result.crossover == "DEATH_CROSS_FROM_TOP":
                motivos.append("Death Cross no topo")
                score += 15
            if stoch_result.divergence == "HIDDEN_BEARISH":
                motivos.append("Divergência oculta bearish")
                score += 10
            acao_sugerida = "SHORT"
    
    score = min(100.0, max(0.0, score))
    
    if score >= 81:
        confianca = ConfidenceLevel.MUITO_ALTA
        band = ScoreBand.SINAL_FORTE
    elif score >= 61:
        confianca = ConfidenceLevel.ALTA
        band = ScoreBand.SINAL_MODERADO
    elif score >= 31:
        confianca = ConfidenceLevel.MODERADA
        band = ScoreBand.SINAL_FRACO
    else:
        confianca = ConfidenceLevel.BAIXA
        band = ScoreBand.SEM_SINAL
        squeeze_type = SqueezeType.NO_SIGNAL
        motivos = ["Nenhum sinal de squeeze detectado"]
        acao_sugerida = "NEUTRO"
    
    log.info(
        "SQUEEZE_SIGNAL",
        f"{symbol}: tipo={squeeze_type.value} score={score:.0f} acao={acao_sugerida} "
        f"stoch={stoch_signal} motivos={len(motivos)} conf={confianca.value}"
    )
    
    return SqueezeSignal(
        token=symbol,
        sinal=squeeze_type.value,
        score=score,
        motivos=motivos,
        acao_sugerida=acao_sugerida,
        confianca=confianca.value,
        funding_info=f"{funding_rate*100:.3f}%" if funding_rate else "N/A",
        oi_info=f"{oi_change_pct:+.1f}%" if oi_change_pct else "N/A",
        price_info=f"{price_change_4h:+.1f}%" if has_closes else "N/A",
        volume_confirmed=volume_declining,
        wick_confirmed=has_wick,
        rsi_divergence=has_rsi_div,
        score_band=band.value,
        stoch_signal=stoch_signal,
    )


def format_squeeze_alert(signal: SqueezeSignal) -> str:
    """Formata um alerta de squeeze para envio no Telegram."""
    if signal.sinal == SqueezeType.NO_SIGNAL.value:
        return ""
    
    emoji_dir = "📈" if signal.acao_sugerida == "LONG" else "📉" if signal.acao_sugerida == "SHORT" else "⚪"
    emoji_conf = "🔥" if signal.confianca == "muito alta" else "⚡" if signal.confianca == "alta" else "💡"
    
    lines = [
        f"{emoji_dir} *SINAL DE SQUEEZE* {emoji_conf}",
        f"_{signal.token}_",
        "",
        f"*Tipo:* `{signal.sinal}`",
        f"*Score:* `{signal.score:.0f}/100`",
        f"*Ação:* `{signal.acao_sugerida}`",
        f"*Confiança:* `{signal.confianca}`",
        "",
        "*Motivos:*",
    ]
    
    for motivo in signal.motivos:
        lines.append(f"• {motivo}")
    
    lines.append("")
    lines.append("_Jarvis AI Trading Monitor_")
    
    return "\n".join(lines)


# Mantém compatibilidade com código existente
def detect_squeeze(
    funding_rate: Optional[float],
    oi_change_pct: Optional[float],
    current_price: float,
    ath_price: float,
    position_direction: str,
) -> SqueezeResult:
    """
    Analisa se o trade está em squeeze/crowded territory (versão legada).
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
    
    return SqueezeResult(
        is_squeeze=is_squeeze,
        is_crowded=is_crowded,
        danger_level=danger_level,
        reasons=reasons,
        recommendation=recommendation,
    )


def score_adjustment_for_squeeze(squeeze: SqueezeResult, base_score: float) -> float:
    """Ajusta score do sinal baseado no squeeze."""
    if squeeze.is_squeeze:
        return base_score - 10
    if squeeze.is_crowded:
        return base_score - 5
    if squeeze.danger_level == "MEDIUM":
        return base_score - 3
    return base_score


def annotate_squeeze_to_signal(signal_dict: dict, squeeze: SqueezeResult) -> dict:
    """Adiciona informações de squeeze a um sinal."""
    signal_dict["squeeze"] = {
        "is_squeeze": squeeze.is_squeeze,
        "is_crowded": squeeze.is_crowded,
        "danger_level": squeeze.danger_level,
        "reasons": squeeze.reasons,
        "adjusted_score": score_adjustment_for_squeeze(squeeze, signal_dict.get("score", 0)),
    }
    return signal_dict
