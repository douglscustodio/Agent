"""
alerts_dedup.py — Alert deduplication for Jarvis AI
Prevents spamming the same alert repeatedly.
"""

import asyncio
from typing import Tuple

from logger import get_logger

log = get_logger("alerts_dedup")

DEDUP_COOLDOWN_S = 3600  # 1 hour between same symbol/direction alerts


class AlertDedupStore:
    def __init__(self):
        self._cache: dict = {}
        self._lock = asyncio.Lock()
    
    async def ensure_table(self) -> None:
        """Initialize dedup storage (in-memory for now)."""
        log.info("ALERTS_DEDUP", "dedup store initialized")
    
    async def should_send(self, symbol: str, direction: str, score: float) -> Tuple[bool, str]:
        """Check if alert should be sent or suppressed due to cooldown."""
        import time
        key = f"{symbol}:{direction}"
        now = time.time()
        
        async with self._lock:
            if key in self._cache:
                last_sent, last_score = self._cache[key]
                if now - last_sent < DEDUP_COOLDOWN_S:
                    return False, f"cooldown ativo ({(now - last_sent):.0f}s)"
                if abs(score - last_score) < 5:
                    return False, "score similar ao último enviado"
            
            self._cache[key] = (now, score)
            return True, ""
    
    async def record_sent(self, symbol: str, direction: str, score: float) -> None:
        """Record that an alert was sent."""
        import time
        key = f"{symbol}:{direction}"
        self._cache[key] = (time.time(), score)
