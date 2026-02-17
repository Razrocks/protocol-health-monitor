"""
Risk score computation.
Computes final risk score with category-specific guardrails.
"""

LENDING_ALERT_TYPES = {
    'UTILIZATION_CRITICAL',
    'UTILIZATION_EXTREME',
    'RATE_SPREAD_STRESS',
    'RATE_SPREAD_SEVERE',
    'RATE_UNSTABLE',
    'RATE_WILD',
    'POOL_CONCENTRATION_RISK',
    'INSUFFICIENT_HISTORY',
    'TAIL_POOL_UTIL_SPIKE',
    'DATA_INCOMPLETE_UTIL',
    'DATA_INCOMPLETE_RATE',
}

def compute_risk_scores(protocol_id, category, alerts, metrics):
    """Compute final risk score with guardrails.

    Args:
        protocol_id: Protocol ID
        category: Protocol category (LENDING/DEX/LSD/PERP)
        alerts: List of triggered alert dicts from generate_alerts()
        metrics: Dict of all computed metrics

    Returns:
        Tuple of (risk_score: int, risk_flags: dict)
        risk_flags maps alert_type -> {points, value, threshold}
    """
    global_points = 0
    lending_points = 0
    risk_flags = {}

    for alert in alerts:
        alert_type = alert['alert_type']
        points = alert['points']

        if alert_type in LENDING_ALERT_TYPES:
            lending_points += points
        else:
            global_points += points

        risk_flags[alert_type] = {
            'points': points,
            'severity': alert['severity'],
            'value': _safe_float(alert.get('value')),
            'threshold': _safe_float(alert.get('threshold')),
            'message': alert.get('message', ''),
        }

    # Coverage guardrail: if coverage_pct < 0.5, reduce lending-only points by 30%
    coverage_pct = metrics.get('coverage_pct')
    if coverage_pct is not None and coverage_pct < 0.5:
        lending_points = int(lending_points * 0.7)
        risk_flags['_COVERAGE_GUARDRAIL'] = {
            'applied': True,
            'coverage_pct': coverage_pct,
            'lending_points_multiplier': 0.7,
            'info': 'Lending points reduced due to low coverage'
        }

    # Metric coverage guardrail: if metric coverage < 0.5, block CRIT and reduce points
    metric_cov_util = metrics.get('metric_coverage_util')
    if metric_cov_util is not None and metric_cov_util < 0.5:
        lending_points = int(lending_points * 0.7)
        risk_flags['_METRIC_COVERAGE_GUARDRAIL'] = {
            'applied': True,
            'metric_coverage_util': metric_cov_util,
            'lending_points_multiplier': 0.7,
            'info': 'Lending points reduced: <50% of selected TVL has utilization data'
        }

    total = min(global_points + lending_points, 100)

    return total, risk_flags

def _safe_float(value):
    """Convert to float safely for JSON serialization"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
