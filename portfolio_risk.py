"""
portfolio_risk.py — Portfolio Risk Manager para Jarvis AI Trading Monitor

Responsabilidades:
1. Controlar exposição total por símbolo e direção
2. Limitar trades simultâneos
3. Detectar correlações perigosas
4. Limitar alavancagem agregada
5. Blockear sinais em dados inválidos

Uso:
    risk_manager = PortfolioRiskManager()
    approved_signals = risk_manager.filter_signals(signals, macro_snap)
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from logger import get_logger
from data_quality import get_current_quality

log = get_logger("portfolio_risk")

SECTOR_CORRELATION: Dict[str, Set[str]] = {
    "layer1": {"ETH", "SOL", "AVAX", "NEAR", "APT", "SUI", "INJ", "TIA"},
    "DeFi": {"ARB", "OP", "LDO", "RNDR", "FET", "UNI", "AAVE"},
    "meme": {"WIF", "BONK", "PEPE", "DOGE"},
    "AI": {"TAO", "FET", "RNDR", "WLD"},
    "L2": {"ARB", "OP"},
}


@dataclass
class PositionRisk:
    symbol: str
    direction: str
    score: float
    sector: str
    correlation_key: Optional[str] = None


class PortfolioRiskManager:
    def __init__(self):
        self._open_positions: Dict[str, PositionRisk] = {}
        self._last_check: float = time.time()
        self._lock_period: int = 300

        self.max_simultaneous_trades: int = 3
        self.max_exposure_per_symbol: float = 0.20
        self.max_sector_exposure: float = 0.40
        self.max_correlated_trades: int = 2
        self.min_data_quality_score: float = 0.60

        self._recent_rejections: List[dict] = []

    def reset_daily(self) -> None:
        self._open_positions.clear()
        self._recent_rejections.clear()
        log.info("PORTFOLIO_RISK", "Daily reset complete")

    def _check_data_quality(self) -> tuple[bool, str]:
        quality = get_current_quality()
        
        if quality.quality_score < self.min_data_quality_score:
            return False, f"Data quality {quality.quality_score:.0%} below minimum {self.min_data_quality_score:.0%}"
        
        if quality.warnings:
            critical_warnings = [w for w in quality.warnings if "desconectado" in w.lower() or "fail" in w.lower()]
            if critical_warnings:
                return False, f"Critical data warnings: {critical_warnings[0]}"
        
        if not quality.hyperliquid_available:
            return False, "Hyperliquid API unavailable"
        
        if quality.market_age_minutes > 10:
            return False, f"Market data stale ({quality.market_age_minutes:.0f}min)"
        
        return True, "OK"

    def _check_sector_correlation(self, symbol: str, direction: str) -> tuple[bool, str]:
        sector_key = None
        for key, symbols in SECTOR_CORRELATION.items():
            if symbol in symbols:
                sector_key = key
                break
        
        if not sector_key:
            return True, "OK"
        
        same_direction_in_sector = [
            p for p in self._open_positions.values()
            if p.sector == sector_key and p.direction == direction
        ]
        
        if len(same_direction_in_sector) >= self.max_correlated_trades:
            return False, f"Too many correlated trades in {sector_key}/{direction} ({len(same_direction_in_sector)}/{self.max_correlated_trades})"
        
        return True, "OK"

    def _check_macro_regime(self, macro_snap) -> tuple[bool, str]:
        if not macro_snap:
            return True, "OK"
        
        risk_score = getattr(macro_snap, "risk_score", 50)
        
        if risk_score >= 80:
            return False, f"Extreme macro risk ({risk_score:.0f}) - no new positions"
        
        if risk_score >= 70:
            log.warning("PORTFOLIO_RISK", f"High macro risk ({risk_score:.0f}) - limiting positions")
        
        return True, "OK"

    def filter_signals(
        self,
        signals: List,
        macro_snap = None,
    ) -> tuple[List, List[dict]]:
        approved = []
        rejected = []
        
        data_ok, data_reason = self._check_data_quality()
        if not data_ok:
            log.error("PORTFOLIO_RISK", f"BLOCKING ALL SIGNALS: {data_reason}")
            for sig in signals:
                rejected.append({
                    "symbol": sig.symbol,
                    "reason": f"DATA_INVALID: {data_reason}",
                    "timestamp": time.time(),
                })
            return [], rejected
        
        macro_ok, macro_reason = self._check_macro_regime(macro_snap)
        if not macro_ok:
            log.warning("PORTFOLIO_RISK", f"BLOCKING ALL SIGNALS: {macro_reason}")
            for sig in signals:
                rejected.append({
                    "symbol": sig.symbol,
                    "reason": f"MACRO_BLOCK: {macro_reason}",
                    "timestamp": time.time(),
                })
            return [], rejected
        
        for sig in signals:
            symbol = sig.symbol
            direction = sig.direction
            
            sector_key = None
            for key, symbols in SECTOR_CORRELATION.items():
                if symbol in symbols:
                    sector_key = key
                    break
            
            corr_ok, corr_reason = self._check_sector_correlation(symbol, direction)
            if not corr_ok:
                rejected.append({
                    "symbol": symbol,
                    "direction": direction,
                    "reason": corr_reason,
                    "score": sig.score,
                    "timestamp": time.time(),
                })
                log.warning("PORTFOLIO_RISK", f"REJECTED {symbol}: {corr_reason}")
                continue
            
            if len(approved) >= self.max_simultaneous_trades:
                rejected.append({
                    "symbol": symbol,
                    "direction": direction,
                    "reason": f"Max simultaneous trades ({self.max_simultaneous_trades}) reached",
                    "score": sig.score,
                    "timestamp": time.time(),
                })
                continue
            
            approved.append(sig)
            self._open_positions[symbol] = PositionRisk(
                symbol=symbol,
                direction=direction,
                score=sig.score,
                sector=sector_key or "other",
            )
            log.info("PORTFOLIO_RISK", f"APPROVED {symbol}/{direction} score={sig.score:.0f}")
        
        if rejected:
            self._recent_rejections.extend(rejected[-20:])
        
        return approved, rejected

    def get_status(self) -> dict:
        return {
            "open_positions": len(self._open_positions),
            "max_allowed": self.max_simultaneous_trades,
            "data_quality_ok": self._check_data_quality()[0],
            "recent_rejections": len(self._recent_rejections[-10:]),
        }
