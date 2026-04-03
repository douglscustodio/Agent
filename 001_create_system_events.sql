-- migration: 001_create_system_events.sql
-- Creates the system_events table used by SystemEventWriter for dual-write logging.

CREATE TABLE IF NOT EXISTS system_events (
    id                 BIGSERIAL PRIMARY KEY,
    timestamp          TIMESTAMPTZ     NOT NULL,
    level              VARCHAR(10)     NOT NULL,          -- DEBUG | INFO | WARNING | ERROR | CRITICAL
    module             VARCHAR(100)    NOT NULL,
    event_type         VARCHAR(100)    NOT NULL,          -- standardised event name
    detail             TEXT            NOT NULL,

    -- Optional context fields (nullable)
    symbol             VARCHAR(20),
    direction          VARCHAR(10),
    score              NUMERIC(10, 4),
    ws_status          VARCHAR(20),
    reconnect_attempt  INTEGER,
    db_status          VARCHAR(20),
    alert_id           VARCHAR(100),
    latency_ms         NUMERIC(12, 2),

    created_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Indexes for common Railway log filter queries
CREATE INDEX IF NOT EXISTS idx_se_event_type  ON system_events (event_type);
CREATE INDEX IF NOT EXISTS idx_se_level       ON system_events (level);
CREATE INDEX IF NOT EXISTS idx_se_timestamp   ON system_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_se_symbol      ON system_events (symbol) WHERE symbol IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_se_alert_id    ON system_events (alert_id) WHERE alert_id IS NOT NULL;
