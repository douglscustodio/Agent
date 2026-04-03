"""
data_quality.py — Sistema de qualidade de dados

Rastreia a qualidade e frescor dos dados para garantir que os sinais
emitidos sejam baseados em dados confiáveis e atuais.

Métricas rastreadas:
  - news_age_minutes: idade das notícias (0 = fresco, > 30min = stale)
  - market_data_age_minutes: idade dos dados de mercado
  - ws_connected: se WebSocket está conectado
  - hyperliquid_available: se dados Hyperliquid estão disponíveis
  - macro_available: se dados macro estão disponíveis
  - ai_available: se IA Groq está disponível
  - symbols_with_data: quantos símbolos têm dados

Thresholds:
  - FRESH_THRESHOLD_MIN: dados com menos de 2 min são "frescos"
  - STALE_THRESHOLD_MIN: dados com mais de 30 min são "stale"
  - QUALITY_THRESHOLD: se qualidade < 0.5, sinais são emitidos com aviso
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger("data_quality")

# Thresholds
FRESH_THRESHOLD_MIN = 2.0
STALE_THRESHOLD_MIN = 30.0
QUALITY_THRESHOLD = 0.5  # 50% dos dados devem estar disponíveis


@dataclass
class DataQuality:
    """Qualidade dos dados para o ciclo atual."""
    
    # Timestamps dos dados
    news_fetched_at: float = 0.0
    market_fetched_at: float = 0.0
    macro_fetched_at: float = 0.0
    
    # Status de disponibilidade
    ws_connected: bool = False
    hyperliquid_available: bool = False
    news_api_available: bool = False
    macro_api_available: bool = False
    ai_available: bool = False
    
    # Cobertura
    symbols_requested: int = 0
    symbols_with_data: int = 0
    
    # Erros
    errors: List[str] = field(default_factory=list)
    
    @property
    def is_fresh(self) -> bool:
        """Dados são frescos?"""
        now = time.time()
        max_age = max(
            now - self.news_fetched_at if self.news_fetched_at else 0,
            now - self.market_fetched_at if self.market_fetched_at else 0,
        )
        return max_age < FRESH_THRESHOLD_MIN * 60
    
    @property
    def news_age_minutes(self) -> float:
        """Idade das notícias em minutos."""
        if not self.news_fetched_at:
            return 999.0
        return (time.time() - self.news_fetched_at) / 60
    
    @property
    def market_age_minutes(self) -> float:
        """Idade dos dados de mercado em minutos."""
        if not self.market_fetched_at:
            return 999.0
        return (time.time() - self.market_fetched_at) / 60
    
    @property
    def macro_age_minutes(self) -> float:
        """Idade dos dados macro em minutos."""
        if not self.macro_fetched_at:
            return 999.0
        return (time.time() - self.macro_fetched_at) / 60
    
    @property
    def quality_score(self) -> float:
        """
        Score de qualidade 0.0 - 1.0.
        
        1.0 = todos os dados frescos e disponíveis
        0.5 = metade dos dados disponíveis
        0.0 = nenhum dado disponível
        """
        score = 0.0
        factors = 0
        
        # WS conectado (peso 0.2)
        if self.ws_connected:
            score += 0.2
        factors += 0.2
        
        # Hyperliquid disponível (peso 0.3)
        if self.hyperliquid_available:
            score += 0.3
        factors += 0.3
        
        # Notícias disponíveis (peso 0.15)
        if self.news_fetched_at and self.news_age_minutes < STALE_THRESHOLD_MIN:
            score += 0.15
        elif self.news_fetched_at:
            score += 0.05  # dados velhos mas disponíveis
        factors += 0.15
        
        # Macro disponível (peso 0.1)
        if self.macro_fetched_at and self.macro_age_minutes < STALE_THRESHOLD_MIN:
            score += 0.1
        elif self.macro_fetched_at:
            score += 0.03  # dados velhos
        factors += 0.1
        
        # Cobertura de símbolos (peso 0.15)
        if self.symbols_requested > 0:
            coverage = self.symbols_with_data / self.symbols_requested
            score += 0.15 * coverage
        factors += 0.15
        
        # IA disponível (peso 0.1 - menos crítico, pode ter fallback)
        if self.ai_available:
            score += 0.1
        factors += 0.1
        
        return score / 0.85 if factors > 0 else 0.0  # normalize
    
    @property
    def quality_label(self) -> str:
        """Label legível da qualidade."""
        q = self.quality_score
        if q >= 0.85:
            return "EXCELENTE [GREEN]"
        elif q >= 0.7:
            return "BOM [YELLOW]"
        elif q >= 0.5:
            return "RAZOÁVEL [ORANGE]"
        elif q >= 0.3:
            return "FRACO [RED]"
        else:
            return "INSUFICIENTE [FAIL]"
    
    @property
    def warnings(self) -> List[str]:
        """Lista de avisos baseados na qualidade."""
        warnings = []
        
        if not self.ws_connected:
            warnings.append("[WARN] WebSocket desconectado - usando dados potencialmente desatualizados")
        
        if self.news_age_minutes > STALE_THRESHOLD_MIN:
            warnings.append(f"[WARN] Notícias com {self.news_age_minutes:.0f}min de idade - dados podem estar obsoletos")
        
        if self.market_age_minutes > STALE_THRESHOLD_MIN:
            warnings.append(f"[WARN] Dados de mercado com {self.market_age_minutes:.0f}min de idade")
        
        if self.symbols_requested > 0:
            coverage = self.symbols_with_data / self.symbols_requested * 100
            if coverage < 50:
                warnings.append(f"[WARN] Apenas {coverage:.0f}% dos símbolos com dados")
        
        if not self.hyperliquid_available:
            warnings.append("[WARN] Hyperliquid indisponível - verificando dados alternativos")
        
        if not self.ai_available:
            warnings.append("[WARN] IA desabilitada - sinais sem validação de inteligência artificial")
        
        if self.errors:
            warnings.append(f"[WARN] {len(self.errors)} erro(s) detectado(s): {self.errors[0]}")
        
        return warnings
    
    @property
    def should_block_signals(self) -> bool:
        """Sinais devem ser bloqueados?"""
        return self.quality_score < QUALITY_THRESHOLD or not self.hyperliquid_available
    
    def validate_or_fail(self) -> None:
        """
        Valida dados e LANÇA ERRO se inválidos.
        NUNCA retorna dados simulados em silêncio.
        """
        if not self.hyperliquid_available:
            raise RuntimeError("BLOCKED: Hyperliquid API unavailable - no market data")
        
        if self.market_age_minutes > 15:
            raise RuntimeError(f"BLOCKED: Market data stale ({self.market_age_minutes:.0f}min)")
        
        if self.symbols_requested > 0:
            coverage = self.symbols_with_data / self.symbols_requested
            if coverage < 0.5:
                raise RuntimeError(f"BLOCKED: Only {coverage:.0%} symbols with data")
        
        if self.quality_score < 0.4:
            raise RuntimeError(f"BLOCKED: Data quality too low ({self.quality_score:.0%})")
    
    def to_dict(self) -> dict:
        """Serializa para dict (para logs/debug)."""
        return {
            "quality_score": round(self.quality_score, 2),
            "quality_label": self.quality_label,
            "is_fresh": self.is_fresh,
            "news_age_min": round(self.news_age_minutes, 1),
            "market_age_min": round(self.market_age_minutes, 1),
            "macro_age_min": round(self.macro_age_minutes, 1),
            "ws_connected": self.ws_connected,
            "hyperliquid_available": self.hyperliquid_available,
            "symbols_coverage": f"{self.symbols_with_data}/{self.symbols_requested}",
            "ai_available": self.ai_available,
            "warnings_count": len(self.warnings),
        }


# Singleton para compartilhar estado entre módulos
_current_quality: Optional[DataQuality] = None


def get_current_quality() -> DataQuality:
    """Retorna a qualidade atual dos dados."""
    global _current_quality
    if _current_quality is None:
        _current_quality = DataQuality()
    return _current_quality


def update_quality(**kwargs) -> DataQuality:
    """Atualiza a qualidade dos dados."""
    global _current_quality
    if _current_quality is None:
        _current_quality = DataQuality()
    
    for key, value in kwargs.items():
        if hasattr(_current_quality, key):
            setattr(_current_quality, key, value)
    
    log.debug(
        "PERFORMANCE_LOGGED",
        f"data quality updated: {get_current_quality().quality_label} "
        f"({get_current_quality().quality_score:.2f})"
    )
    
    return _current_quality


def log_quality_warnings() -> None:
    """Loga todos os avisos de qualidade."""
    quality = get_current_quality()
    for warning in quality.warnings:
        log.warning("DATA_QUALITY", warning)
