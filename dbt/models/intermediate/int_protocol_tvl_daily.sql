{{
  config(
    materialized='view'
  )
}}

-- Canonical daily TVL series from raw snapshots
-- This is already parsed and loaded by the ingestion script,
-- so we just select and validate it here

SELECT
    date,
    protocol_id,
    tvl_usd,
    source,
    created_at
FROM {{ source('public', 'protocol_tvl_daily') }}
WHERE tvl_usd > 0  -- Basic validation
ORDER BY protocol_id, date
