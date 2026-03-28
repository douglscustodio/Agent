"""
main.py — Phase 1 entry point
Starts: database pool → health server → WebSocket client
Handles graceful shutdown on SIGINT / SIGTERM
"""

import asyncio
import signal
from datetime import datetime, timezone

from config import config
from database import init_db, close_db, write_system_event
from health_server import run_health_server, app_state
from logger import get_logger
from websocket_client import run_websocket_client

log = get_logger("main")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_event = asyncio.Event()


def _handle_signal(sig) -> None:
    log.warning("SYSTEM_START", f"received signal {sig.name} — initiating shutdown")
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("SYSTEM_START", "=== Phase 1 starting ===")
    app_state["started_at"] = _now_iso()

    # Register OS signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # 1. Database
    try:
        await init_db()
        await write_system_event(
            "SYSTEM_START", "database initialised",
            level="INFO", module="main",
        )
    except RuntimeError as exc:
        log.critical("SYSTEM_START", f"database startup failed: {exc}")
        return

    # 2. Health server
    await run_health_server()
    await write_system_event(
        "SYSTEM_READY", "health server ready",
        level="INFO", module="main",
    )

    # 3. WebSocket client (background task)
    ws_task = asyncio.create_task(run_websocket_client())

    log.info("SYSTEM_READY", "all subsystems running")
    await write_system_event(
        "SYSTEM_READY", "all subsystems running",
        level="INFO", module="main",
    )

    # 4. Wait until shutdown signal
    await _shutdown_event.wait()

    # 5. Graceful teardown
    log.info("SYSTEM_START", "shutting down gracefully")
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

    await close_db()
    log.info("SYSTEM_START", "shutdown complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
