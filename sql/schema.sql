-- Protocol Health Monitor Database Schema

-- 1) Static protocol configuration
CREATE TABLE IF NOT EXISTS protocols (
    protocol_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,
    category VARCHAR(50) NOT NULL,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2) Ingestion run tracking
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id SERIAL PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR(20) NOT NULL,
    git_sha VARCHAR(40),
    notes TEXT,
    CONSTRAINT valid_status CHECK (status IN ('running', 'success', 'failed'))
);

-- 3) Raw snapshots from DeFiLlama
CREATE TABLE IF NOT EXISTS raw_protocol_snapshots (
    snapshot_id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    fetched_at TIMESTAMP NOT NULL,
    raw_json JSONB NOT NULL,
    UNIQUE(run_id, protocol_id)
);

-- 4) Daily TVL time series
CREATE TABLE IF NOT EXISTS protocol_tvl_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    tvl_usd NUMERIC(20, 2) NOT NULL,
    source VARCHAR(50) DEFAULT 'defillama',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, protocol_id)
);

-- 5) Chain breakdown daily
CREATE TABLE IF NOT EXISTS protocol_chain_tvl_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    chain VARCHAR(100) NOT NULL,
    tvl_usd NUMERIC(20, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, protocol_id, chain)
);

-- 6) Computed metrics (mart table)
CREATE TABLE IF NOT EXISTS protocol_metrics_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    tvl_usd NUMERIC(20, 2) NOT NULL,
    tvl_change_1d_pct NUMERIC(10, 4),
    tvl_change_7d_pct NUMERIC(10, 4),
    tvl_change_30d_pct NUMERIC(10, 4),
    top_chain VARCHAR(100),
    top_chain_share_pct NUMERIC(10, 4),
    chain_concentration_change_7d_pp NUMERIC(10, 4),
    flags TEXT[],
    risk_score INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, protocol_id),
    CONSTRAINT valid_risk_score CHECK (risk_score >= 0 AND risk_score <= 100)
);

-- 7) Alerts
CREATE TABLE IF NOT EXISTS alerts_daily (
    alert_id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    alert_type VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    value_numeric NUMERIC(20, 4),
    threshold_numeric NUMERIC(20, 4),
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT valid_severity CHECK (severity IN ('low', 'med', 'high'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_protocol_tvl_daily_date ON protocol_tvl_daily(date);
CREATE INDEX IF NOT EXISTS idx_protocol_tvl_daily_protocol ON protocol_tvl_daily(protocol_id);
CREATE INDEX IF NOT EXISTS idx_protocol_chain_tvl_daily_date ON protocol_chain_tvl_daily(date);
CREATE INDEX IF NOT EXISTS idx_protocol_metrics_daily_date ON protocol_metrics_daily(date);
CREATE INDEX IF NOT EXISTS idx_protocol_metrics_daily_risk ON protocol_metrics_daily(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_daily_date ON alerts_daily(date);
CREATE INDEX IF NOT EXISTS idx_alerts_daily_severity ON alerts_daily(severity);

-- Insert initial protocol configuration
INSERT INTO protocols (name, slug, category, enabled) VALUES
    ('Aave', 'aave', 'lending', true),
    ('Sky', 'sky', 'lending', true),
    ('Uniswap V3', 'uniswap-v3', 'dex', true),
    ('Curve', 'curve', 'dex', true),
    ('Lido', 'lido', 'lsd', true),
    ('GMX', 'gmx', 'perp', true)
ON CONFLICT (slug) DO NOTHING;
