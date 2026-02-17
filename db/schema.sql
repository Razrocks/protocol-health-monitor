-- Protocol Health Monitor - Full Database Schema
-- Use this for fresh deployments. For upgrades, use db/migrations/

CREATE TABLE IF NOT EXISTS protocols (
    protocol_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,
    category VARCHAR(50) NOT NULL,
    protocol_slug VARCHAR(100),
    yields_project VARCHAR(100),
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id SERIAL PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR(20) NOT NULL,
    git_sha VARCHAR(40),
    notes TEXT,
    error_message TEXT,
    CONSTRAINT valid_status CHECK (status IN ('running', 'success', 'failed'))
);

CREATE TABLE IF NOT EXISTS raw_protocol_snapshots (
    snapshot_id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    fetched_at TIMESTAMP NOT NULL,
    raw_json JSONB NOT NULL,
    UNIQUE(run_id, protocol_id)
);

CREATE TABLE IF NOT EXISTS protocol_tvl_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    tvl_usd NUMERIC(20, 2) NOT NULL,
    source VARCHAR(50) DEFAULT 'defillama',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, protocol_id)
);

CREATE TABLE IF NOT EXISTS protocol_chain_tvl_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    chain VARCHAR(100) NOT NULL,
    tvl_usd NUMERIC(20, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, protocol_id, chain)
);

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

CREATE TABLE IF NOT EXISTS alerts_daily (
    alert_id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    alert_type VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    value_numeric NUMERIC(20, 4),
    threshold_numeric NUMERIC(20, 4),
    points INTEGER,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT valid_severity CHECK (severity IN ('low', 'med', 'high', 'crit'))
);

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

CREATE TABLE IF NOT EXISTS raw_pools_json (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    pool_count INTEGER,
    raw_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS pools_current (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    pool_id VARCHAR(200) NOT NULL,
    protocol_id INTEGER REFERENCES protocols(protocol_id),
    pool_name VARCHAR(300),
    chain VARCHAR(100),
    project VARCHAR(100),
    tvl_usd NUMERIC(20, 2),
    supply_apy_total NUMERIC(10, 4),
    supply_apy_base NUMERIC(10, 4),
    supply_apy_reward NUMERIC(10, 4),
    borrow_apy_total NUMERIC(10, 4),
    borrow_apy_base NUMERIC(10, 4),
    borrow_apy_reward NUMERIC(10, 4),
    total_supply_usd NUMERIC(20, 2),
    total_borrow_usd NUMERIC(20, 2),
    has_borrow_fields BOOLEAN DEFAULT false,
    has_totals_fields BOOLEAN DEFAULT false,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, pool_id)
);

CREATE TABLE IF NOT EXISTS pool_timeseries_daily (
    id SERIAL PRIMARY KEY,
    pool_id VARCHAR(200) NOT NULL,
    date DATE NOT NULL,
    supply_apy_total NUMERIC(10, 4),
    borrow_apy_total NUMERIC(10, 4),
    total_supply_usd NUMERIC(20, 2),
    total_borrow_usd NUMERIC(20, 2),
    UNIQUE(pool_id, date)
);

CREATE TABLE IF NOT EXISTS protocol_pool_selection_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    selection_method VARCHAR(50) DEFAULT 'TOP_TVL_TO_80',
    selected_pool_ids JSONB,
    selected_pool_tvl_sum NUMERIC(20, 2),
    pool_count_80 INTEGER,
    top_pool_share NUMERIC(10, 4),
    coverage_pct NUMERIC(10, 4),
    UNIQUE(date, protocol_id)
);

CREATE TABLE IF NOT EXISTS protocol_risk_metrics_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    category VARCHAR(20) NOT NULL,
    tvl_usd NUMERIC(20, 2),
    tvl_1d_pct NUMERIC(10, 4),
    tvl_7d_pct NUMERIC(10, 4),
    tvl_30d_pct NUMERIC(10, 4),
    top_chain VARCHAR(100),
    top_chain_share_pct NUMERIC(10, 4),
    top_chain_share_change_7d_pp NUMERIC(10, 4),
    coverage_pct NUMERIC(10, 4),
    pool_count_80 INTEGER,
    top_pool_share NUMERIC(10, 4),
    lending_util_avg NUMERIC(10, 4),
    lending_util_max NUMERIC(10, 4),
    lending_ratio_max NUMERIC(10, 4),
    lending_ratio_median NUMERIC(10, 4),
    lending_rate_cv_supply_30d NUMERIC(10, 4),
    lending_rate_cv_borrow_30d NUMERIC(10, 4),
    risk_score INTEGER DEFAULT 0,
    risk_flags JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, protocol_id),
    CONSTRAINT valid_risk_score_v2 CHECK (risk_score >= 0 AND risk_score <= 100)
);

CREATE INDEX IF NOT EXISTS idx_protocol_tvl_daily_date ON protocol_tvl_daily(date);
CREATE INDEX IF NOT EXISTS idx_protocol_tvl_daily_protocol ON protocol_tvl_daily(protocol_id);
CREATE INDEX IF NOT EXISTS idx_protocol_chain_tvl_daily_date ON protocol_chain_tvl_daily(date);
CREATE INDEX IF NOT EXISTS idx_protocol_metrics_daily_date ON protocol_metrics_daily(date);
CREATE INDEX IF NOT EXISTS idx_protocol_metrics_daily_risk ON protocol_metrics_daily(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_daily_date ON alerts_daily(date);
CREATE INDEX IF NOT EXISTS idx_alerts_daily_severity ON alerts_daily(severity);
CREATE INDEX IF NOT EXISTS idx_protocol_fees_date ON protocol_fees(protocol_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_raw_pools_json_run ON raw_pools_json(run_id);
CREATE INDEX IF NOT EXISTS idx_pools_current_run ON pools_current(run_id);
CREATE INDEX IF NOT EXISTS idx_pools_current_protocol ON pools_current(protocol_id);
CREATE INDEX IF NOT EXISTS idx_pools_current_project ON pools_current(project);
CREATE INDEX IF NOT EXISTS idx_pool_ts_pool ON pool_timeseries_daily(pool_id);
CREATE INDEX IF NOT EXISTS idx_pool_ts_date ON pool_timeseries_daily(date);
CREATE INDEX IF NOT EXISTS idx_pool_selection_date ON protocol_pool_selection_daily(date);
CREATE INDEX IF NOT EXISTS idx_pool_selection_protocol ON protocol_pool_selection_daily(protocol_id);
CREATE INDEX IF NOT EXISTS idx_risk_metrics_date ON protocol_risk_metrics_daily(date);
CREATE INDEX IF NOT EXISTS idx_risk_metrics_protocol ON protocol_risk_metrics_daily(protocol_id);
CREATE INDEX IF NOT EXISTS idx_risk_metrics_score ON protocol_risk_metrics_daily(risk_score DESC);

INSERT INTO protocols (name, slug, category, protocol_slug, yields_project, enabled) VALUES
    ('Aave V3', 'aave-v3', 'LENDING', 'aave-v3', 'Aave V3', true),
    ('Sky', 'sky', 'LENDING', 'sky-lending', NULL, true),
    ('Uniswap V3', 'uniswap-v3', 'DEX', 'uniswap-v3', NULL, true),
    ('Curve', 'curve-finance', 'DEX', 'curve-finance', NULL, true),
    ('Lido', 'lido', 'LSD', 'lido', NULL, true),
    ('GMX', 'gmx', 'PERP', 'gmx', NULL, true)
ON CONFLICT (slug) DO NOTHING;
