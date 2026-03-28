"""
logger.py — Production-grade structured JSON logging
Requirements: pip install python-json-logger
Compatible with Railway log ingestion, filtering, and alerting.
"""

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from pythonjsonlogger import jsonlogger


# ---------------------------------------------------------------------------
# Custom JSON Formatter
# ---------------------------------------------------------------------------

class ProductionJsonFormatter(jsonlogger.JsonFormatter):
    """
    Enforces the mandatory log schema:
      timestamp, level, module, event_type, detail
    Plus optional fields: symbol, direction, score, ws_status,
      reconnect_attempt, db_status, alert_id, latency_ms
    All output is single-line JSON — Railway-compatible.
    """

    OPTIONAL_FIELDS = {
        "symbol", "direction", "score", "ws_status",
        "reconnect_attempt", "db_status", "alert_id", "latency_ms",
    }

    def add_fields(
        self,
        log_record: dict,
        record: logging.LogRecord,
        message_dict: dict,
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        # --- Mandatory fields (strict order for readability) ---
        ordered: dict = {}
        ordered["timestamp"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        ordered["level"]      = record.levelname
        ordered["module"]     = record.name
        ordered["event_type"] = log_record.pop("event_type", "UNSPECIFIED")
        ordered["detail"]     = log_record.pop("message", record.getMessage())

        # --- Optional fields (only emitted when present) ---
        for field in self.OPTIONAL_FIELDS:
            value = log_record.pop(field, None)
            if value is not None:
                ordered[field] = value

        # Replace log_record contents with our ordered schema
        log_record.clear()
        log_record.update(ordered)

        # Drop internal python logging noise
        for key in ("msg", "exc_info", "exc_text", "stack_info"):
            log_record.pop(key, None)

    def format(self, record: logging.LogRecord) -> str:
        # Guarantee single-line output — no embedded newlines
        line = super().format(record)
        return line.replace("\n", " ").replace("\r", "")


# ---------------------------------------------------------------------------
# Logger Factory
# ---------------------------------------------------------------------------

def get_logger(module_name: str) -> "StructuredLogger":
    """
    Return a StructuredLogger for the given module name.
    Calling this multiple times with the same name is safe (idempotent).
    """
    return StructuredLogger(module_name)


def _build_stdout_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        ProductionJsonFormatter(
            fmt="%(timestamp)s %(level)s %(module)s %(event_type)s %(detail)s"
        )
    )
    return handler


# ---------------------------------------------------------------------------
# Structured Logger Wrapper
# ---------------------------------------------------------------------------

class StructuredLogger:
    """
    Thin wrapper around stdlib Logger that enforces the mandatory schema.

    Usage:
        log = get_logger("websocket_client")
        log.info("WS_CONNECTED",    "stream connected to Binance", symbol="BTCUSDT")
        log.warning("WS_DEAD_STREAM", "no heartbeat for 30 s",   symbol="BTCUSDT")
        log.error("DB_CONNECT_FAIL", "cannot reach postgres",     db_status="DOWN")

    Latency helper:
        t0 = time.monotonic()
        ... # do work
        log.timed("PERFORMANCE_LOGGED", "scan complete", t0, symbol="ETHUSDT")
    """

    _handler: logging.StreamHandler = _build_stdout_handler()

    def __init__(self, module_name: str) -> None:
        self._logger = logging.getLogger(module_name)
        if not self._logger.handlers:
            self._logger.addHandler(self._handler)
        self._logger.propagate = False
        self._logger.setLevel(logging.DEBUG)

    # ------------------------------------------------------------------
    # Public log-level methods
    # ------------------------------------------------------------------

    def debug(self, event_type: str, detail: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, event_type, detail, **kwargs)

    def info(self, event_type: str, detail: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, event_type, detail, **kwargs)

    def warning(self, event_type: str, detail: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, event_type, detail, **kwargs)

    def error(self, event_type: str, detail: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, event_type, detail, **kwargs)

    def critical(self, event_type: str, detail: str, **kwargs: Any) -> None:
        self._emit(logging.CRITICAL, event_type, detail, **kwargs)

    # ------------------------------------------------------------------
    # Latency helper
    # ------------------------------------------------------------------

    def timed(
        self,
        event_type: str,
        detail: str,
        start: float,
        **kwargs: Any,
    ) -> None:
        """Emit an INFO log with latency_ms auto-computed from time.monotonic() start."""
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        self.info(event_type, detail, latency_ms=latency_ms, **kwargs)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(
        self,
        level: int,
        event_type: str,
        detail: str,
        **kwargs: Any,
    ) -> None:
        extra = {"event_type": event_type, **kwargs}
        self._logger.log(level, detail, extra=extra)
