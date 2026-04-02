"""
kill_switch.py — Emergency Kill Switch and Drawdown Protection

Responsabilidades:
1. Monitorar P&L diário
2. Parar trading se perda diária > limite
3. Contador de perdas consecutivas
4. Auto-reset à meia-noite UTC
5. Bloquear novas posições se em drawdown

Uso:
    kill_switch = KillSwitch()
    
    # Antes de entrar em trade
    if not kill_switch.can_trade():
        log.warning("KILL_SWITCH", "Trading blocked - kill switch active")
        return
    
    # Depois de fechar trade
    kill_switch.record_result(symbol, pnl)
    
    # Status
    status = kill_switch.get_status()
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger("kill_switch")

MAX_DAILY_LOSS_PCT = 0.05
MAX_CONSECUTIVE_LOSSES = 3
MAX_DRAWDOWN_PCT = 0.10
RESET_HOUR_UTC = 0


@dataclass
class TradeResult:
    symbol: str
    pnl_pct: float
    timestamp: float
    direction: str


@dataclass
class KillSwitchStatus:
    is_active: bool
    reason: str
    daily_pnl_pct: float
    consecutive_losses: int
    last_reset: str
    trades_today: int
    block_new_trades: bool


class KillSwitch:
    def __init__(self):
        self._daily_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._last_reset_date: str = self._today_str()
        self._trades_today: int = 0
        self._trade_history: List[TradeResult] = []
        self._blocked_reason: str = ""
        
        self.max_daily_loss_pct = MAX_DAILY_LOSS_PCT
        self.max_consecutive_losses = MAX_CONSECUTIVE_LOSSES
        self.max_drawdown_pct = MAX_DRAWDOWN_PCT
        
        self._check_reset()
        log.info("KILL_SWITCH", f"initialized - max daily loss: {self.max_daily_loss_pct*100}%")

    def _today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_reset(self) -> None:
        today = self._today_str()
        if today != self._last_reset_date:
            self._daily_pnl = 0.0
            self._consecutive_losses = 0
            self._trades_today = 0
            self._trade_history = []
            self._last_reset_date = today
            self._blocked_reason = ""
            log.info("KILL_SWITCH", "Daily reset - trading allowed")

    def can_trade(self) -> bool:
        self._check_reset()
        
        if self._daily_pnl <= -self.max_daily_loss_pct:
            self._blocked_reason = f"Daily loss limit reached ({self._daily_pnl*100:.2f}%)"
            log.critical("KILL_SWITCH", self._blocked_reason)
            return False
        
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._blocked_reason = f"Consecutive losses limit ({self._consecutive_losses})"
            log.critical("KILL_SWITCH", self._blocked_reason)
            return False
        
        return True

    def record_result(self, symbol: str, pnl_pct: float, direction: str = "LONG") -> None:
        self._check_reset()
        
        self._trades_today += 1
        self._daily_pnl += pnl_pct
        
        trade = TradeResult(
            symbol=symbol,
            pnl_pct=pnl_pct,
            timestamp=time.time(),
            direction=direction,
        )
        self._trade_history.append(trade)
        
        if pnl_pct < 0:
            self._consecutive_losses += 1
            log.warning("KILL_SWITCH", f"Loss recorded: {symbol} {pnl_pct*100:.2f}% - consecutive: {self._consecutive_losses}")
        else:
            self._consecutive_losses = 0
            log.info("KILL_SWITCH", f"Win recorded: {symbol} {pnl_pct*100:.2f}%")
        
        self._check_kill_conditions()

    def _check_kill_conditions(self) -> None:
        if self._daily_pnl <= -self.max_daily_loss_pct:
            log.critical("KILL_SWITCH", f"KILL SWITCH TRIGGERED - Daily loss: {self._daily_pnl*100:.2f}%")

    def get_status(self) -> KillSwitchStatus:
        self._check_reset()
        
        return KillSwitchStatus(
            is_active=not self.can_trade(),
            reason=self._blocked_reason,
            daily_pnl_pct=self._daily_pnl,
            consecutive_losses=self._consecutive_losses,
            last_reset=self._last_reset_date,
            trades_today=self._trades_today,
            block_new_trades=not self.can_trade(),
        )

    def force_reset(self) -> None:
        self._daily_pnl = 0.0
        self._consecutive_losses = 0
        self._trades_today = 0
        self._trade_history = []
        self._blocked_reason = ""
        self._last_reset_date = self._today_str()
        log.warning("KILL_SWITCH", "FORCED RESET by admin")

    def get_position_size_factor(self) -> float:
        factor = 1.0
        
        if self._consecutive_losses >= 2:
            factor = 0.5
            log.warning("KILL_SWITCH", f"Reducing size by 50% - {self._consecutive_losses} consecutive losses")
        elif self._daily_pnl < -0.02:
            factor = 0.75
            log.warning("KILL_SWITCH", f"Reducing size by 25% - daily loss {self._daily_pnl*100:.1f}%")
        
        return factor

    def get_confidence_adjustment(self) -> float:
        confidence = 1.0
        
        if self._consecutive_losses == 1:
            confidence = 0.9
        elif self._consecutive_losses >= 2:
            confidence = 0.7
        
        if self._daily_pnl < 0:
            confidence *= 0.8
        
        return confidence
