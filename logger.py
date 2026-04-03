"""
logger.py — Production-grade structured JSON logging
Mandatory: python-json-logger
All output: single-line JSON to stdout (Railway-compatible)
"""

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from pythonjsonlogger import jsonlogger


class ProductionJsonFormatter(jsonlogger.JsonFormatter):
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

        ordered: dict = {}
        ordered["timestamp"]  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ordered["level"]      = record.levelname
        ordered["module"]     = record.name
        ordered["event_type"] = log_record.pop("event_type", "UNSPECIFIED")
        ordered["detail"]     = log_record.pop("message", record.getMessage())

        for field in self.OPTIONAL_FIELDS:
            value = log_record.pop(field, None)
            if value is not None:
                ordered[field] = value

        log_record.clear()
        log_record.update(ordered)

        for key in ("msg", "exc_info", "exc_text", "stack_info"):
            log_record.pop(key, None)

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        return line.replace("\n", " ").replace("\r", "")


def _build_stdout_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        ProductionJsonFormatter(
            fmt="%(timestamp)s %(level)s %(module)s %(event_type)s %(detail)s"
        )
    )
    return handler


class StructuredLogger:
    _handler: logging.StreamHandler = _build_stdout_handler()

    def __init__(self, module_name: str) -> None:
        self._logger = logging.getLogger(module_name)
        if not self._logger.handlers:
            self._logger.addHandler(self._handler)
        self._logger.propagate = False
        self._logger.setLevel(logging.DEBUG)

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

    def timed(self, event_type: str, detail: str, start: float, **kwargs: Any) -> None:
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        self.info(event_type, detail, latency_ms=latency_ms, **kwargs)

    def _emit(self, level: int, event_type: str, detail: str, **kwargs: Any) -> None:
        extra = {"event_type": event_type, **kwargs}
        self._logger.log(level, detail, extra=extra)


def get_logger(module_name: str) -> StructuredLogger:
    return StructuredLogger(module_name)
