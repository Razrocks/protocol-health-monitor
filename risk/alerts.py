"""
Alert generation engine.
Evaluates all alert rules against protocol metrics and generates alert records.
"""
from risk.metrics import (
    get_metric, has_lending_data, has_rate_data, has_volatility_data,
    max_rate_cv, has_material_util_pool, has_material_spread_pool,
    max_material_util, max_material_spread,
    worst_material_util_pool, worst_material_spread_pool,
    is_structural_chain_category,
)

def _fmt_tvl(tvl):
    """Format TVL for messages"""
    if tvl is None:
        return '$?'
    if tvl >= 1e9:
        return f'${tvl/1e9:.1f}B'
    if tvl >= 1e6:
        return f'${tvl/1e6:.0f}M'
    if tvl >= 1e3:
        return f'${tvl/1e3:.0f}K'
    return f'${tvl:.0f}'

def generate_alerts(protocol_id, category, metrics):
    """Generate all applicable alerts for a protocol.

    Args:
        protocol_id: Protocol ID
        category: Protocol category (LENDING/DEX/LSD/PERP)
        metrics: Dict of all computed metrics (including pool_details)

    Returns:
        List of alert dicts with type, severity, points, value, threshold, message
    """
    name = metrics.get('protocol_name', f'Protocol {protocol_id}')
    triggered = []

    # TVL drops (7d supersedes 1d)
    tvl_7d = metrics.get('tvl_7d_pct')
    tvl_1d = metrics.get('tvl_1d_pct')

    if tvl_7d is not None and tvl_7d <= -8:
        triggered.append({
            'alert_type': 'TVL_7D_DROP',
            'severity': 'high',
            'points': 40,
            'value': tvl_7d,
            'threshold': -8,
            'message': f"{name} TVL dropped {abs(tvl_7d):.1f}% in 7 days",
        })
    elif tvl_1d is not None and tvl_1d <= -3:
        triggered.append({
            'alert_type': 'TVL_1D_DROP',
            'severity': 'med',
            'points': 20,
            'value': tvl_1d,
            'threshold': -3,
            'message': f"{name} TVL dropped {abs(tvl_1d):.1f}% in 1 day",
        })

    # Chain concentration
    top_share = metrics.get('top_chain_share_pct') or 0
    top_chain = metrics.get('top_chain', 'unknown')
    share_change = metrics.get('top_chain_share_change_7d_pp') or 0

    if is_structural_chain_category(category):
        # LSD/PERP: label as "Structural Dependence", keep points but not alarming
        if top_share >= 70:
            triggered.append({
                'alert_type': 'STRUCTURAL_CHAIN_DEPENDENCE',
                'severity': 'low',
                'points': 15,
                'value': top_share,
                'threshold': 70,
                'message': f"{name} structural dependence: {top_share:.1f}% TVL on {top_chain} (expected for {category})",
            })
    else:
        if top_share >= 70:
            triggered.append({
                'alert_type': 'TOP_CHAIN_CONCENTRATION_HIGH',
                'severity': 'med',
                'points': 15,
                'value': top_share,
                'threshold': 70,
                'message': f"{name} has {top_share:.1f}% TVL on {top_chain}",
            })

    if share_change >= 10:
        triggered.append({
            'alert_type': 'TOP_CHAIN_CONCENTRATION_SPIKE',
            'severity': 'high',
            'points': 25,
            'value': share_change,
            'threshold': 10,
            'message': f"{name} top chain concentration spiked +{share_change:.1f}pp in 7d",
        })

    # Data gap
    if metrics.get('has_data_gap', False):
        triggered.append({
            'alert_type': 'DATA_GAP',
            'severity': 'low',
            'points': 10,
            'value': 1,
            'threshold': 0,
            'message': f"{name} has missing historical data for TVL comparison",
        })

    # Low coverage
    coverage = metrics.get('coverage_pct')
    if coverage is not None and coverage < 0.5:
        triggered.append({
            'alert_type': 'LOW_COVERAGE',
            'severity': 'med',
            'points': 10,
            'value': coverage,
            'threshold': 0.5,
            'message': f"{name} pool coverage is {coverage:.1%} (below 50%)",
        })

    if category != 'LENDING':
        # Non-lending: pool concentration (only for LENDING/DEX)
        if category == 'DEX':
            pc80 = metrics.get('pool_count_80')
            if pc80 is not None and pc80 < 3:
                triggered.append({
                    'alert_type': 'POOL_CONCENTRATION_RISK',
                    'severity': 'high',
                    'points': 25,
                    'value': pc80,
                    'threshold': 3,
                    'message': f"{name} pool concentration risk: only {pc80} pools for 80% TVL",
                })
        return triggered

    # Metric coverage check â€” if a required metric is NULL, flag it but don't alert
    metric_cov_util = metrics.get('metric_coverage_util')
    metric_cov_rate = metrics.get('metric_coverage_rate')
    low_metric_coverage = (metric_cov_util is not None and metric_cov_util < 0.5)

    # Data completeness flags
    if metrics.get('lending_util_avg') is None and has_lending_data(metrics) is False:
        triggered.append({
            'alert_type': 'DATA_INCOMPLETE_UTIL',
            'severity': 'low',
            'points': 0,
            'value': metric_cov_util,
            'threshold': None,
            'message': f"{name} utilization data unavailable from source",
        })
    if metrics.get('lending_spread_max') is None and has_rate_data(metrics) is False:
        triggered.append({
            'alert_type': 'DATA_INCOMPLETE_RATE',
            'severity': 'low',
            'points': 0,
            'value': metric_cov_rate,
            'threshold': None,
            'message': f"{name} rate spread data unavailable from source",
        })

    util_avg = metrics.get('lending_util_avg')
    mat_util = max_material_util(metrics)
    worst_util_pool = worst_material_util_pool(metrics)

    if mat_util is not None and worst_util_pool is not None:
        pool_name = worst_util_pool['pool_name']
        pool_tvl = worst_util_pool['tvl']
        pool_share = worst_util_pool['pool_share']

        # Severity gating:
        # CRIT requires share >= 5% (high protocol impact), not just big TVL
        # Pools with large TVL but low share: cap at MED (systemically large but not protocol-critical)
        high_share = pool_share >= 0.05

        if mat_util >= 0.90:
            if low_metric_coverage:
                sev = 'high'
                pts = 25
            elif high_share:
                sev = 'crit'
                pts = 40
            else:
                # Large TVL but low share â€” MED, reduced points
                sev = 'med'
                pts = 15
            triggered.append({
                'alert_type': 'UTILIZATION_EXTREME',
                'severity': sev,
                'points': pts,
                'value': mat_util,
                'threshold': 0.90,
                'message': f"UTILIZATION_EXTREME on {pool_name}: {mat_util:.1%} (tvl {_fmt_tvl(pool_tvl)}, share {pool_share:.1%})",
            })
        elif util_avg is not None and util_avg >= 0.80:
            triggered.append({
                'alert_type': 'UTILIZATION_CRITICAL',
                'severity': 'high' if high_share else 'med',
                'points': 25 if high_share else 10,
                'value': util_avg,
                'threshold': 0.80,
                'message': f"{name} avg utilization at {util_avg:.1%} (critical)",
            })

    # Check for non-material tail pool spikes (INFO only, capped points)
    all_utils = metrics.get('pool_details', [])
    for d in all_utils:
        if not d.get('is_material') and d.get('utilization') is not None and d['utilization'] >= 0.90:
            triggered.append({
                'alert_type': 'TAIL_POOL_UTIL_SPIKE',
                'severity': 'low',
                'points': 5,
                'value': d['utilization'],
                'threshold': 0.90,
                'message': f"Tail pool spike: {d['pool_name']} util {d['utilization']:.1%} (tvl {_fmt_tvl(d['tvl'])}, share {d['pool_share']:.1%}) â€” non-material",
            })
            break  # Only one tail alert per run

    mat_spread = max_material_spread(metrics)
    worst_spread_pool = worst_material_spread_pool(metrics)

    if mat_spread is not None and worst_spread_pool is not None:
        pool_name = worst_spread_pool['pool_name']
        pool_tvl = worst_spread_pool['tvl']
        pool_share = worst_spread_pool['pool_share']

        # Spread values are in percentage points (e.g., 2.6 = 2.6pp)
        # DeFiLlama APYs are already in % form (e.g., 3.75 means 3.75%)
        if mat_spread >= 6.0:  # 6pp spread
            triggered.append({
                'alert_type': 'RATE_SPREAD_SEVERE',
                'severity': 'high',
                'points': 25,
                'value': mat_spread,
                'threshold': 6.0,
                'message': f"RATE_SPREAD_SEVERE on {pool_name}: {mat_spread:.2f}pp spread (tvl {_fmt_tvl(pool_tvl)}, share {pool_share:.1%})",
            })
        elif mat_spread >= 3.0:  # 3pp spread
            triggered.append({
                'alert_type': 'RATE_SPREAD_STRESS',
                'severity': 'med',
                'points': 15,
                'value': mat_spread,
                'threshold': 3.0,
                'message': f"RATE_SPREAD_STRESS on {pool_name}: {mat_spread:.2f}pp spread (tvl {_fmt_tvl(pool_tvl)}, share {pool_share:.1%})",
            })

    if has_volatility_data(metrics):
        cv = max_rate_cv(metrics)
        if cv >= 1.0:
            triggered.append({
                'alert_type': 'RATE_WILD',
                'severity': 'high',
                'points': 20,
                'value': cv,
                'threshold': 1.0,
                'message': f"{name} rate volatility CV at {cv:.2f} (wild)",
            })
        elif cv >= 0.5:
            triggered.append({
                'alert_type': 'RATE_UNSTABLE',
                'severity': 'med',
                'points': 10,
                'value': cv,
                'threshold': 0.5,
                'message': f"{name} rate volatility CV at {cv:.2f} (unstable)",
            })

    # Pool concentration
    pc80 = metrics.get('pool_count_80')
    if pc80 is not None and pc80 < 3:
        triggered.append({
            'alert_type': 'POOL_CONCENTRATION_RISK',
            'severity': 'high',
            'points': 25,
            'value': pc80,
            'threshold': 3,
            'message': f"{name} pool concentration risk: only {pc80} pools for 80% TVL",
        })

    # Insufficient history
    if 'INSUFFICIENT_HISTORY' in metrics.get('lending_flags', []):
        triggered.append({
            'alert_type': 'INSUFFICIENT_HISTORY',
            'severity': 'low',
            'points': 5,
            'value': 1,
            'threshold': 21,
            'message': f"{name} has insufficient rate history (<21 days) for volatility computation",
        })

    return triggered
