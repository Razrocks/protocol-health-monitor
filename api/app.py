"""
Flask API for Protocol Health Monitor Dashboard
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
from datetime import datetime, date, timedelta
from decimal import Decimal


app = Flask(__name__)
CORS(app)


def get_db_connection():
    """Get PostgreSQL connection"""
    return psycopg2.connect(
        os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@postgres:5432/protocol_health'),
        cursor_factory=RealDictCursor
    )


def decimal_to_float(obj):
    """Convert Decimal to float for JSON serialization"""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (date, datetime)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_float(item) for item in obj]
    return obj


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})


@app.route('/api/run-status', methods=['GET'])
def get_run_status():
    """Get the latest pipeline run status"""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT run_id, started_at, finished_at, status, error_message, notes
        FROM ingestion_runs
        ORDER BY run_id DESC
        LIMIT 5
    """)

    runs = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(decimal_to_float(runs))


@app.route('/api/protocols', methods=['GET'])
def get_protocols():
    """Get all protocols with category and config info"""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT protocol_id, name, slug, category, protocol_slug,
               yields_project, enabled
        FROM protocols
        ORDER BY name
    """)

    protocols = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(decimal_to_float(protocols))


@app.route('/api/overview', methods=['GET'])
def get_overview():
    """Get overview with latest risk metrics for all protocols"""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT ON (p.protocol_id)
            p.protocol_id,
            p.name,
            p.slug,
            p.category,
            m.date,
            m.tvl_usd,
            m.tvl_1d_pct,
            m.tvl_7d_pct,
            m.tvl_30d_pct,
            m.top_chain,
            m.top_chain_share_pct,
            m.top_chain_share_change_7d_pp,
            m.coverage_pct,
            m.pool_count_80,
            m.top_pool_share,
            m.top_pool_share_total,
            m.lending_util_avg,
            m.lending_util_max,
            m.lending_spread_max,
            m.lending_spread_median,
            m.metric_coverage_util,
            m.metric_coverage_rate,
            m.risk_score,
            m.risk_flags
        FROM protocols p
        LEFT JOIN protocol_risk_metrics_daily m ON p.protocol_id = m.protocol_id
        WHERE p.enabled = true
        ORDER BY p.protocol_id, m.date DESC NULLS LAST
    """)

    results = cur.fetchall()

    # Get alert counts per protocol for today
    cur.execute("""
        SELECT protocol_id,
            COUNT(*) AS alert_count,
            SUM(CASE WHEN severity = 'crit' THEN 1 ELSE 0 END) AS crit_count,
            SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END) AS high_count,
            SUM(CASE WHEN severity = 'med' THEN 1 ELSE 0 END) AS med_count,
            SUM(CASE WHEN severity = 'low' THEN 1 ELSE 0 END) AS low_count
        FROM alerts_daily
        WHERE date = (SELECT MAX(date) FROM alerts_daily)
        GROUP BY protocol_id
    """)

    alert_counts = {row['protocol_id']: row for row in cur.fetchall()}

    cur.close()
    conn.close()

    # Merge alert counts into results
    output = []
    for row in results:
        row_dict = dict(row)
        pid = row_dict['protocol_id']
        row_dict['alerts'] = alert_counts.get(pid, {
            'alert_count': 0, 'crit_count': 0,
            'high_count': 0, 'med_count': 0, 'low_count': 0
        })
        output.append(row_dict)

    return jsonify(decimal_to_float(output))


