CREATE TABLE execution_requests (
    client_id TEXT PRIMARY KEY,
    mode execution_mode NOT NULL,
    symbol TEXT NOT NULL,
    request JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN
        ('PREPARED', 'ACKNOWLEDGED', 'FILLED', 'CONFIRMED', 'UNKNOWN', 'ABORTED', 'BOOKED')),
    exchange_order_id TEXT,
    response JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP()
);
CREATE INDEX execution_requests_pending ON execution_requests(mode, created_at)
    WHERE state IN ('PREPARED', 'ACKNOWLEDGED', 'FILLED', 'UNKNOWN');
CREATE UNIQUE INDEX orders_unique_fill ON orders(mode, exchange_order_id)
    WHERE exchange_order_id IS NOT NULL AND status IN ('FILLED', 'PARTIALLY_FILLED');
ALTER TABLE execution_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON execution_requests FROM PUBLIC, anon, authenticated;
