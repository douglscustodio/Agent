"""
scheduler.py — APScheduler loop orchestration
Loops:
  btc_regime:          every 2 min
  news:                every 3 min
  scanner + ranking:   every 5 min
  performance checks:  every 1 hour
  adaptive weights:    every 24 hours
"""

import asyncio
import time
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import write_system_event
from logger import get_logger

log = get_logger("scheduler")


class AppScheduler:
    """
    Thin wrapper around APScheduler.
    All jobs are registered via add_job() and started together.
    Errors in individual jobs are caught and logged — they never crash the loop.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    # ------------------------------------------------------------------
    # Job registration helpers
    # ------------------------------------------------------------------

    def _wrap(self, fn: Callable, job_name: str) -> Callable:
        """Wrap a coroutine with error isolation and structured logging."""
        async def _safe_job(*args, **kwargs):
            t0 = time.monotonic()
            try:
                await fn(*args, **kwargs)
                elapsed = round((time.monotonic() - t0) * 1000, 2)
                log.debug(
                    "PERFORMANCE_LOGGED",
                    f"job '{job_name}' completed in {elapsed}ms",
                    latency_ms=elapsed,
                )
            except Exception as exc:
                log.error(
                    "HEALTH_CHECK_FAIL",
                    f"job '{job_name}' raised: {exc}",
                )
                await write_system_event(
                    "HEALTH_CHECK_FAIL",
                    f"scheduler job '{job_name}' failed: {exc}",
                    level="ERROR",
                    module="scheduler",
                )
        return _safe_job

    def add_interval_job(
        self,
        fn:       Callable,
        minutes:  int,
        name:     str,
        *,
        hours:    int = 0,
        jitter:   int = 10,
    ) -> None:
        self._scheduler.add_job(
            self._wrap(fn, name),
            trigger=IntervalTrigger(
                minutes=minutes,
                hours=hours,
                jitter=jitter,
            ),
            id=name,
            name=name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        log.info(
            "SYSTEM_READY",
            f"job registered: '{name}' every {hours*60+minutes}min",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._scheduler.start()
        log.info("SYSTEM_READY", f"scheduler started — {len(self._scheduler.get_jobs())} jobs active")

    async def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        log.info("SYSTEM_START", "scheduler stopped")

    def get_job_ids(self) -> list:
        return [j.id for j in self._scheduler.get_jobs()]


# ---------------------------------------------------------------------------
# Job factory — wires concrete callables
# ---------------------------------------------------------------------------

def build_scheduler(
    scan_fn:          Callable,
    news_fn:          Callable,
    regime_fn:        Callable,
    performance_fn:   Callable,
    adaptive_fn:      Callable,
) -> AppScheduler:
    """
    Wire all application jobs into the scheduler.
    Called once from main.py after all subsystems are initialised.
    """
    sched = AppScheduler()

    # BTC regime refresh: 2 min
    sched.add_interval_job(regime_fn,      minutes=2,  name="btc_regime",    jitter=5)

    # News fetch: 3 min
    sched.add_interval_job(news_fn,        minutes=3,  name="news_fetch",    jitter=15)

    # Full scan + ranking: 5 min
    sched.add_interval_job(scan_fn,        minutes=5,  name="scan_cycle",    jitter=20)

    # Performance checks: 1 hour
    sched.add_interval_job(performance_fn, minutes=0,  name="perf_checks",   hours=1,  jitter=60)

    # Adaptive weight update: 24 hours
    sched.add_interval_job(adaptive_fn,    minutes=0,  name="adaptive_tune", hours=24, jitter=300)

    return sched
