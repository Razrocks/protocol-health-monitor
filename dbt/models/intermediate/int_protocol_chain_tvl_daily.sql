{{
  config(
    materialized='view'
  )
}}

SELECT
    date,
    protocol_id,
    chain,
    tvl_usd,
    created_at
FROM {{ source('public', 'protocol_chain_tvl_daily') }}
WHERE tvl_usd > 0
ORDER BY protocol_id, date, tvl_usd DESC
