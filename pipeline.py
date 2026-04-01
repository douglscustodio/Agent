"""
pipeline.py — Pipeline de Dados do Jarvis

PIPELINE:
RAW DATA → CLEAN → VALIDATE → FEATURE → ANALYZE → DECIDE

Cada etapa:
1. RAW DATA: Dados crus das APIs
2. CLEAN: Limpa e formata
3. VALIDATE: Valida qualidade
4. FEATURE: Extrai features
5. ANALYZE: Roda análises
6. DECIDE: Toma decisões
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
import time

from logger import get_logger

log = get_logger("pipeline")


@dataclass
class DataQuality:
    """Qualidade dos dados."""
    is_valid: bool
    age_seconds: float
    source: str
    issues: List[str]
    
    def should_block(self) -> bool:
        return not self.is_valid or self.age_seconds > 300  # 5 min


class PipelineStage:
    """Uma etapa do pipeline."""
    
    def __init__(self, name: str, processor: Callable):
        self.name = name
        self.processor = processor
        self.execution_count = 0
        self.error_count = 0
        self.last_execution = 0.0
    
    async def execute(self, data: Any) -> Any:
        self.execution_count += 1
        self.last_execution = time.time()
        
        try:
            result = await self.processor(data)
            log.debug("PIPELINE_STAGE", f"{self.name}: OK")
            return result
        except Exception as exc:
            self.error_count += 1
            log.error("PIPELINE_STAGE", f"{self.name}: ERROR - {exc}")
            raise


class DataPipeline:
    """
    Pipeline de processamento de dados.
    
    Executa etapas em sequência:
    1. fetch_raw()
    2. clean()
    3. validate()
    4. extract_features()
    5. analyze()
    6. decide()
    """
    
    def __init__(self):
        self.stages: List[PipelineStage] = []
        self._data_cache: Dict[str, Any] = {}
        self._last_pipeline_time = 0.0
    
    def add_stage(self, name: str, processor: Callable) -> None:
        """Adiciona uma etapa ao pipeline."""
        stage = PipelineStage(name, processor)
        self.stages.append(stage)
        log.info("PIPELINE", f"Stage added: {name} (total: {len(self.stages)})")
    
    async def execute(self, initial_data: Any = None) -> Any:
        """Executa todo o pipeline."""
        pipeline_start = time.time()
        current_data = initial_data
        
        for stage in self.stages:
            try:
                current_data = await stage.execute(current_data)
            except Exception as exc:
                log.error("PIPELINE", f"Stage {stage.name} failed: {exc}")
                return None
        
        self._last_pipeline_time = time.time() - pipeline_start
        
        log.info(
            "PIPELINE",
            f"Pipeline complete in {self._last_pipeline_time*1000:.0f}ms",
            stages=len(self.stages),
            errors=sum(s.error_count for s in self.stages)
        )
        
        return current_data
    
    def get_cache(self, key: str) -> Optional[Any]:
        return self._data_cache.get(key)
    
    def set_cache(self, key: str, value: Any) -> None:
        self._data_cache[key] = value
    
    def clear_cache(self) -> None:
        self._data_cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "stages": len(self.stages),
            "total_time_ms": self._last_pipeline_time * 1000,
            "cache_size": len(self._data_cache),
            "stage_stats": [
                {
                    "name": s.name,
                    "executions": s.execution_count,
                    "errors": s.error_count,
                    "last_execution": datetime.fromtimestamp(s.last_execution, tz=timezone.utc).isoformat()
                        if s.last_execution else None
                }
                for s in self.stages
            ]
        }


# ============================================================================
# ETAPAS PRÉ-DEFINIDAS
# ============================================================================

async def clean_price_data(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """Limpa dados de preço."""
    cleaned = {}
    
    # Validar BTC
    btc_price = raw_data.get("btc_price", 0)
    if btc_price and btc_price > 0:
        # Remover preços impossíveis
        if 1000 < btc_price < 1000000:
            cleaned["btc_price"] = btc_price
    
    # Validar closes
    closes = raw_data.get("closes", [])
    if closes and len(closes) > 0:
        # Filtrar valores inválidos
        valid_closes = [c for c in closes if c and 1000 < c < 1000000]
        if valid_closes:
            cleaned["closes"] = valid_closes
    
    return cleaned


async def validate_data_quality(data: Dict[str, Any]) -> DataQuality:
    """Valida qualidade dos dados."""
    issues = []
    
    if "btc_price" not in data or not data["btc_price"]:
        issues.append("BTC price inválido")
    
    if "closes" not in data or len(data.get("closes", [])) < 20:
        issues.append("Dados de closes insuficientes")
    
    is_valid = len(issues) == 0
    
    return DataQuality(
        is_valid=is_valid,
        age_seconds=data.get("age_seconds", 0),
        source=data.get("source", "unknown"),
        issues=issues
    )


async def extract_market_features(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai features de mercado."""
    features = {}
    
    closes = data.get("closes", [])
    if len(closes) >= 20:
        # Variações
        features["change_1h"] = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0
        features["change_4h"] = ((closes[-1] - closes[-5]) / closes[-5] * 100) if len(closes) >= 5 else 0
        features["change_24h"] = ((closes[-1] - closes[-25]) / closes[-25] * 100) if len(closes) >= 25 else 0
        
        # Volatilidade
        if len(closes) >= 20:
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, min(20, len(closes)))]
            features["volatility_1d"] = (max(returns) - min(returns)) * 100 if returns else 0
            features["avg_volume"] = sum(returns) / len(returns) if returns else 0
        
        # Níveis
        features["high_20"] = max(closes[-20:])
        features["low_20"] = min(closes[-20:])
        features["avg_20"] = sum(closes[-20:]) / 20
        
        # Range position
        current = closes[-1]
        high = features["high_20"]
        low = features["low_20"]
        if high != low:
            features["range_position"] = (current - low) / (high - low)
        else:
            features["range_position"] = 0.5
    
    return {**data, "features": features}


# ============================================================================
# SINGLETON
# ============================================================================

_pipeline: Optional[DataPipeline] = None


def get_pipeline() -> DataPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DataPipeline()
        # Adicionar estágios padrão
        _pipeline.add_stage("clean", clean_price_data)
        _pipeline.add_stage("validate", lambda d: validate_data_quality(d))
        _pipeline.add_stage("features", extract_market_features)
    return _pipeline
