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
        seconds:  int = 0,   # UPGRADE: sub-minute intervals (e.g. flush job)
        jitter:   int = 10,
    ) -> None:
        self._scheduler.add_job(
            self._wrap(fn, name),
            trigger=IntervalTrigger(
                seconds=seconds,
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
        total_sec = hours*3600 + minutes*60 + seconds
        log.info(
            "SYSTEM_READY",
            f"job registered: '{name}' every {total_sec}s",
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
    scan_fn:           Callable,
    news_fn:           Callable,
    regime_fn:         Callable,
    performance_fn:    Callable,
    adaptive_fn:       Callable,
    market_report_fn:  Callable = None,
    macro_refresh_fn:  Callable = None,
    btc_spike_fn:      Callable = None,
    daily_summary_fn:  Callable = None,
    flush_fn:          Callable = None,    # UPGRADE: DB event buffer flush
    ai_learn_fn:       Callable = None,   # UPGRADE: AI learning suggestions
) -> AppScheduler:
    """
    Wire all application jobs into the scheduler.
    Called once from main.py after all subsystems are initialised.
    """
    sched = AppScheduler()

    # BTC regime refresh: 5 min (era 2)
    sched.add_interval_job(regime_fn,      minutes=5,  name="btc_regime",      jitter=10)

    # News fetch: 10 min (era 3)
    sched.add_interval_job(news_fn,        minutes=10,  name="news_fetch",      jitter=30)

    # Full scan + ranking: 15 min (era 5)
    sched.add_interval_job(scan_fn,        minutes=15,  name="scan_cycle",      jitter=60)

    # BTC spike check: 15 min (era 5)
    if btc_spike_fn:
        sched.add_interval_job(btc_spike_fn, minutes=15, name="btc_spike",      jitter=30)

    # Performance checks: 2 hours (era 1)
    sched.add_interval_job(performance_fn, minutes=0,  name="perf_checks",     hours=2,  jitter=120)

    # Macro intelligence refresh: 60 min (era 30)
    if macro_refresh_fn:
        sched.add_interval_job(macro_refresh_fn, minutes=60, name="macro_refresh", jitter=120)

    # Market report: 3 hours (era 1)
    if market_report_fn:
        sched.add_interval_job(market_report_fn, minutes=0, name="market_report", hours=3, jitter=300)

    # Daily summary: 24 hours
    if daily_summary_fn:
        sched.add_interval_job(daily_summary_fn, minutes=0, name="daily_summary", hours=24, jitter=300)

    # Adaptive weight update: 24 hours
    sched.add_interval_job(adaptive_fn,    minutes=0,  name="adaptive_tune",   hours=24, jitter=600)

    # UPGRADE: AI learning + trade suggestions: every 3 hours (era 2)
    if ai_learn_fn:
        sched.add_interval_job(ai_learn_fn, minutes=0, name="ai_learn_suggest", hours=3, jitter=300)

    # UPGRADE: DB event buffer flush every 15 seconds
    if flush_fn:
        sched.add_interval_job(flush_fn,   minutes=0,  name="flush_events",    seconds=15, jitter=0)

    return sched
