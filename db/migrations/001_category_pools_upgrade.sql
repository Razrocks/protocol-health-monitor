-- Migration 001: Category-Aware Pools Upgrade
-- Adds pool-level tracking, lending metrics, expanded risk scoring
-- Safe to run multiple times (IF NOT EXISTS / IF EXISTS guards)

BEGIN;

-- ============================================================
-- 1. Extend protocols table
-- ============================================================
ALTER TABLE protocols ADD COLUMN IF NOT EXISTS yields_project VARCHAR(100);
ALTER TABLE protocols ADD COLUMN IF NOT EXISTS protocol_slug VARCHAR(100);

-- Update protocol slugs and yields_project mappings
UPDATE protocols SET
    name = 'Aave V3',
    slug = 'aave-v3',
    protocol_slug = 'aave-v3',
    yields_project = 'Aave V3',
    category = 'LENDING'
WHERE slug IN ('aave', 'aave-v3');

UPDATE protocols SET
    protocol_slug = 'sky-lending',
    category = 'LENDING'
WHERE slug = 'sky';

UPDATE protocols SET
    protocol_slug = 'uniswap-v3',
    category = 'DEX'
WHERE slug = 'uniswap-v3';

UPDATE protocols SET
    slug = 'curve-finance',
    protocol_slug = 'curve-finance',
    category = 'DEX'
WHERE slug IN ('curve', 'curve-finance');

UPDATE protocols SET
    protocol_slug = 'lido',
    category = 'LSD'
WHERE slug = 'lido';

UPDATE protocols SET
    protocol_slug = 'gmx',
    category = 'PERP'
WHERE slug = 'gmx';

-- ============================================================
-- 2. Extend ingestion_runs
-- ============================================================
ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS error_message TEXT;

-- ============================================================
-- 3. Extend alerts_daily
-- ============================================================
ALTER TABLE alerts_daily ADD COLUMN IF NOT EXISTS points INTEGER;

-- Update severity constraint to include 'crit'
ALTER TABLE alerts_daily DROP CONSTRAINT IF EXISTS valid_severity;
ALTER TABLE alerts_daily ADD CONSTRAINT valid_severity
    CHECK (severity IN ('low', 'med', 'high', 'crit'));

-- ============================================================
-- 4. Raw pools JSON storage (one row per pipeline run)
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_pools_json (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    pool_count INTEGER,
    raw_json JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_pools_json_run ON raw_pools_json(run_id);

-- ============================================================
-- 5. Current pool snapshots (parsed from yields API)
-- ============================================================
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

CREATE INDEX IF NOT EXISTS idx_pools_current_run ON pools_current(run_id);
CREATE INDEX IF NOT EXISTS idx_pools_current_protocol ON pools_current(protocol_id);
CREATE INDEX IF NOT EXISTS idx_pools_current_project ON pools_current(project);

-- ============================================================
-- 6. Pool time series (lending history for rate volatility)
-- ============================================================
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

CREATE INDEX IF NOT EXISTS idx_pool_ts_pool ON pool_timeseries_daily(pool_id);
CREATE INDEX IF NOT EXISTS idx_pool_ts_date ON pool_timeseries_daily(date);

-- ============================================================
-- 7. Protocol pool selection tracking
-- ============================================================
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

CREATE INDEX IF NOT EXISTS idx_pool_selection_date ON protocol_pool_selection_daily(date);
CREATE INDEX IF NOT EXISTS idx_pool_selection_protocol ON protocol_pool_selection_daily(protocol_id);

-- ============================================================
-- 8. Expanded risk metrics (replaces protocol_metrics_daily)
-- ============================================================
CREATE TABLE IF NOT EXISTS protocol_risk_metrics_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    protocol_id INTEGER NOT NULL REFERENCES protocols(protocol_id),
    category VARCHAR(20) NOT NULL,

    -- TVL metrics
    tvl_usd NUMERIC(20, 2),
    tvl_1d_pct NUMERIC(10, 4),
    tvl_7d_pct NUMERIC(10, 4),
    tvl_30d_pct NUMERIC(10, 4),

    -- Chain concentration
    top_chain VARCHAR(100),
    top_chain_share_pct NUMERIC(10, 4),
    top_chain_share_change_7d_pp NUMERIC(10, 4),

    -- Pool coverage
    coverage_pct NUMERIC(10, 4),
    pool_count_80 INTEGER,
    top_pool_share NUMERIC(10, 4),

    -- Lending-only metrics (NULL for non-lending)
    lending_util_avg NUMERIC(10, 4),
    lending_util_max NUMERIC(10, 4),
    lending_ratio_max NUMERIC(10, 4),
    lending_ratio_median NUMERIC(10, 4),
    lending_rate_cv_supply_30d NUMERIC(10, 4),
    lending_rate_cv_borrow_30d NUMERIC(10, 4),

    -- Risk scoring
    risk_score INTEGER DEFAULT 0,
    risk_flags JSONB,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, protocol_id),
    CONSTRAINT valid_risk_score_v2 CHECK (risk_score >= 0 AND risk_score <= 100)
);

CREATE INDEX IF NOT EXISTS idx_risk_metrics_date ON protocol_risk_metrics_daily(date);
CREATE INDEX IF NOT EXISTS idx_risk_metrics_protocol ON protocol_risk_metrics_daily(protocol_id);
CREATE INDEX IF NOT EXISTS idx_risk_metrics_score ON protocol_risk_metrics_daily(risk_score DESC);

COMMIT;
