{{
  config(
    materialized='table'
  )
}}

WITH metrics AS (
    SELECT * FROM {{ ref('mart_protocol_metrics_daily') }}
),

alerts_exploded AS (
    SELECT
        m.date,
        m.protocol_id,
        p.name AS protocol_name,
        UNNEST(m.flags) AS alert_type,
        m.tvl_change_1d_pct,
        m.tvl_change_7d_pct,
        m.top_chain_share_pct,
        m.chain_concentration_change_7d_pp
    FROM metrics m
    JOIN {{ source('public', 'protocols') }} p ON m.protocol_id = p.protocol_id
    WHERE ARRAY_LENGTH(m.flags, 1) > 0
),

alerts_enriched AS (
    SELECT
        date,
        protocol_id,
        alert_type,
        
        CASE alert_type
            WHEN 'TVL_1D_DROP' THEN 'med'
            WHEN 'TVL_7D_DROP' THEN 'high'
            WHEN 'TOP_CHAIN_CONCENTRATION_HIGH' THEN 'med'
            WHEN 'TOP_CHAIN_CONCENTRATION_SPIKE' THEN 'high'
            WHEN 'DATA_GAP' THEN 'low'
        END AS severity,
        
        CASE alert_type
            WHEN 'TVL_1D_DROP' THEN tvl_change_1d_pct
            WHEN 'TVL_7D_DROP' THEN tvl_change_7d_pct
            WHEN 'TOP_CHAIN_CONCENTRATION_HIGH' THEN top_chain_share_pct
            WHEN 'TOP_CHAIN_CONCENTRATION_SPIKE' THEN chain_concentration_change_7d_pp
            ELSE NULL
        END AS value_numeric,
        
        CASE alert_type
            WHEN 'TVL_1D_DROP' THEN -3
            WHEN 'TVL_7D_DROP' THEN -8
            WHEN 'TOP_CHAIN_CONCENTRATION_HIGH' THEN 70
            WHEN 'TOP_CHAIN_CONCENTRATION_SPIKE' THEN 10
            ELSE NULL
        END AS threshold_numeric,
        
        CASE alert_type
            WHEN 'TVL_1D_DROP' THEN 
                protocol_name || ' TVL dropped ' || ROUND(ABS(tvl_change_1d_pct)::numeric, 2)::text || '% in 1 day'
            WHEN 'TVL_7D_DROP' THEN 
                protocol_name || ' TVL dropped ' || ROUND(ABS(tvl_change_7d_pct)::numeric, 2)::text || '% in 7 days'
            WHEN 'TOP_CHAIN_CONCENTRATION_HIGH' THEN 
                protocol_name || ' has ' || ROUND(top_chain_share_pct::numeric, 1)::text || '% TVL on single chain (high concentration)'
            WHEN 'TOP_CHAIN_CONCENTRATION_SPIKE' THEN 
                protocol_name || ' top chain concentration spiked +' || ROUND(chain_concentration_change_7d_pp::numeric, 1)::text || 'pp in 7 days'
            WHEN 'DATA_GAP' THEN 
                protocol_name || ' has missing historical data'
        END AS message
        
    FROM alerts_exploded
)

SELECT
    date,
    protocol_id,
    alert_type,
    severity,
    ROUND(value_numeric::numeric, 4) AS value_numeric,
    threshold_numeric,
    message,
    CURRENT_TIMESTAMP AS created_at
FROM alerts_enriched
ORDER BY 
    date DESC,
    CASE severity
        WHEN 'high' THEN 1
        WHEN 'med' THEN 2
        WHEN 'low' THEN 3
    END,
    protocol_id
