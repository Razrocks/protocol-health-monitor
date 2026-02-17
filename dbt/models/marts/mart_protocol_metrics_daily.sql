{{
  config(
    materialized='table'
  )
}}

WITH tvl_series AS (
    SELECT * FROM {{ ref('int_protocol_tvl_daily') }}
),

tvl_with_lags AS (
    SELECT
        date,
        protocol_id,
        tvl_usd,
        LAG(tvl_usd, 1) OVER (PARTITION BY protocol_id ORDER BY date) AS tvl_1d_ago,
        LAG(tvl_usd, 7) OVER (PARTITION BY protocol_id ORDER BY date) AS tvl_7d_ago,
        LAG(tvl_usd, 30) OVER (PARTITION BY protocol_id ORDER BY date) AS tvl_30d_ago
    FROM tvl_series
),

tvl_changes AS (
    SELECT
        date,
        protocol_id,
        tvl_usd,
        CASE 
            WHEN tvl_1d_ago > 0 THEN ((tvl_usd / tvl_1d_ago) - 1) * 100
            ELSE NULL
        END AS tvl_change_1d_pct,
        CASE 
            WHEN tvl_7d_ago > 0 THEN ((tvl_usd / tvl_7d_ago) - 1) * 100
            ELSE NULL
        END AS tvl_change_7d_pct,
        CASE 
            WHEN tvl_30d_ago > 0 THEN ((tvl_usd / tvl_30d_ago) - 1) * 100
            ELSE NULL
        END AS tvl_change_30d_pct,
        tvl_1d_ago,
        tvl_7d_ago
    FROM tvl_with_lags
),

chain_totals AS (
    SELECT
        date,
        protocol_id,
        chain,
        tvl_usd,
        SUM(tvl_usd) OVER (PARTITION BY date, protocol_id) AS total_tvl,
        ROW_NUMBER() OVER (PARTITION BY date, protocol_id ORDER BY tvl_usd DESC) AS chain_rank
    FROM {{ ref('int_protocol_chain_tvl_daily') }}
),

top_chains AS (
    SELECT
        date,
        protocol_id,
        chain AS top_chain,
        CASE 
            WHEN total_tvl > 0 THEN (tvl_usd / total_tvl) * 100
            ELSE NULL
        END AS top_chain_share_pct
    FROM chain_totals
    WHERE chain_rank = 1
),

top_chain_with_lag AS (
    SELECT
        date,
        protocol_id,
        top_chain,
        top_chain_share_pct,
        LAG(top_chain_share_pct, 7) OVER (PARTITION BY protocol_id ORDER BY date) AS top_chain_share_7d_ago
    FROM top_chains
),

chain_concentration AS (
    SELECT
        date,
        protocol_id,
        top_chain,
        top_chain_share_pct,
        top_chain_share_pct - COALESCE(top_chain_share_7d_ago, top_chain_share_pct) AS chain_concentration_change_7d_pp
    FROM top_chain_with_lag
),

metrics_with_flags AS (
    SELECT
        tc.date,
        tc.protocol_id,
        tc.tvl_usd,
        tc.tvl_change_1d_pct,
        tc.tvl_change_7d_pct,
        tc.tvl_change_30d_pct,
        cc.top_chain,
        cc.top_chain_share_pct,
        cc.chain_concentration_change_7d_pp,
        
        ARRAY_REMOVE(ARRAY[
            CASE WHEN tc.tvl_change_1d_pct <= -3 THEN 'TVL_1D_DROP' END,
            CASE WHEN tc.tvl_change_7d_pct <= -8 THEN 'TVL_7D_DROP' END,
            CASE WHEN cc.top_chain_share_pct >= 70 THEN 'TOP_CHAIN_CONCENTRATION_HIGH' END,
            CASE WHEN cc.chain_concentration_change_7d_pp >= 10 THEN 'TOP_CHAIN_CONCENTRATION_SPIKE' END,
            CASE WHEN tc.tvl_1d_ago IS NULL OR tc.tvl_7d_ago IS NULL THEN 'DATA_GAP' END
        ], NULL) AS flags,
        
        LEAST(
            COALESCE(CASE WHEN tc.tvl_change_7d_pct <= -8 THEN 40 ELSE 0 END, 0) +
            COALESCE(CASE WHEN tc.tvl_change_1d_pct <= -3 THEN 20 ELSE 0 END, 0) +
            COALESCE(CASE WHEN cc.top_chain_share_pct >= 70 THEN 15 ELSE 0 END, 0) +
            COALESCE(CASE WHEN cc.chain_concentration_change_7d_pp >= 10 THEN 25 ELSE 0 END, 0) +
            COALESCE(CASE WHEN tc.tvl_1d_ago IS NULL OR tc.tvl_7d_ago IS NULL THEN 10 ELSE 0 END, 0),
            100
        ) AS risk_score
        
    FROM tvl_changes tc
    LEFT JOIN chain_concentration cc
        ON tc.date = cc.date
        AND tc.protocol_id = cc.protocol_id
)

SELECT
    date,
    protocol_id,
    tvl_usd,
    ROUND(tvl_change_1d_pct::numeric, 4) AS tvl_change_1d_pct,
    ROUND(tvl_change_7d_pct::numeric, 4) AS tvl_change_7d_pct,
    ROUND(tvl_change_30d_pct::numeric, 4) AS tvl_change_30d_pct,
    top_chain,
    ROUND(top_chain_share_pct::numeric, 4) AS top_chain_share_pct,
    ROUND(chain_concentration_change_7d_pp::numeric, 4) AS chain_concentration_change_7d_pp,
    flags,
    risk_score,
    CURRENT_TIMESTAMP AS created_at
FROM metrics_with_flags
