"""
Pool data fetcher, mapper, and selector (Steps C + D + E)
Fetches all pools from yields API, maps to monitored protocols, selects top pools
"""
import json
import time
import requests
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from psycopg2.extras import Json


def fetch_pools(session, api_config):
    """Step C: Fetch all pools from yields API"""
    url = api_config['endpoints']['yields_pools']
    print(f"  Fetching pools from {url}...")

    try:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        data = response.json()
        pools = data.get('data', [])
        print(f"    -> {len(pools)} total pools")
        return data
    except requests.exceptions.RequestException as e:
        print(f"  ERROR fetching pools: {e}")
        return None


def store_raw_pools(conn, run_id, raw_data):
    """Store raw pools JSON for auditing"""
    pools = raw_data.get('data', [])
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO raw_pools_json (run_id, fetched_at, pool_count, raw_json)
               VALUES (%s, %s, %s, %s)""",
            (run_id, datetime.now(), len(pools), Json(raw_data))
        )


def map_pool_fields(pool_row):
    """Map yields API pool fields to our schema.
    Returns a dict with normalized field names.
    Fields that don't exist are set to None with appropriate flags."""

    has_borrow = pool_row.get('apyBaseBorrow') is not None
    has_totals = (pool_row.get('totalSupplyUsd') is not None and
                  pool_row.get('totalBorrowUsd') is not None)

    return {
        'pool_id': pool_row.get('pool', ''),
        'chain': pool_row.get('chain', ''),
        'project': pool_row.get('project', ''),
        'pool_name': pool_row.get('symbol', ''),
        'tvl_usd': _to_decimal(pool_row.get('tvlUsd')),
        'supply_apy_total': _to_decimal(pool_row.get('apy')),
        'supply_apy_base': _to_decimal(pool_row.get('apyBase')),
        'supply_apy_reward': _to_decimal(pool_row.get('apyReward')),
        'borrow_apy_total': _to_decimal(pool_row.get('apyBaseBorrow')) if has_borrow else None,
        'borrow_apy_base': _to_decimal(pool_row.get('apyBaseBorrow')) if has_borrow else None,
        'borrow_apy_reward': _to_decimal(pool_row.get('apyRewardBorrow')) if has_borrow else None,
        'total_supply_usd': _to_decimal(pool_row.get('totalSupplyUsd')) if has_totals else None,
        'total_borrow_usd': _to_decimal(pool_row.get('totalBorrowUsd')) if has_totals else None,
        'has_borrow_fields': has_borrow,
        'has_totals_fields': has_totals,
    }


def _to_decimal(value):
    """Safely convert to Decimal, return None if not possible"""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def map_and_store_pools(conn, run_id, raw_data, protocols_config):
    """Step D: Map pools to monitored protocols and store in pools_current"""

    # Build yields_project -> protocol_id lookup
    project_to_protocol = {}
    with conn.cursor() as cur:
        for p in protocols_config.get('protocols', []):
            yp = p.get('yields_project')
            if yp and yp != 'REPLACE_ME':
                cur.execute(
                    "SELECT protocol_id FROM protocols WHERE slug = %s OR protocol_slug = %s",
                    (p.get('protocol_slug', ''), p.get('protocol_slug', ''))
                )
                row = cur.fetchone()
                if row:
                    project_to_protocol[yp] = row[0]

    pools = raw_data.get('data', [])
    mapped_count = 0

    with conn.cursor() as cur:
        for pool_row in pools:
            project = pool_row.get('project', '')
            protocol_id = project_to_protocol.get(project)

            if protocol_id is None:
                continue

            mapped = map_pool_fields(pool_row)
            mapped_count += 1

            cur.execute(
                """INSERT INTO pools_current
                   (run_id, pool_id, protocol_id, pool_name, chain, project,
                    tvl_usd, supply_apy_total, supply_apy_base, supply_apy_reward,
                    borrow_apy_total, borrow_apy_base, borrow_apy_reward,
                    total_supply_usd, total_borrow_usd,
                    has_borrow_fields, has_totals_fields, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s,
                           %s, %s, %s,
                           %s, %s,
                           %s, %s, %s)
                   ON CONFLICT (run_id, pool_id) DO UPDATE SET
                    tvl_usd = EXCLUDED.tvl_usd,
                    supply_apy_total = EXCLUDED.supply_apy_total,
                    updated_at = EXCLUDED.updated_at""",
                (run_id, mapped['pool_id'], protocol_id, mapped['pool_name'],
                 mapped['chain'], mapped['project'],
                 mapped['tvl_usd'], mapped['supply_apy_total'],
                 mapped['supply_apy_base'], mapped['supply_apy_reward'],
                 mapped['borrow_apy_total'], mapped['borrow_apy_base'],
                 mapped['borrow_apy_reward'],
                 mapped['total_supply_usd'], mapped['total_borrow_usd'],
                 mapped['has_borrow_fields'], mapped['has_totals_fields'],
                 datetime.now())
            )

    print(f"    -> Mapped {mapped_count} pools to monitored protocols")
    return mapped_count


