"""
health_server.py — aiohttp HTTP health server
Endpoints:
  GET /health  — liveness probe (Railway)
  GET /status  — detailed system status
"""

import json
from datetime import datetime, timezone

from aiohttp import web

from config import config
from database import db_ping
from logger import get_logger
from websocket_client import ws_state

log = get_logger("health_server")

# ---------------------------------------------------------------------------
# Shared app state (set by main.py at startup)
# ---------------------------------------------------------------------------

app_state = {
    "started_at":          None,
    "last_scan_timestamp": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_health(request: web.Request) -> web.Response:
    """
    GET /health — simple liveness probe.
    Returns 200 if the process is alive, 503 if DB is down.
    """
    db_ok = await db_ping()
    status_code = 200 if db_ok else 503
    body = {
        "status":    "ok" if db_ok else "degraded",
        "db_status": "UP" if db_ok else "DOWN",
        "ws_status": ws_state["status"],
        "timestamp": _now_iso(),
    }
    log.debug(
        "HEALTH_CHECK_FAIL" if not db_ok else "SYSTEM_READY",
        f"/health → {status_code}",
        db_status=body["db_status"],
        ws_status=body["ws_status"],
    )
    return web.Response(
        status=status_code,
        content_type="application/json",
        text=json.dumps(body),
    )


async def handle_status(request: web.Request) -> web.Response:
    """
    GET /status — detailed operational status.
    """
    db_ok = await db_ping()
    body = {
        "ws_status":            ws_state["status"],
        "ws_connected_at":      ws_state.get("connected_at"),
        "ws_last_message_at":   ws_state.get("last_message_at"),
        "ws_reconnect_attempts": ws_state.get("reconnect_attempts", 0),
        "db_status":            "UP" if db_ok else "DOWN",
        "last_scan_timestamp":  app_state.get("last_scan_timestamp"),
        "started_at":           app_state.get("started_at"),
        "timestamp":            _now_iso(),
    }
    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(body),
    )


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    return app


async def run_health_server() -> None:
    cfg = config.health
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    log.info(
        "SYSTEM_READY",
        f"health server listening on {cfg.host}:{cfg.port}",
    )
