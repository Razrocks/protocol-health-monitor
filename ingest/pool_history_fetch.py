"""
Pool history fetcher for lending protocols (Step F)
Fetches lend/borrow time series for top pools of LENDING protocols
"""
import time
import requests
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Optional


def fetch_lending_pool_history(conn, run_id, api_config, protocols_config, pool_selections):
    """Step F: Fetch pool history for rate volatility computation (LENDING only)"""

    url_template = api_config['endpoints']['pool_history']
    delay = api_config.get('rate_limit', {}).get('delay_seconds', 0.5)
    max_pools = api_config.get('rate_limit', {}).get('max_pool_history_fetch', 10)

    session = requests.Session()
    session.headers.update({'User-Agent': 'Protocol-Health-Monitor/2.0'})

    # Get lending protocol IDs
    lending_protocol_ids = set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT protocol_id, name FROM protocols WHERE category = 'LENDING' AND enabled = true"
        )
        lending_protocols = cur.fetchall()
        for pid, name in lending_protocols:
            lending_protocol_ids.add(pid)

    if not lending_protocol_ids:
        print("  No LENDING protocols configured, skipping pool history")
        return {}

    history_stats = {}

    for protocol_id, name in lending_protocols:
        selection = pool_selections.get(protocol_id)
        if not selection:
            print(f"  {name}: no pool selection data, skipping")
            continue

        pool_ids = selection.get('selected_pool_ids', [])[:max_pools]

        if not pool_ids:
            print(f"  {name}: no selected pools, skipping")
            continue

        print(f"  Fetching history for {name} ({len(pool_ids)} pools)...")
        fetched = 0
        failed = 0

        for pool_id in pool_ids:
            url = url_template.format(pool_id=pool_id)
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                data = response.json()

                points = _parse_and_store_history(conn, pool_id, data)
                if points > 0:
                    fetched += 1
                    print(f"    -> {pool_id[:20]}...: {points} datapoints")
                else:
                    print(f"    -> {pool_id[:20]}...: no parseable data")

            except requests.exceptions.HTTPError as e:
                if e.response and e.response.status_code == 429:
                    print(f"    -> Rate limited, stopping history fetch for {name}")
                    break
                print(f"    -> {pool_id[:20]}...: HTTP error {e}")
                failed += 1
            except requests.exceptions.RequestException as e:
                print(f"    -> {pool_id[:20]}...: error {e}")
                failed += 1

            time.sleep(delay)

        history_stats[protocol_id] = {
            'fetched': fetched,
            'failed': failed,
            'total': len(pool_ids)
        }

    return history_stats


def _parse_and_store_history(conn, pool_id, data):
    """Parse chartLendBorrow response and store in pool_timeseries_daily"""
    # The API returns a list of data points with timestamp and metrics
    if not isinstance(data, list) and not isinstance(data, dict):
        return 0

    # Handle both list format and dict with 'data' key
    points = data if isinstance(data, list) else data.get('data', [])

    if not points:
        return 0

    stored = 0
    seen_dates = set()

    with conn.cursor() as cur:
        for point in points:
            if not isinstance(point, dict):
                continue

            # Parse timestamp
            ts = point.get('timestamp')
            if ts is None:
                continue

            try:
                if isinstance(ts, str):
                    point_date = datetime.fromisoformat(ts.replace('Z', '+00:00')).date()
                else:
                    point_date = date.fromtimestamp(int(ts))
            except (ValueError, OSError):
                continue

            if point_date in seen_dates:
                continue
            seen_dates.add(point_date)

            supply_apy = _safe_decimal(point.get('apyBase'))
            borrow_apy = _safe_decimal(point.get('apyBaseBorrow'))
            total_supply = _safe_decimal(point.get('totalSupplyUsd'))
            total_borrow = _safe_decimal(point.get('totalBorrowUsd'))

            cur.execute(
                """INSERT INTO pool_timeseries_daily
                   (pool_id, date, supply_apy_total, borrow_apy_total,
                    total_supply_usd, total_borrow_usd)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (pool_id, date) DO UPDATE SET
                    supply_apy_total = EXCLUDED.supply_apy_total,
                    borrow_apy_total = EXCLUDED.borrow_apy_total,
                    total_supply_usd = EXCLUDED.total_supply_usd,
                    total_borrow_usd = EXCLUDED.total_borrow_usd""",
                (pool_id, point_date, supply_apy, borrow_apy,
                 total_supply, total_borrow)
            )
            stored += 1

    return stored


def _safe_decimal(value):
    """Safely convert to Decimal"""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