def select_top_pools(conn, run_id, protocols_config, api_config):
    """Step E: For each protocol, select pools until 80% cumulative TVL reached"""

    threshold = api_config.get('pool_selection', {}).get('cumulative_tvl_threshold', 0.80)
    today = date.today()

    with conn.cursor() as cur:
        # Get all monitored protocol IDs
        cur.execute("SELECT protocol_id, name, protocol_slug FROM protocols WHERE enabled = true")
        protocols = cur.fetchall()

    results = {}

    for protocol_id, name, slug in protocols:
        with conn.cursor() as cur:
            # Get all pools for this protocol, sorted by TVL desc
            cur.execute(
                """SELECT pool_id, tvl_usd, pool_name
                   FROM pools_current
                   WHERE run_id = %s AND protocol_id = %s AND tvl_usd > 0
                   ORDER BY tvl_usd DESC""",
                (run_id, protocol_id)
            )
            pools = cur.fetchall()

        if not pools:
            print(f"    {name}: no pools found")
            results[protocol_id] = {
                'selected_pool_ids': [],
                'pool_count_80': 0,
                'top_pool_share': None,
                'selected_pool_tvl_sum': Decimal('0'),
                'coverage_pct': None
            }
            continue

        # Total pool TVL for this protocol
        total_pool_tvl = sum(p[1] for p in pools if p[1])

        if total_pool_tvl <= 0:
            print(f"    {name}: zero total pool TVL")
            continue

        # Select pools until cumulative reaches threshold
        selected_ids = []
        cumulative_tvl = Decimal('0')

        for pool_id, tvl, pool_name in pools:
            if tvl is None or tvl <= 0:
                continue
            selected_ids.append(pool_id)
            cumulative_tvl += tvl
            if cumulative_tvl / total_pool_tvl >= threshold:
                break

        pool_count_80 = len(selected_ids)
        top_pool_tvl = pools[0][1] if pools and pools[0][1] else Decimal('0')
        top_pool_share = float(top_pool_tvl / total_pool_tvl) if total_pool_tvl > 0 else None

        # Concentration shares from FULL pool universe (not selected subset)
        valid_pools = [(pid, tvl, pname) for pid, tvl, pname in pools if tvl and tvl > 0]
        top1_share = float(sum(p[1] for p in valid_pools[:1]) / total_pool_tvl) if valid_pools else None
        top5_share = float(sum(p[1] for p in valid_pools[:5]) / total_pool_tvl) if valid_pools else None
        top10_share = float(sum(p[1] for p in valid_pools[:10]) / total_pool_tvl) if valid_pools else None

        # Get protocol-level TVL for coverage computation
        with conn.cursor() as cur:
            cur.execute(
                """SELECT tvl_usd FROM protocol_tvl_daily
                   WHERE protocol_id = %s
                   ORDER BY date DESC LIMIT 1""",
                (protocol_id,)
            )
            row = cur.fetchone()
            protocol_tvl = row[0] if row else None

        coverage_pct = None
        if protocol_tvl and protocol_tvl > 0:
            coverage_pct = float(cumulative_tvl / protocol_tvl)

        # Store selection + concentration shares
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO protocol_pool_selection_daily
                   (date, protocol_id, selection_method, selected_pool_ids,
                    selected_pool_tvl_sum, pool_count_80, top_pool_share, coverage_pct,
                    total_pool_tvl_usd, top1_share_total, top5_share_total, top10_share_total)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (date, protocol_id) DO UPDATE SET
                    selected_pool_ids = EXCLUDED.selected_pool_ids,
                    selected_pool_tvl_sum = EXCLUDED.selected_pool_tvl_sum,
                    pool_count_80 = EXCLUDED.pool_count_80,
                    top_pool_share = EXCLUDED.top_pool_share,
                    coverage_pct = EXCLUDED.coverage_pct,
                    total_pool_tvl_usd = EXCLUDED.total_pool_tvl_usd,
                    top1_share_total = EXCLUDED.top1_share_total,
                    top5_share_total = EXCLUDED.top5_share_total,
                    top10_share_total = EXCLUDED.top10_share_total""",
                (today, protocol_id, 'TOP_TVL_TO_80',
                 Json(selected_ids), float(cumulative_tvl),
                 pool_count_80, top_pool_share, coverage_pct,
                 float(total_pool_tvl), top1_share, top5_share, top10_share)
            )

        results[protocol_id] = {
            'selected_pool_ids': selected_ids,
            'pool_count_80': pool_count_80,
            'top_pool_share': top_pool_share,
            'selected_pool_tvl_sum': cumulative_tvl,
            'coverage_pct': coverage_pct,
            'total_pool_tvl_usd': float(total_pool_tvl),
            'top1_share_total': top1_share,
            'top5_share_total': top5_share,
            'top10_share_total': top10_share,
        }

        print(f"    {name}: {pool_count_80} pools to 80% TVL, "
              f"coverage={coverage_pct:.1%}" if coverage_pct else f"    {name}: {pool_count_80} pools")

    return results