@app.route('/api/protocol/<int:protocol_id>', methods=['GET'])
def get_protocol_detail(protocol_id):
    """Get detailed risk metrics for a specific protocol"""
    days = request.args.get('days', 90, type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT protocol_id, name, slug, category, protocol_slug, yields_project
        FROM protocols
        WHERE protocol_id = %s
    """, (protocol_id,))

    protocol = cur.fetchone()

    if not protocol:
        cur.close()
        conn.close()
        return jsonify({'error': 'Protocol not found'}), 404

    # Risk metrics history
    cur.execute("""
        SELECT
            date, tvl_usd, tvl_1d_pct, tvl_7d_pct, tvl_30d_pct,
            top_chain, top_chain_share_pct, top_chain_share_change_7d_pp,
            coverage_pct, pool_count_80, top_pool_share, top_pool_share_total,
            lending_util_avg, lending_util_max,
            lending_spread_max, lending_spread_median,
            lending_ratio_max, lending_ratio_median,
            lending_rate_cv_supply_30d, lending_rate_cv_borrow_30d,
            metric_coverage_util, metric_coverage_rate,
            risk_score, risk_flags
        FROM protocol_risk_metrics_daily
        WHERE protocol_id = %s
        AND date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY date DESC
    """, (protocol_id, days))

    metrics = cur.fetchall()

    # Alerts (last 14 days)
    cur.execute("""
        SELECT
            date, alert_type, severity,
            value_numeric, threshold_numeric,
            points, message
        FROM alerts_daily
        WHERE protocol_id = %s
        AND date >= CURRENT_DATE - INTERVAL '14 days'
        ORDER BY date DESC,
            CASE severity
                WHEN 'crit' THEN 0
                WHEN 'high' THEN 1
                WHEN 'med' THEN 2
                WHEN 'low' THEN 3
            END
    """, (protocol_id,))

    alerts = cur.fetchall()

    # Pool selection info
    cur.execute("""
        SELECT date, selection_method, selected_pool_ids,
               selected_pool_tvl_sum, pool_count_80,
               top_pool_share, coverage_pct,
               total_pool_tvl_usd, top1_share_total, top5_share_total, top10_share_total
        FROM protocol_pool_selection_daily
        WHERE protocol_id = %s
        ORDER BY date DESC
        LIMIT 1
    """, (protocol_id,))

    pool_selection = cur.fetchone()

    cur.close()
    conn.close()

    return jsonify(decimal_to_float({
        'protocol': protocol,
        'metrics': metrics,
        'alerts': alerts,
        'pool_selection': pool_selection
    }))


@app.route('/api/protocol/<int:protocol_id>/pools', methods=['GET'])
def get_protocol_pools(protocol_id):
    """Get selected pools with current metrics for a protocol"""
    conn = get_db_connection()
    cur = conn.cursor()

    # Get latest run_id that has pools for this protocol
    cur.execute("""
        SELECT DISTINCT run_id FROM pools_current
        WHERE protocol_id = %s
        ORDER BY run_id DESC LIMIT 1
    """, (protocol_id,))

    run_row = cur.fetchone()
    if not run_row:
        cur.close()
        conn.close()
        return jsonify([])

    run_id = run_row['run_id']

    # Get selected pool IDs from the pool selection table
    cur.execute("""
        SELECT selected_pool_ids
        FROM protocol_pool_selection_daily
        WHERE protocol_id = %s
        ORDER BY date DESC LIMIT 1
    """, (protocol_id,))

    sel_row = cur.fetchone()
    selected_ids = None
    if sel_row and sel_row['selected_pool_ids']:
        selected_ids = sel_row['selected_pool_ids']
        if isinstance(selected_ids, str):
            selected_ids = json.loads(selected_ids)

    # Build query - filter to selected pools only if we have a selection
    if selected_ids:
        cur.execute("""
            SELECT
                pc.pool_id, pc.pool_name, pc.chain, pc.project,
                pc.tvl_usd,
                COALESCE(ts.supply_apy_total, pc.supply_apy_total) AS supply_apy_total,
                pc.supply_apy_base, pc.supply_apy_reward,
                COALESCE(ts.borrow_apy_total, pc.borrow_apy_total) AS borrow_apy_total,
                pc.borrow_apy_base, pc.borrow_apy_reward,
                COALESCE(ts.total_supply_usd, pc.total_supply_usd) AS total_supply_usd,
                COALESCE(ts.total_borrow_usd, pc.total_borrow_usd) AS total_borrow_usd,
                pc.has_borrow_fields, pc.has_totals_fields,
                pc.updated_at,
                CASE WHEN COALESCE(ts.total_supply_usd, pc.total_supply_usd) > 0
                          AND COALESCE(ts.total_borrow_usd, pc.total_borrow_usd) IS NOT NULL
                     THEN COALESCE(ts.total_borrow_usd, pc.total_borrow_usd)
                          / COALESCE(ts.total_supply_usd, pc.total_supply_usd)
                     ELSE NULL
                END AS utilization
            FROM pools_current pc
            LEFT JOIN LATERAL (
                SELECT total_supply_usd, total_borrow_usd,
                       supply_apy_total, borrow_apy_total
                FROM pool_timeseries_daily ptd
                WHERE ptd.pool_id = pc.pool_id
                ORDER BY ptd.date DESC
                LIMIT 1
            ) ts ON true
            WHERE pc.run_id = %s AND pc.protocol_id = %s
              AND pc.pool_id = ANY(%s)
            ORDER BY pc.tvl_usd DESC NULLS LAST
        """, (run_id, protocol_id, selected_ids))
    else:
        # Fallback: no selection yet, return top 25 by TVL
        cur.execute("""
            SELECT
                pc.pool_id, pc.pool_name, pc.chain, pc.project,
                pc.tvl_usd,
                COALESCE(ts.supply_apy_total, pc.supply_apy_total) AS supply_apy_total,
                pc.supply_apy_base, pc.supply_apy_reward,
                COALESCE(ts.borrow_apy_total, pc.borrow_apy_total) AS borrow_apy_total,
                pc.borrow_apy_base, pc.borrow_apy_reward,
                COALESCE(ts.total_supply_usd, pc.total_supply_usd) AS total_supply_usd,
                COALESCE(ts.total_borrow_usd, pc.total_borrow_usd) AS total_borrow_usd,
                pc.has_borrow_fields, pc.has_totals_fields,
                pc.updated_at,
                CASE WHEN COALESCE(ts.total_supply_usd, pc.total_supply_usd) > 0
                          AND COALESCE(ts.total_borrow_usd, pc.total_borrow_usd) IS NOT NULL
                     THEN COALESCE(ts.total_borrow_usd, pc.total_borrow_usd)
                          / COALESCE(ts.total_supply_usd, pc.total_supply_usd)
                     ELSE NULL
                END AS utilization
            FROM pools_current pc
            LEFT JOIN LATERAL (
                SELECT total_supply_usd, total_borrow_usd,
                       supply_apy_total, borrow_apy_total
                FROM pool_timeseries_daily ptd
                WHERE ptd.pool_id = pc.pool_id
                ORDER BY ptd.date DESC
                LIMIT 1
            ) ts ON true
            WHERE pc.run_id = %s AND pc.protocol_id = %s
            ORDER BY pc.tvl_usd DESC NULLS LAST
            LIMIT 25
        """, (run_id, protocol_id))

    pools = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(decimal_to_float(pools))


@app.route('/api/protocol/<int:protocol_id>/chains', methods=['GET'])
def get_protocol_chains(protocol_id):
    """Get chain breakdown over time for a protocol"""
    days = request.args.get('days', 90, type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT date, chain, tvl_usd
        FROM protocol_chain_tvl_daily
        WHERE protocol_id = %s
        AND date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY date, tvl_usd DESC
    """, (protocol_id, days))

    chains = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(decimal_to_float(chains))


