"""
retry_utils.py — Utilitários de retry para APIs externas
Uso: @with_retry() ou await retry_async(fn, ...)
"""

import asyncio
import functools
import time
from typing import Any, Callable, Optional, Tuple, Type

from logger import get_logger

log = get_logger("retry_utils")


async def retry_async(
    fn:            Callable,
    *args,
    max_attempts:  int   = 3,
    base_delay:    float = 1.0,
    max_delay:     float = 30.0,
    exceptions:    Tuple[Type[Exception], ...] = (Exception,),
    label:         str   = "",
    **kwargs,
) -> Any:
    """
    Executa fn(*args, **kwargs) com retry exponencial.
    Lança a última exceção se todos os attempts falharem.

    Uso:
        data = await retry_async(fetch_candles, "BTC", max_attempts=3)
    """
    label = label or getattr(fn, "__name__", "fn")
    delay = base_delay

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except exceptions as exc:
            if attempt == max_attempts:
                log.error(
                    "RETRY_EXHAUSTED",
                    f"{label} falhou após {max_attempts} tentativas: {exc}",
                    latency_ms=round(delay * 1000, 0),
                )
                raise
            log.warning(
                "RETRY_ATTEMPT",
                f"{label} tentativa {attempt}/{max_attempts} falhou: {exc} — retry em {delay:.1f}s",
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


def with_retry(
    max_attempts: int   = 3,
    base_delay:   float = 1.0,
    max_delay:    float = 30.0,
    exceptions:   Tuple[Type[Exception], ...] = (Exception,),
):
    """
    Decorator de retry para corrotinas assíncronas.

    Uso:
        @with_retry(max_attempts=3, base_delay=0.5)
        async def fetch_data():
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            return await retry_async(
                fn, *args,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                exceptions=exceptions,
                label=fn.__name__,
                **kwargs,
            )
        return wrapper
    return decorator
