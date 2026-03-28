"""
demo.py — Shows every standardized event type in action.
Run: python demo.py
Expected output: one-line JSON per event, straight to stdout.
"""

import time
from logger import get_logger
from system_events import SystemEventWriter

# ── Bare StructuredLogger (stdout only) ─────────────────────────────────────
log = get_logger("websocket_client")

print("=== Direct StructuredLogger calls ===")
log.info("SYSTEM_START",      "service initialising")
log.info("WS_CONNECTED",      "stream connected to Binance",      symbol="BTCUSDT", ws_status="OPEN")
log.warning("WS_DEAD_STREAM", "no heartbeat for 30 s",            symbol="BTCUSDT", ws_status="STALE")
log.warning("WS_RECONNECTING","attempting reconnect",             symbol="BTCUSDT", reconnect_attempt=1)
log.error("WS_DISCONNECTED",  "stream dropped unexpectedly",      symbol="BTCUSDT", ws_status="CLOSED")
log.info("WS_RECONNECTED",    "stream successfully reconnected",  symbol="BTCUSDT", reconnect_attempt=1)

log.error("DB_CONNECT_FAIL",  "cannot reach postgres",            db_status="DOWN")
log.info("DB_RECOVERED",      "postgres connection restored",     db_status="UP")

log.info("ALERT_SENT",        "signal dispatched to Telegram",    alert_id="ALT-0042",
         symbol="ETHUSDT", direction="LONG", score=0.87)
log.info("ALERT_SUPPRESSED",  "duplicate signal suppressed",      alert_id="ALT-0042",
         symbol="ETHUSDT")

log.warning("NEWS_PRIMARY_FAIL",  "primary news source timed out")
log.warning("NEWS_FALLBACK_USED", "using fallback RSS feed")

log.error("HEALTH_CHECK_FAIL",    "health endpoint returned 503")
log.critical("SYSTEM_START",      "startup failed — unrecoverable state")

# Latency helper
t0 = time.monotonic()
time.sleep(0.012)          # simulate 12 ms of work
log.timed("PERFORMANCE_LOGGED", "scan cycle complete", t0, symbol="SOLUSDT")

# ── SystemEventWriter (stdout + DB dual-write, no DB configured in demo) ───
print("\n=== SystemEventWriter convenience methods ===")
writer = SystemEventWriter(db_conn=None)   # pass a real psycopg2/asyncpg conn in prod

writer.system_start()
writer.ws_connected("stream up", symbol="BNBUSDT", ws_status="OPEN")
writer.ws_dead_stream("heartbeat lost", symbol="BNBUSDT")
writer.ws_reconnecting("retry in 5 s", attempt=2, symbol="BNBUSDT")
writer.ws_reconnected("back online", attempt=2, symbol="BNBUSDT")
writer.db_connect_fail("postgres unreachable after 3 retries")
writer.db_recovered("connection pool healthy")
writer.alert_sent("LONG signal fired", alert_id="ALT-0099",
                  symbol="BTCUSDT", direction="LONG", score=0.92)
writer.alert_suppressed("cooldown active", alert_id="ALT-0099", symbol="BTCUSDT")
writer.news_primary_fail("CryptoPanic API returned 429")
writer.news_fallback_used("switched to CoinDesk RSS")
writer.health_check_fail("DB ping exceeded 5 s threshold")
writer.performance_logged("full scan in 42 ms", latency_ms=42.0, symbol="ETHUSDT")
writer.system_ready()