@app.route('/api/protocol/<int:protocol_id>/risk-detail', methods=['GET'])
def get_protocol_risk_detail(protocol_id):
    """Get category-specific risk detail for a protocol"""
    conn = get_db_connection()
    cur = conn.cursor()

    # Get protocol info
    cur.execute("""
        SELECT protocol_id, name, category FROM protocols WHERE protocol_id = %s
    """, (protocol_id,))
    protocol = cur.fetchone()

    if not protocol:
        cur.close()
        conn.close()
        return jsonify({'error': 'Protocol not found'}), 404

    category = protocol['category']

    # Latest risk metrics
    cur.execute("""
        SELECT * FROM protocol_risk_metrics_daily
        WHERE protocol_id = %s
        ORDER BY date DESC LIMIT 1
    """, (protocol_id,))
    latest_metrics = cur.fetchone()

    # Pool selection
    cur.execute("""
        SELECT * FROM protocol_pool_selection_daily
        WHERE protocol_id = %s
        ORDER BY date DESC LIMIT 1
    """, (protocol_id,))
    pool_selection = cur.fetchone()

    result = {
        'protocol': protocol,
        'category': category,
        'latest_metrics': latest_metrics,
        'pool_selection': pool_selection,
    }

    # Lending-specific: pool history for rate charts
    if category == 'LENDING' and pool_selection and pool_selection.get('selected_pool_ids'):
        pool_ids = pool_selection['selected_pool_ids']
        if isinstance(pool_ids, str):
            pool_ids = json.loads(pool_ids)

        cur.execute("""
            SELECT pool_id, date, supply_apy_total, borrow_apy_total,
                   total_supply_usd, total_borrow_usd
            FROM pool_timeseries_daily
            WHERE pool_id = ANY(%s)
            AND date >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY pool_id, date
        """, (pool_ids[:10],))
        result['pool_history'] = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify(decimal_to_float(result))


