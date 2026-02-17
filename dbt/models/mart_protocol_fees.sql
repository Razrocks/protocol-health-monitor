WITH daily_fees AS (
    SELECT
        pf.protocol_id,
        p.name as protocol_name,
        p.slug,
        pf.date,
        pf.fees_24h,
        pf.revenue_24h,
        pf.users_24h,
        AVG(pf.fees_24h) OVER (
            PARTITION BY pf.protocol_id 
            ORDER BY pf.date 
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) as fees_7d_ma,
        AVG(pf.fees_24h) OVER (
            PARTITION BY pf.protocol_id 
            ORDER BY pf.date 
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) as fees_30d_ma,
        SUM(pf.fees_24h) OVER (
            PARTITION BY pf.protocol_id 
            ORDER BY pf.date 
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) as fees_7d_total,
        SUM(pf.fees_24h) OVER (
            PARTITION BY pf.protocol_id 
            ORDER BY pf.date 
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) as fees_30d_total,
        SUM(pf.revenue_24h) OVER (
            PARTITION BY pf.protocol_id 
            ORDER BY pf.date 
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) as revenue_30d_total
    FROM protocol_fees pf
    JOIN protocols p ON pf.protocol_id = p.protocol_id
    WHERE p.enabled = true
),

latest_metrics AS (
    SELECT
        df.*,
        m.tvl_usd,
        CASE 
            WHEN df.fees_7d_ma > df.fees_30d_ma THEN 'INCREASING'
            WHEN df.fees_7d_ma < df.fees_30d_ma * 0.95 THEN 'DECREASING'
            ELSE 'STABLE'
        END as fees_trend,
        CASE 
            WHEN df.fees_30d_total > 0 THEN m.tvl_usd / df.fees_30d_total
            ELSE NULL
        END as tvl_to_fees_ratio,
        CASE 
            WHEN m.tvl_usd / NULLIF(df.fees_30d_total, 0) < 1000 THEN 'HIGH'
            WHEN m.tvl_usd / NULLIF(df.fees_30d_total, 0) < 5000 THEN 'MEDIUM'
            ELSE 'LOW'
        END as sustainability_rating
    FROM daily_fees df
    LEFT JOIN LATERAL (
        SELECT tvl_usd
        FROM protocol_tvl_daily
        WHERE protocol_id = df.protocol_id
        ORDER BY date DESC
        LIMIT 1
    ) m ON true
)

SELECT
    protocol_id,
    protocol_name,
    slug,
    date,
    fees_24h,
    revenue_24h,
    users_24h,
    fees_7d_ma,
    fees_30d_ma,
    fees_7d_total,
    fees_30d_total,
    revenue_30d_total,
    tvl_usd,
    fees_trend,
    tvl_to_fees_ratio,
    sustainability_rating,
    CASE 
        WHEN tvl_usd > 0 AND revenue_30d_total > 0 
        THEN (revenue_30d_total / tvl_usd) * 100 * 12
        ELSE NULL
    END as annualized_revenue_pct
FROM latest_metrics
ORDER BY date DESC, protocol_name
