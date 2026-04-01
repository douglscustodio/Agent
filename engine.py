"""
engine.py — Centro de Orquestração do Jarvis AI Trading Monitor

Este é o CÉREBRO do sistema. Responsável por:
1. Orquestrar o fluxo principal
2. Manter estado global
3. Coordenar todos os módulos
4. Tomar decisões

NÃO FAZ:
- Lógica de análise (vai para modules/)
- Comunicação direta (vai para interface/)
- Acesso a banco (vai para infra/)
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from enum import Enum

from logger import get_logger

log = get_logger("engine")


class MarketTrend(Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"
    VOLATILE = "VOLATILE"


class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    CONSOLIDATING = "CONSOLIDATING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


@dataclass
class MarketState:
    """
    Estado global do mercado - ÚNICA FONTE DE VERDADE.
    
    Todos os módulos leem daqui para decisões.
    """
    # Preços
    btc_price: float = 0.0
    btc_change_1h: float = 0.0
    btc_change_4h: float = 0.0
    btc_change_24h: float = 0.0
    
    # Regime e tendência
    regime: MarketRegime = MarketRegime.UNKNOWN
    trend: MarketTrend = MarketTrend.NEUTRAL
    regime_strength: float = 0.0  # ADX
    regime_direction: str = "NEUTRAL"
    
    # Volatilidade
    volatility: str = "NORMAL"  # LOW, NORMAL, HIGH, EXTREME
    btc_volatility_1h: float = 0.0
    
    # Risco
    risk_level: str = "MEDIUM"  # LOW, MEDIUM, HIGH, EXTREME
    risk_score: float = 50.0
    
    # Liquidez
    liquidity: str = "NORMAL"  # LOW, NORMAL, HIGH
    
    # Timestamps
    last_update: float = field(default_factory=time.time)
    last_regime_change: float = 0.0
    last_trend_change: float = 0.0
    
    # Dados históricos
    btc_closes: List[float] = field(default_factory=list)
    btc_highs: List[float] = field(default_factory=list)
    btc_lows: List[float] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "btc_price": self.btc_price,
            "btc_change_1h": self.btc_change_1h,
            "btc_change_4h": self.btc_change_4h,
            "btc_change_24h": self.btc_change_24h,
            "regime": self.regime.value,
            "trend": self.trend.value,
            "regime_strength": self.regime_strength,
            "volatility": self.volatility,
            "risk_level": self.risk_level,
            "liquidity": self.liquidity,
            "last_update": datetime.fromtimestamp(self.last_update, tz=timezone.utc).isoformat(),
        }


@dataclass
class SystemState:
    """
    Estado do sistema - configurações e saúde.
    """
    is_running: bool = False
    ws_connected: bool = False
    db_connected: bool = False
    api_available: bool = False
    last_error: Optional[str] = None
    restart_count: int = 0


class JarvisEngine:
    """
    Motor central do Jarvis.
    
    Fluxo:
    1. fetch_data()     - Busca dados
    2. validate_data() - Valida qualidade
    3. update_state()  - Atualiza estado global
    4. analyze()       - Roda análises
    5. decide()        - Toma decisões
    6. execute()       - Executa ações
    7. log()           - Registra tudo
    """
    
    def __init__(self):
        self.market_state = MarketState()
        self.system_state = SystemState()
        self._running = False
        self._last_cycle = 0.0
        self._cycle_count = 0
        
    @property
    def is_market_bullish(self) -> bool:
        return self.market_state.trend == MarketTrend.BULL
    
    @property
    def is_market_bearish(self) -> bool:
        return self.market_state.trend == MarketTrend.BEAR
    
    @property
    def is_trending(self) -> bool:
        return self.market_state.regime in (
            MarketRegime.TRENDING_UP,
            MarketRegime.TRENDING_DOWN,
        )
    
    @property
    def is_volatile(self) -> bool:
        return self.market_state.volatility in ("HIGH", "EXTREME")
    
    @property
    def can_trade(self) -> bool:
        """Verifica se pode operar baseado no estado."""
        if self.market_state.risk_level == "EXTREME":
            return False
        if self.market_state.volatility == "EXTREME":
            return False
        return True
    
    async def fetch_data(self) -> Dict[str, Any]:
        """
        FETCH - Busca dados de todas as fontes.
        
        Returns:
            Dict com todos os dados coletados
        """
        data = {
            "timestamp": time.time(),
            "btc_price": self.market_state.btc_price,
            "closes": self.market_state.btc_closes,
        }
        return data
    
    def validate_data(self, data: Dict[str, Any]) -> bool:
        """
        VALIDATE - Valida qualidade dos dados.
        
        Returns:
            True se dados são válidos
        """
        if data.get("btc_price", 0) <= 0:
            log.warning("ENGINE_VALIDATE", "btc_price inválido")
            return False
        if not data.get("closes"):
            log.warning("ENGINE_VALIDATE", "sem dados de closes")
            return False
        return True
    
    def update_state(self, data: Dict[str, Any]) -> None:
        """
        UPDATE STATE - Atualiza estado global com novos dados.
        
        Este é o ÚNICO lugar onde market_state é modificado.
        """
        old_trend = self.market_state.trend
        old_regime = self.market_state.regime
        
        # Atualiza preços
        if "btc_price" in data:
            self.market_state.btc_price = data["btc_price"]
        
        if "closes" in data:
            closes = data["closes"]
            self.market_state.btc_closes = closes
            
            # Calcula variações
            if len(closes) >= 2:
                self.market_state.btc_change_1h = (
                    (closes[-1] - closes[-2]) / closes[-2] * 100
                )
            if len(closes) >= 5:
                self.market_state.btc_change_4h = (
                    (closes[-1] - closes[-5]) / closes[-5] * 100
                )
            if len(closes) >= 25:
                self.market_state.btc_change_24h = (
                    (closes[-1] - closes[-25]) / closes[-25] * 100
                )
        
        if "regime" in data:
            self.market_state.regime = data["regime"]
            if data["regime"] != old_regime:
                self.market_state.last_regime_change = time.time()
                log.info("ENGINE_STATE", f"regime changed: {old_regime.value} -> {data['regime'].value}")
        
        if "trend" in data:
            self.market_state.trend = data["trend"]
            if data["trend"] != old_trend:
                self.market_state.last_trend_change = time.time()
                log.info("ENGINE_STATE", f"trend changed: {old_trend.value} -> {data['trend'].value}")
        
        if "regime_strength" in data:
            self.market_state.regime_strength = data["regime_strength"]
        
        self.market_state.last_update = time.time()
    
    def decide(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        DECIDE - Toma decisões baseadas na análise.
        
        Returns:
            Dict com decisão: action, reason, confidence
        """
        decision = {
            "action": "WAIT",
            "reason": "No clear signal",
            "confidence": 0.0,
            "state": self.market_state.to_dict(),
        }
        
        # Verifica se pode operar
        if not self.can_trade:
            decision["reason"] = f"Cannot trade - Risk: {self.market_state.risk_level}, Vol: {self.market_state.volatility}"
            return decision
        
        # Análise de score
        score = analysis.get("total_score", 0)
        
        if score >= 75:
            decision["action"] = "STRONG_BUY" if analysis.get("direction") == "LONG" else "STRONG_SELL"
            decision["confidence"] = 0.85
            decision["reason"] = f"Score {score:.0f} - Confirmação múltipla"
        elif score >= 60:
            decision["action"] = "BUY" if analysis.get("direction") == "LONG" else "SELL"
            decision["confidence"] = 0.70
            decision["reason"] = f"Score {score:.0f} - Setup válido"
        elif score >= 45:
            decision["action"] = "WATCH"
            decision["confidence"] = 0.50
            decision["reason"] = f"Score {score:.0f} - Monitorar"
        else:
            decision["action"] = "WAIT"
            decision["confidence"] = 0.30
            decision["reason"] = f"Score {score:.0f} - Sem setup"
        
        # Ajusta baseado no regime
        if self.market_state.regime == MarketRegime.TRENDING_UP:
            if decision["action"] in ("SELL", "STRONG_SELL"):
                decision["action"] = "HOLD"
                decision["reason"] += " (contra tendência)"
        elif self.market_state.regime == MarketRegime.TRENDING_DOWN:
            if decision["action"] in ("BUY", "STRONG_BUY"):
                decision["action"] = "HOLD"
                decision["reason"] += " (contra tendência)"
        
        return decision
    
    def log_decision(self, decision: Dict[str, Any], analysis: Dict[str, Any]) -> None:
        """
        LOG - Registra decisão para rastreabilidade.
        
        Em produção, isso vai para DB para backtest.
        """
        log.info(
            "ENGINE_DECISION",
            f"action={decision['action']} confidence={decision['confidence']:.0%} "
            f"reason={decision['reason'][:50]} "
            f"state={self.market_state.trend.value} {self.market_state.regime.value}",
            extra={
                "cycle": self._cycle_count,
                "action": decision["action"],
                "confidence": decision["confidence"],
                "reason": decision["reason"],
                "market_state": self.market_state.to_dict(),
                "analysis_score": analysis.get("total_score", 0),
            }
        )
    
    async def run_cycle(self) -> Dict[str, Any]:
        """
        RUN CYCLE - Executa um ciclo completo.
        
        Returns:
            Dict com resultado do ciclo
        """
        self._cycle_count += 1
        cycle_start = time.time()
        
        try:
            # 1. Fetch
            data = await self.fetch_data()
            
            # 2. Validate
            if not self.validate_data(data):
                return {"status": "invalid_data", "cycle": self._cycle_count}
            
            # 3. Update State
            self.update_state(data)
            
            # 4. Analyze (será chamado externamente)
            analysis = {"status": "pending"}
            
            # 5. Decide
            decision = self.decide(analysis)
            
            # 6. Log
            self.log_decision(decision, analysis)
            
            self._last_cycle = time.time()
            
            return {
                "status": "success",
                "cycle": self._cycle_count,
                "state": self.market_state.to_dict(),
                "decision": decision,
                "duration_ms": (time.time() - cycle_start) * 1000,
            }
            
        except Exception as exc:
            log.error("ENGINE_CYCLE_ERROR", f"cycle {self._cycle_count} failed: {exc}")
            return {
                "status": "error",
                "cycle": self._cycle_count,
                "error": str(exc),
            }
    
    def get_state_summary(self) -> str:
        """Retorna resumo do estado atual para display."""
        state = self.market_state
        
        lines = [
            f"BTC: ${state.btc_price:,.2f}",
            f"1h: {state.btc_change_1h:+.2f}% | 4h: {state.btc_change_4h:+.2f}%",
            f"Regime: {state.regime.value}",
            f"Tendência: {state.trend.value}",
            f"ADX: {state.regime_strength:.0f}",
            f"Risco: {state.risk_level}",
            f"Volatilidade: {state.volatility}",
            f"Pode Operar: {'SIM' if self.can_trade else 'NÃO'}",
        ]
        
        return " | ".join(lines)


# Singleton
_engine: Optional[JarvisEngine] = None


def get_engine() -> JarvisEngine:
    """Retorna instância singleton do engine."""
    global _engine
    if _engine is None:
        _engine = JarvisEngine()
    return _engine
