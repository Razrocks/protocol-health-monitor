"""
Metric normalization and computation (Steps G + H)
Computes lending metrics, TVL deltas, chain concentration
"""
import statistics
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple


# Materiality thresholds
MATERIAL_SHARE_THRESHOLD = 0.05       # 5% of selected TVL
MATERIAL_TVL_THRESHOLD = 250_000_000  # $250M absolute


def compute_lending_metrics(conn, protocol_id, pool_selections):
    """Step G: Compute lending-specific metrics for a LENDING protocol.
    Returns dict with lending_util_avg, lending_util_max, lending_spread_max,
    lending_spread_median, rate CVs, per-pool detail, metric coverage, and data flags."""

    result = {
        'lending_util_avg': None,
        'lending_util_max': None,
        # New: spread replaces ratio
        'lending_spread_max': None,
        'lending_spread_median': None,
        # Kept for backward compat but gated
        'lending_ratio_max': None,
        'lending_ratio_median': None,
        'lending_rate_cv_supply_30d': None,
        'lending_rate_cv_borrow_30d': None,
        # New: materiality + coverage
        'pool_details': [],          # per-pool breakdown for alerts
        'metric_coverage_util': None,  # % of selected TVL with util data
        'metric_coverage_rate': None,  # % of selected TVL with rate data
        'flags': []
    }

    selection = pool_selections.get(protocol_id)
    if not selection:
        return result

    selected_pool_ids = selection.get('selected_pool_ids', [])
    if not selected_pool_ids:
        return result

    selected_tvl_sum = float(selection.get('selected_pool_tvl_sum', 0) or 0)

    # --- Utilization & APY data from pool_timeseries_daily (chartLendBorrow) ---
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (pool_id)
                      pool_id, total_supply_usd, total_borrow_usd,
                      supply_apy_total, borrow_apy_total
               FROM pool_timeseries_daily
               WHERE pool_id = ANY(%s)
               ORDER BY pool_id, date DESC""",
            (selected_pool_ids,)
        )
        ts_rows = cur.fetchall()

    # Also get pool TVL + name from pools_current for weighting + messages
    pool_info = {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT pool_id, tvl_usd, pool_name FROM pools_current
               WHERE pool_id = ANY(%s)""",
            (selected_pool_ids,)
        )
        for row in cur.fetchall():
            pool_info[row[0]] = {
                'tvl': float(row[1]) if row[1] else 0,
                'name': row[2] or row[0][:20]
            }

    # Check data availability from timeseries
    any_totals = any(r[1] is not None and r[2] is not None for r in ts_rows)
    any_borrow = any(r[4] is not None for r in ts_rows)

    if not any_totals:
        result['flags'].append('MISSING_TOTALS_FIELDS')
    if not any_borrow:
        result['flags'].append('MISSING_BORROW_FIELDS')

    # Compute per-pool metrics with materiality tagging
    pool_details = []
    utilizations = []
    spreads = []
    ratios = []
    total_tvl_with_util = 0.0
    total_tvl_with_rate = 0.0

    for pool_id, supply, borrow, s_apy, b_apy in ts_rows:
        info = pool_info.get(pool_id, {'tvl': 0, 'name': pool_id[:20]})
        tvl = info['tvl']
        name = info['name']

        # Compute pool share of selected TVL
        pool_share = tvl / selected_tvl_sum if selected_tvl_sum > 0 else 0

        # Materiality check
        is_material = (pool_share >= MATERIAL_SHARE_THRESHOLD or
                       tvl >= MATERIAL_TVL_THRESHOLD)

        detail = {
            'pool_id': pool_id,
            'pool_name': name,
            'tvl': tvl,
            'pool_share': pool_share,
            'is_material': is_material,
            'utilization': None,
            'spread': None,
            'ratio': None,
        }

        # Utilization
        if supply is not None and float(supply) > 0 and borrow is not None:
            util = float(borrow) / float(supply)
            detail['utilization'] = util
            utilizations.append({
                'pool_id': pool_id,
                'pool_name': name,
                'util': util,
                'tvl': tvl,
                'pool_share': pool_share,
                'is_material': is_material,
            })
            total_tvl_with_util += tvl

        # Spread and ratio
        if s_apy is not None and b_apy is not None:
            spread = float(b_apy) - float(s_apy)
            detail['spread'] = spread
            spreads.append({
                'pool_id': pool_id,
                'pool_name': name,
                'spread': spread,
                'tvl': tvl,
                'pool_share': pool_share,
                'is_material': is_material,
            })
            total_tvl_with_rate += tvl

            # Only compute ratio if supply APY >= 0.5% (avoid blowup)
            if float(s_apy) >= 0.005:
                ratio = float(b_apy) / float(s_apy)
                detail['ratio'] = ratio
                ratios.append(ratio)

        pool_details.append(detail)

    result['pool_details'] = pool_details

    # Metric coverage: % of selected TVL with required fields
    if selected_tvl_sum > 0:
        result['metric_coverage_util'] = total_tvl_with_util / selected_tvl_sum
        result['metric_coverage_rate'] = total_tvl_with_rate / selected_tvl_sum

    # Aggregates: utilization
    if utilizations:
        result['lending_util_max'] = max(u['util'] for u in utilizations)

        total_w = sum(u['tvl'] for u in utilizations)
        if total_w > 0:
            weighted_sum = sum(u['util'] * u['tvl'] for u in utilizations)
            result['lending_util_avg'] = weighted_sum / total_w
        else:
            result['lending_util_avg'] = statistics.mean(u['util'] for u in utilizations)

    # Aggregates: spread (replaces ratio as primary metric)
    if spreads:
        spread_values = [s['spread'] for s in spreads]
        result['lending_spread_max'] = max(spread_values)
        result['lending_spread_median'] = statistics.median(spread_values)

    # Aggregates: ratio (gated — only pools with supply >= 0.5%)
    if ratios:
        result['lending_ratio_max'] = max(ratios)
        result['lending_ratio_median'] = statistics.median(ratios)

    # --- Rate volatility (30-day CV from pool history) ---
    today = date.today()
    thirty_days_ago = today - timedelta(days=30)

    supply_cvs = []
    borrow_cvs = []
    history_days_counts = []

    for pool_id in selected_pool_ids[:10]:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT date, supply_apy_total, borrow_apy_total
                   FROM pool_timeseries_daily
                   WHERE pool_id = %s AND date >= %s
                   ORDER BY date""",
                (pool_id, thirty_days_ago)
            )
            rows = cur.fetchall()

        history_days_counts.append(len(rows))

        if len(rows) < 5:
            continue

        supply_series = [float(r[1]) for r in rows if r[1] is not None]
        borrow_series = [float(r[2]) for r in rows if r[2] is not None]

        if len(supply_series) >= 5:
            cv = _compute_cv(supply_series)
            if cv is not None:
                supply_cvs.append(cv)

        if len(borrow_series) >= 5:
            cv = _compute_cv(borrow_series)
            if cv is not None:
                borrow_cvs.append(cv)

    if supply_cvs:
        result['lending_rate_cv_supply_30d'] = max(supply_cvs)
    if borrow_cvs:
        result['lending_rate_cv_borrow_30d'] = max(borrow_cvs)

    # Check for insufficient history
    if history_days_counts and max(history_days_counts) < 21:
        result['flags'].append('INSUFFICIENT_HISTORY')

    return result


def _compute_cv(series):
    """Compute coefficient of variation: stdev / max(mean, 0.01)"""
    if len(series) < 2:
        return None
    mean = statistics.mean(series)
    stdev = statistics.stdev(series)
    floor = max(mean, 0.01)  # 1% floor per spec
    return stdev / floor


def compute_tvl_deltas(conn, protocol_id):
    """Step H (part 1): Compute TVL percentage changes 1d/7d/30d.
    Returns dict with tvl_usd, tvl_1d_pct, tvl_7d_pct, tvl_30d_pct, has_data_gap."""

    today = date.today()
    result = {
        'tvl_usd': None,
        'tvl_1d_pct': None,
        'tvl_7d_pct': None,
        'tvl_30d_pct': None,
        'has_data_gap': False
    }

    with conn.cursor() as cur:
        # Get latest TVL
        cur.execute(
            """SELECT date, tvl_usd FROM protocol_tvl_daily
               WHERE protocol_id = %s ORDER BY date DESC LIMIT 1""",
            (protocol_id,)
        )
        latest = cur.fetchone()

        if not latest:
            result['has_data_gap'] = True
            return result

        result['tvl_usd'] = float(latest[1])
        latest_date = latest[0]

        # 1-day comparison
        target_1d = latest_date - timedelta(days=1)
        cur.execute(
            """SELECT tvl_usd FROM protocol_tvl_daily
               WHERE protocol_id = %s AND date <= %s
               ORDER BY date DESC LIMIT 1""",
            (protocol_id, target_1d)
        )
        row = cur.fetchone()
        if row and row[0] and row[0] > 0:
            result['tvl_1d_pct'] = float((latest[1] - row[0]) / row[0] * 100)
        else:
            result['has_data_gap'] = True

        # 7-day comparison
        target_7d = latest_date - timedelta(days=7)
        cur.execute(
            """SELECT tvl_usd FROM protocol_tvl_daily
               WHERE protocol_id = %s AND date <= %s
               ORDER BY date DESC LIMIT 1""",
            (protocol_id, target_7d)
        )
        row = cur.fetchone()
        if row and row[0] and row[0] > 0:
            result['tvl_7d_pct'] = float((latest[1] - row[0]) / row[0] * 100)
        else:
            result['has_data_gap'] = True

        # 30-day comparison
        target_30d = latest_date - timedelta(days=30)
        cur.execute(
            """SELECT tvl_usd FROM protocol_tvl_daily
               WHERE protocol_id = %s AND date <= %s
               ORDER BY date DESC LIMIT 1""",
            (protocol_id, target_30d)
        )
        row = cur.fetchone()
        if row and row[0] and row[0] > 0:
            result['tvl_30d_pct'] = float((latest[1] - row[0]) / row[0] * 100)

    return result


def compute_chain_concentration(conn, protocol_id):
    """Step H (part 2): Compute top chain share and 7d change.
    Returns dict with top_chain, top_chain_share_pct, top_chain_share_change_7d_pp."""

    today = date.today()
    result = {
        'top_chain': None,
        'top_chain_share_pct': None,
        'top_chain_share_change_7d_pp': None
    }

    with conn.cursor() as cur:
        # Get latest chain breakdown
        cur.execute(
            """SELECT chain, tvl_usd FROM protocol_chain_tvl_daily
               WHERE protocol_id = %s AND date = (
                   SELECT MAX(date) FROM protocol_chain_tvl_daily WHERE protocol_id = %s
               )
               ORDER BY tvl_usd DESC""",
            (protocol_id, protocol_id)
        )
        chains = cur.fetchall()

    if not chains:
        return result

    total_tvl = sum(c[1] for c in chains if c[1])
    if total_tvl <= 0:
        return result

    top_chain = chains[0][0]
    top_chain_tvl = chains[0][1]
    top_chain_share = float(top_chain_tvl / total_tvl * 100)

    result['top_chain'] = top_chain
    result['top_chain_share_pct'] = top_chain_share

    # 7d ago comparison
    seven_days_ago = today - timedelta(days=7)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT chain, tvl_usd FROM protocol_chain_tvl_daily
               WHERE protocol_id = %s AND date <= %s
               AND date = (
                   SELECT MAX(date) FROM protocol_chain_tvl_daily
                   WHERE protocol_id = %s AND date <= %s
               )
               ORDER BY tvl_usd DESC""",
            (protocol_id, seven_days_ago, protocol_id, seven_days_ago)
        )
        old_chains = cur.fetchall()

    if old_chains:
        old_total = sum(c[1] for c in old_chains if c[1])
        if old_total > 0:
            # Find the same chain's share 7d ago
            old_top_share = None
            for chain, tvl in old_chains:
                if chain == top_chain:
                    old_top_share = float(tvl / old_total * 100)
                    break

            if old_top_share is not None:
                result['top_chain_share_change_7d_pp'] = top_chain_share - old_top_share

    return result
