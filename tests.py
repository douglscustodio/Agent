"""
system_events.py — Dual-write system events to stdout (JSON) + DB table
Every system event is logged to BOTH channels as required by the spec.
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from logger import get_logger

log = get_logger("system_events")


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------

@dataclass
class SystemEvent:
    event_type: str
    detail: str
    level: str = "INFO"
    module: str = "system_events"
    # Optional schema fields
    symbol: Optional[str] = None
    direction: Optional[str] = None
    score: Optional[float] = None
    ws_status: Optional[str] = None
    reconnect_attempt: Optional[int] = None
    db_status: Optional[str] = None
    alert_id: Optional[str] = None
    latency_ms: Optional[float] = None
    # Auto-set at emit time
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def optional_kwargs(self) -> dict:
        """Return only the non-None optional fields as a plain dict."""
        optional_keys = {
            "symbol", "direction", "score", "ws_status",
            "reconnect_attempt", "db_status", "alert_id", "latency_ms",
        }
        return {k: v for k, v in asdict(self).items() if k in optional_keys and v is not None}


# ---------------------------------------------------------------------------
# Dual-write emitter
# ---------------------------------------------------------------------------

class SystemEventWriter:
    """
    Writes every system event to:
      1. stdout (JSON via StructuredLogger)
      2. database table `system_events`

    Pass a live DB connection/pool at construction, or call set_db() later.
    The writer degrades gracefully: if the DB is unavailable the stdout log
    still emits and the DB error itself is logged.
    """

    _LEVEL_MAP = {
        "DEBUG":    log.debug,
        "INFO":     log.info,
        "WARNING":  log.warning,
        "ERROR":    log.error,
        "CRITICAL": log.critical,
    }

    def __init__(self, db_conn=None) -> None:
        self._db = db_conn

    def set_db(self, db_conn) -> None:
        self._db = db_conn

    # ------------------------------------------------------------------
    # Public emit
    # ------------------------------------------------------------------

    def emit(self, event: SystemEvent) -> None:
        """Dual-write: stdout JSON + DB row."""
        self._write_stdout(event)
        self._write_db(event)

    # Convenience factory methods -------------------------------------------

    def ws_connected(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("WS_CONNECTED", detail, level="INFO", **kw))

    def ws_disconnected(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("WS_DISCONNECTED", detail, level="ERROR", **kw))

    def ws_reconnecting(self, detail: str, attempt: int, **kw) -> None:
        self.emit(SystemEvent("WS_RECONNECTING", detail, level="WARNING",
                              reconnect_attempt=attempt, **kw))

    def ws_reconnected(self, detail: str, attempt: int, **kw) -> None:
        self.emit(SystemEvent("WS_RECONNECTED", detail, level="INFO",
                              reconnect_attempt=attempt, **kw))

    def ws_dead_stream(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("WS_DEAD_STREAM", detail, level="WARNING", **kw))

    def db_connect_fail(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("DB_CONNECT_FAIL", detail, level="ERROR",
                              db_status="DOWN", **kw))

    def db_recovered(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("DB_RECOVERED", detail, level="INFO",
                              db_status="UP", **kw))

    def alert_sent(self, detail: str, alert_id: str, **kw) -> None:
        self.emit(SystemEvent("ALERT_SENT", detail, level="INFO",
                              alert_id=alert_id, **kw))

    def alert_suppressed(self, detail: str, alert_id: str, **kw) -> None:
        self.emit(SystemEvent("ALERT_SUPPRESSED", detail, level="INFO",
                              alert_id=alert_id, **kw))

    def news_primary_fail(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("NEWS_PRIMARY_FAIL", detail, level="WARNING", **kw))

    def news_fallback_used(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("NEWS_FALLBACK_USED", detail, level="WARNING", **kw))

    def health_check_fail(self, detail: str, **kw) -> None:
        self.emit(SystemEvent("HEALTH_CHECK_FAIL", detail, level="ERROR", **kw))

    def performance_logged(self, detail: str, latency_ms: float, **kw) -> None:
        self.emit(SystemEvent("PERFORMANCE_LOGGED", detail, level="INFO",
                              latency_ms=latency_ms, **kw))

    def system_start(self, detail: str = "service starting", **kw) -> None:
        self.emit(SystemEvent("SYSTEM_START", detail, level="INFO", **kw))

    def system_ready(self, detail: str = "service ready", **kw) -> None:
        self.emit(SystemEvent("SYSTEM_READY", detail, level="INFO", **kw))

    # ------------------------------------------------------------------
    # Private writers
    # ------------------------------------------------------------------

    def _write_stdout(self, event: SystemEvent) -> None:
        emit_fn = self._LEVEL_MAP.get(event.level, log.info)
        emit_fn(event.event_type, event.detail, **event.optional_kwargs())

    def _write_db(self, event: SystemEvent) -> None:
        if self._db is None:
            return
        sql = """
            INSERT INTO system_events
                (timestamp, level, module, event_type, detail,
                 symbol, direction, score, ws_status, reconnect_attempt,
                 db_status, alert_id, latency_ms)
            VALUES
                (%s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s,
                 %s, %s, %s)
        """
        values = (
            event.timestamp,
            event.level,
            event.module,
            event.event_type,
            event.detail,
            event.symbol,
            event.direction,
            event.score,
            event.ws_status,
            event.reconnect_attempt,
            event.db_status,
            event.alert_id,
            event.latency_ms,
        )
        try:
            with self._db.cursor() as cur:
                cur.execute(sql, values)
            self._db.commit()
        except Exception as exc:
            # Log DB write failure to stdout only (avoid infinite loop)
            log.error(
                "DB_WRITE_FAIL",
                f"failed to persist system event: {exc}",
                event_type=event.event_type,
                db_status="DOWN",
            )