@app.route('/api/daily-brief', methods=['GET'])
def get_daily_brief():
    """Get daily risk brief with all alerts.
    Falls back to most recent date with alerts if requested date has none."""
    target_date = request.args.get('date', date.today().isoformat())

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            a.date,
            p.name AS protocol_name,
            p.category,
            a.alert_type,
            a.severity,
            a.value_numeric,
            a.threshold_numeric,
            a.points,
            a.message
        FROM alerts_daily a
        JOIN protocols p ON a.protocol_id = p.protocol_id
        WHERE a.date = %s
        ORDER BY
            CASE a.severity
                WHEN 'crit' THEN 0
                WHEN 'high' THEN 1
                WHEN 'med' THEN 2
                WHEN 'low' THEN 3
            END,
            p.name
    """, (target_date,))

    alerts = cur.fetchall()

    # If no alerts found for requested date, fall back to most recent date
    if not alerts and not request.args.get('date'):
        cur.execute("SELECT MAX(date) FROM alerts_daily")
        row = cur.fetchone()
        latest = row.get('max') if row else None
        if latest:
            target_date = latest.isoformat() if hasattr(latest, 'isoformat') else str(latest)
            cur.execute("""
                SELECT
                    a.date,
                    p.name AS protocol_name,
                    p.category,
                    a.alert_type,
                    a.severity,
                    a.value_numeric,
                    a.threshold_numeric,
                    a.points,
                    a.message
                FROM alerts_daily a
                JOIN protocols p ON a.protocol_id = p.protocol_id
                WHERE a.date = %s
                ORDER BY
                    CASE a.severity
                        WHEN 'crit' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'med' THEN 2
                        WHEN 'low' THEN 3
                    END,
                    p.name
            """, (target_date,))
            alerts = cur.fetchall()

    cur.execute("""
        SELECT
            COUNT(DISTINCT protocol_id) AS protocols_with_alerts,
            COUNT(*) AS total_alerts,
            SUM(CASE WHEN severity = 'crit' THEN 1 ELSE 0 END) AS crit_count,
            SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END) AS high_count,
            SUM(CASE WHEN severity = 'med' THEN 1 ELSE 0 END) AS med_count,
            SUM(CASE WHEN severity = 'low' THEN 1 ELSE 0 END) AS low_count,
            SUM(CASE WHEN alert_type IN ('LOW_COVERAGE', 'DATA_GAP') THEN 1 ELSE 0 END) AS coverage_issues
        FROM alerts_daily
        WHERE date = %s
    """, (target_date,))

    summary = cur.fetchone()

    cur.close()
    conn.close()

    return jsonify(decimal_to_float({
        'date': target_date,
        'summary': summary,
        'alerts': alerts
    }))


@app.route('/api/protocol/<int:protocol_id>/top-pools', methods=['GET'])
def get_top_pools(protocol_id):
    """Get top N pools from full pool universe for concentration panel tooltips."""
    n = request.args.get('n', 10, type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT run_id FROM pools_current
        WHERE protocol_id = %s ORDER BY run_id DESC LIMIT 1
    """, (protocol_id,))
    run_row = cur.fetchone()
    if not run_row:
        cur.close()
        conn.close()
        return jsonify([])

    cur.execute("""
        SELECT pool_name, chain, tvl_usd
        FROM pools_current
        WHERE run_id = %s AND protocol_id = %s AND tvl_usd > 0
        ORDER BY tvl_usd DESC
        LIMIT %s
    """, (run_row['run_id'], protocol_id, n))

    pools = cur.fetchall()

    # Compute total for share calculation
    cur.execute("""
        SELECT COALESCE(SUM(tvl_usd), 0) AS total
        FROM pools_current
        WHERE run_id = %s AND protocol_id = %s AND tvl_usd > 0
    """, (run_row['run_id'], protocol_id))
    total = cur.fetchone()['total']

    cur.close()
    conn.close()

    result = []
    for p in pools:
        share = float(p['tvl_usd'] / total) if total > 0 else 0
        result.append({
            'pool_name': p['pool_name'],
            'chain': p['chain'],
            'tvl_usd': float(p['tvl_usd']),
            'share': share
        })

    return jsonify(decimal_to_float({'total_pool_tvl': float(total), 'pools': result}))


