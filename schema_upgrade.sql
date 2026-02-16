-- Add protocol_fees table for historical fees/revenue tracking
CREATE TABLE IF NOT EXISTS protocol_fees (
    fee_id SERIAL PRIMARY KEY,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    date DATE NOT NULL,
    fees_24h NUMERIC(20, 2),
    revenue_24h NUMERIC(20, 2),
    users_24h INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(protocol_id, date)
);

CREATE INDEX IF NOT EXISTS idx_protocol_fees_protocol_date ON protocol_fees(protocol_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_protocol_fees_date ON protocol_fees(date DESC);

COMMENT ON TABLE protocol_fees IS 'Daily fees and revenue data for DeFi protocols';
COMMENT ON COLUMN protocol_fees.fees_24h IS 'Total fees generated in 24h (USD)';
COMMENT ON COLUMN protocol_fees.revenue_24h IS 'Protocol revenue in 24h (USD)';
COMMENT ON COLUMN protocol_fees.users_24h IS 'Unique users in 24h';
