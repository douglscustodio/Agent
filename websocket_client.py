"""
websocket_client.py — Hyperliquid WebSocket client skeleton
- Exponential backoff reconnect
- Heartbeat ping every 30 s
- Dead-stream detection
- Dual-write events to stdout + DB
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from config import config
from database import write_system_event
from logger import get_logger

log = get_logger("websocket_client")

# ---------------------------------------------------------------------------
# Shared state (read by health server)
# ---------------------------------------------------------------------------

ws_state = {
    "status":              "DISCONNECTED",
    "last_message_at":     None,   # ISO-8601 string
    "reconnect_attempts":  0,
    "connected_at":        None,
}

ws_price_cache: dict = {}   # symbol -> price, populated by on_market_data


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Message handler (stub — Phase 2 will fill this)
# ---------------------------------------------------------------------------

async def _handle_message(raw: str) -> None:
    """
    Process a raw WebSocket message.
    Phase 1: parse JSON and log receipt only.
    Phase 2: route to scanner/signal logic.
    """
    ws_state["last_message_at"] = _now_iso()
    try:
        data = json.loads(raw)
        log.debug(
            "WS_CONNECTED",
            "message received",
            ws_status="OPEN",
        )
        # TODO Phase 2: dispatch data to scanner
        _ = data
    except json.JSONDecodeError as exc:
        log.warning("WS_CONNECTED", f"non-JSON frame received: {exc}", ws_status="OPEN")


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

async def _heartbeat(ws) -> None:
    """Send a ping every WS_HEARTBEAT_INTERVAL seconds."""
    interval = config.ws.heartbeat_interval
    while True:
        await asyncio.sleep(interval)
        try:
            ping_payload = json.dumps({"method": "ping"})
            await ws.send(ping_payload)
            log.debug("WS_CONNECTED", "heartbeat sent", ws_status="OPEN")
        except Exception:
            break  # connection gone — outer loop will reconnect


# ---------------------------------------------------------------------------
# Dead-stream watchdog
# ---------------------------------------------------------------------------

async def _dead_stream_watchdog() -> None:
    """
    Detect stale streams: if no message arrives within dead_stream_timeout,
    log WS_DEAD_STREAM. The outer reconnect loop handles recovery.
    """
    timeout = config.ws.dead_stream_timeout
    while True:
        await asyncio.sleep(timeout)
        last = ws_state.get("last_message_at")
        if last is None:
            continue
        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
        if elapsed >= timeout:
            log.warning(
                "WS_DEAD_STREAM",
                f"no message for {elapsed:.0f}s — stream may be dead",
                ws_status="STALE",
            )
            await write_system_event(
                "WS_DEAD_STREAM",
                f"no message for {elapsed:.0f}s",
                level="WARNING",
                module="websocket_client",
                ws_status="STALE",
            )


# ---------------------------------------------------------------------------
# Subscription payload (Hyperliquid)
# ---------------------------------------------------------------------------

def _build_subscription() -> str:
    """
    Hyperliquid subscription message.
    Phase 1: subscribe to allMids (all mid-prices).
    Phase 2: extend with specific coin/channel subscriptions.
    """
    return json.dumps({
        "method": "subscribe",
        "subscription": {"type": "allMids"},
    })


# ---------------------------------------------------------------------------
# Single connection lifecycle
# ---------------------------------------------------------------------------

async def _connect_and_listen() -> None:
    url = config.ws.url
    log.info("WS_RECONNECTING", f"connecting to {url}", ws_status="CONNECTING")

    async with websockets.connect(
        url,
        ping_interval=None,   # we manage heartbeat manually
        ping_timeout=None,
        close_timeout=10,
    ) as ws:
        ws_state["status"]      = "CONNECTED"
        ws_state["connected_at"] = _now_iso()
        ws_state["last_message_at"] = _now_iso()

        log.info("WS_CONNECTED", f"connected to {url}", ws_status="OPEN")
        await write_system_event(
            "WS_CONNECTED", f"connected to {url}",
            level="INFO", module="websocket_client", ws_status="OPEN",
        )

        # Send subscription
        await ws.send(_build_subscription())
        log.info("WS_CONNECTED", "subscription sent", ws_status="OPEN")

        # Start heartbeat as background task
        hb_task = asyncio.create_task(_heartbeat(ws))

        try:
            async for raw in ws:
                await _handle_message(raw)
        finally:
            hb_task.cancel()
            ws_state["status"] = "DISCONNECTED"


# ---------------------------------------------------------------------------
# Reconnect loop with exponential backoff
# ---------------------------------------------------------------------------

async def run_websocket_client() -> None:
    """
    Main entry point. Runs forever, reconnecting with exponential backoff.
    Spawns dead-stream watchdog as a background task.
    """
    cfg = config.ws
    base   = cfg.reconnect_base_delay
    cap    = cfg.reconnect_max_delay
    max_attempts = cfg.reconnect_max_attempts  # 0 = unlimited

    # Start dead-stream watchdog once
    asyncio.create_task(_dead_stream_watchdog())

    attempt = 0
    delay   = base

    while True:
        attempt += 1
        ws_state["reconnect_attempts"] = attempt

        if attempt > 1:
            log.warning(
                "WS_RECONNECTING",
                f"reconnect attempt {attempt}, waiting {delay:.1f}s",
                ws_status="RECONNECTING",
                reconnect_attempt=attempt,
            )
            await write_system_event(
                "WS_RECONNECTING",
                f"reconnect attempt {attempt}",
                level="WARNING",
                module="websocket_client",
                ws_status="RECONNECTING",
                reconnect_attempt=attempt,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, cap)

        try:
            await _connect_and_listen()
            # Clean reconnect (server closed gracefully) — reset backoff
            delay = base
            log.info(
                "WS_RECONNECTED",
                "stream reconnected successfully",
                ws_status="OPEN",
                reconnect_attempt=attempt,
            )
            await write_system_event(
                "WS_RECONNECTED",
                "stream reconnected successfully",
                level="INFO",
                module="websocket_client",
                ws_status="OPEN",
                reconnect_attempt=attempt,
            )

        except (ConnectionClosed, WebSocketException) as exc:
            log.error(
                "WS_DISCONNECTED",
                f"stream disconnected: {exc}",
                ws_status="CLOSED",
            )
            await write_system_event(
                "WS_DISCONNECTED",
                f"stream disconnected: {exc}",
                level="ERROR",
                module="websocket_client",
                ws_status="CLOSED",
            )

        except Exception as exc:
            log.error(
                "WS_DISCONNECTED",
                f"unexpected error: {exc}",
                ws_status="CLOSED",
            )

        if max_attempts and attempt >= max_attempts:
            log.critical(
                "WS_DISCONNECTED",
                f"max reconnect attempts ({max_attempts}) reached — giving up",
                ws_status="CLOSED",
            )
            raise RuntimeError("WebSocket max reconnect attempts reached.")