@app.route('/api/tvl-history', methods=['GET'])
def get_tvl_history():
    """Get daily TVL for all enabled protocols within a window.
    Query params:
        days: lookback window (default 30)
    Returns list of {date, protocol_id, name, tvl_usd} sorted by date, protocol."""
    days = request.args.get('days', 30, type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT t.date, t.protocol_id, p.name, t.tvl_usd
        FROM protocol_tvl_daily t
        JOIN protocols p ON t.protocol_id = p.protocol_id
        WHERE p.enabled = true
          AND t.date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY t.date, p.name
    """, (days,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(decimal_to_float(rows))


@app.route('/api/alert-drivers', methods=['GET'])
def get_alert_drivers():
    """Get alert counts grouped by type + severity for a given date.
    Falls back to latest date if requested date has no data.
    Query params:
        date: target date (default: latest in DB)
        limit: top N alert types (default 6)
    Returns {date, drivers: [{alert_type, total, severities: {low, med, high, crit}, protocols: [...]}]}"""
    target_date = request.args.get('date')

    conn = get_db_connection()
    cur = conn.cursor()

    # Resolve date: use latest if not provided or empty
    if not target_date:
        cur.execute("SELECT MAX(date) FROM alerts_daily")
        row = cur.fetchone()
        target_date = row['max'].isoformat() if row and row['max'] else date.today().isoformat()

    limit = request.args.get('limit', 6, type=int)

    # Alert counts by type + severity
    cur.execute("""
        SELECT alert_type, severity, COUNT(*) AS cnt
        FROM alerts_daily
        WHERE date = %s
        GROUP BY alert_type, severity
        ORDER BY alert_type, severity
    """, (target_date,))
    type_sev_rows = cur.fetchall()

    # Protocol names per alert type
    cur.execute("""
        SELECT a.alert_type, ARRAY_AGG(DISTINCT p.name) AS protocols
        FROM alerts_daily a
        JOIN protocols p ON a.protocol_id = p.protocol_id
        WHERE a.date = %s
        GROUP BY a.alert_type
    """, (target_date,))
    proto_rows = {r['alert_type']: r['protocols'] for r in cur.fetchall()}

    cur.close()
    conn.close()

    # Build driver objects
    drivers_map = {}
    for row in type_sev_rows:
        at = row['alert_type']
        if at not in drivers_map:
            drivers_map[at] = {'alert_type': at, 'total': 0, 'severities': {'low': 0, 'med': 0, 'high': 0, 'crit': 0}, 'protocols': []}
        drivers_map[at]['total'] += row['cnt']
        drivers_map[at]['severities'][row['severity']] = row['cnt']

    for at, d in drivers_map.items():
        d['protocols'] = proto_rows.get(at, [])

    # Sort by total desc, limit
    drivers = sorted(drivers_map.values(), key=lambda x: x['total'], reverse=True)[:limit]

    return jsonify(decimal_to_float({'date': target_date, 'drivers': drivers}))


@app.route('/api/fees/<protocol_slug>', methods=['GET'])
def get_protocol_fees(protocol_slug):
    """Get historical fees data for a protocol"""
    days = request.args.get('days', 30, type=int)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            pf.date,
            pf.fees_24h,
            pf.revenue_24h,
            pf.users_24h
        FROM protocol_fees pf
        JOIN protocols p ON pf.protocol_id = p.protocol_id
        WHERE (p.slug = %s OR p.protocol_slug = %s)
            AND pf.date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY pf.date DESC
    """, (protocol_slug, protocol_slug, days))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(decimal_to_float(rows))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
