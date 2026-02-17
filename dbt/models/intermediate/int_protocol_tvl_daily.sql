{{
  config(
    materialized='view'
  )
}}

SELECT
    date,
    protocol_id,
    tvl_usd,
    source,
    created_at
FROM {{ source('public', 'protocol_tvl_daily') }}
WHERE tvl_usd > 0  -- Basic validation
ORDER BY protocol_id, date
