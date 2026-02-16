"""
Shared metric computation helpers for the risk engine.
These are convenience wrappers used by alerts.py and scoring.py.
The heavy computation is done in ingest/normalize.py.
"""


def get_metric(metrics, key, default=None):
    """Safely get a metric value, returning default if None"""
    val = metrics.get(key)
    return val if val is not None else default


def has_lending_data(metrics):
    """Check if lending-specific metrics are available"""
    return (metrics.get('lending_util_avg') is not None or
            metrics.get('lending_util_max') is not None)


def has_rate_data(metrics):
    """Check if rate spread data is available"""
    return (metrics.get('lending_spread_max') is not None or
            metrics.get('lending_ratio_max') is not None)


def has_volatility_data(metrics):
    """Check if rate volatility data is available"""
    return (metrics.get('lending_rate_cv_supply_30d') is not None or
            metrics.get('lending_rate_cv_borrow_30d') is not None)


def max_rate_cv(metrics):
    """Get maximum CV between supply and borrow rate volatility"""
    supply_cv = metrics.get('lending_rate_cv_supply_30d', 0) or 0
    borrow_cv = metrics.get('lending_rate_cv_borrow_30d', 0) or 0
    return max(supply_cv, borrow_cv)


def has_material_util_pool(metrics):
    """Check if any MATERIAL pool has utilization data"""
    details = metrics.get('pool_details', [])
    return any(d.get('is_material') and d.get('utilization') is not None for d in details)


def has_material_spread_pool(metrics):
    """Check if any MATERIAL pool has spread data"""
    details = metrics.get('pool_details', [])
    return any(d.get('is_material') and d.get('spread') is not None for d in details)


def max_material_util(metrics):
    """Get max utilization from MATERIAL pools only"""
    details = metrics.get('pool_details', [])
    material_utils = [d['utilization'] for d in details
                      if d.get('is_material') and d.get('utilization') is not None]
    return max(material_utils) if material_utils else None


def max_material_spread(metrics):
    """Get max spread from MATERIAL pools only"""
    details = metrics.get('pool_details', [])
    material_spreads = [d['spread'] for d in details
                        if d.get('is_material') and d.get('spread') is not None]
    return max(material_spreads) if material_spreads else None


def worst_material_util_pool(metrics):
    """Get the pool detail dict for the material pool with highest utilization"""
    details = metrics.get('pool_details', [])
    material = [d for d in details if d.get('is_material') and d.get('utilization') is not None]
    if not material:
        return None
    return max(material, key=lambda d: d['utilization'])


def worst_material_spread_pool(metrics):
    """Get the pool detail dict for the material pool with highest spread"""
    details = metrics.get('pool_details', [])
    material = [d for d in details if d.get('is_material') and d.get('spread') is not None]
    if not material:
        return None
    return max(material, key=lambda d: d['spread'])


def is_structural_chain_category(category):
    """Check if chain concentration is structural for this category"""
    return category in ('LSD', 'PERP')


CATEGORY_LABELS = {
    'LENDING': 'Lending',
    'DEX': 'DEX',
    'LSD': 'Liquid Staking',
    'PERP': 'Perpetuals',
}
