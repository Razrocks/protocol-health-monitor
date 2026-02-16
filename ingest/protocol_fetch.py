"""
Config-driven protocol TVL fetcher (Steps A + B)
Reads endpoints from config/api.yaml, slugs from config/protocols.yaml
"""
import os
import json
import time
import yaml
import requests
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Optional
from psycopg2.extras import Json


def load_config():
    """Load API and protocol configuration"""
    base_dir = os.path.join(os.path.dirname(__file__), '..', 'config')

    with open(os.path.join(base_dir, 'api.yaml'), 'r') as f:
        api_config = yaml.safe_load(f)

    with open(os.path.join(base_dir, 'protocols.yaml'), 'r') as f:
        protocols_config = yaml.safe_load(f)

    return api_config, protocols_config


def validate_config(protocols_config):
    """Ensure no REPLACE_ME values remain in config"""
    errors = []
    for p in protocols_config.get('protocols', []):
        for field in ['protocol_slug', 'yields_project']:
            val = p.get(field)
            if val == 'REPLACE_ME' or val is None:
                errors.append(f"{p['name']}.{field} is not configured")
    return errors


def fetch_protocol_tvl(session, url_template, slug, delay=0.5):
    """Fetch a single protocol's TVL data from DeFiLlama"""
    url = url_template.format(slug=slug)
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        time.sleep(delay)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"  ERROR fetching {slug}: {e}")
        return None


def fetch_all_protocols(api_config, protocols_config):
    """Fetch TVL data for all configured protocols"""
    url_template = api_config['endpoints']['protocol_tvl']
    delay = api_config.get('rate_limit', {}).get('delay_seconds', 0.5)

    session = requests.Session()
    session.headers.update({'User-Agent': 'Protocol-Health-Monitor/2.0'})

    results = {}
    protocols = [p for p in protocols_config.get('protocols', []) if p.get('enabled', True)]

    for p in protocols:
        slug = p['protocol_slug']
        print(f"  Fetching {p['name']} ({slug})...")
        data = fetch_protocol_tvl(session, url_template, slug, delay)

        if data:
            results[slug] = {
                'config': p,
                'data': data
            }
            tvl_count = len(data.get('tvl', []))
            print(f"    -> {tvl_count} TVL datapoints")
        else:
            print(f"    -> FAILED")

    return results


def load_protocol_tvl(conn, run_id, protocol_id, raw_data):
    """Parse and load TVL history + chain breakdown for one protocol"""
    # Save raw snapshot
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO raw_protocol_snapshots (run_id, protocol_id, fetched_at, raw_json)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (run_id, protocol_id) DO UPDATE
               SET raw_json = EXCLUDED.raw_json, fetched_at = EXCLUDED.fetched_at""",
            (run_id, protocol_id, datetime.now(), Json(raw_data))
        )

    # Parse TVL history
    tvl_data = raw_data.get('tvl', [])
    seen_dates = set()
    tvl_records = []

    for point in tvl_data:
        if isinstance(point, dict) and 'date' in point and 'totalLiquidityUSD' in point:
            point_date = date.fromtimestamp(int(point['date']))
            if point_date in seen_dates:
                continue
            seen_dates.add(point_date)
            tvl_usd = Decimal(str(point['totalLiquidityUSD']))
            tvl_records.append((point_date, protocol_id, tvl_usd))

    with conn.cursor() as cur:
        for record in tvl_records:
            cur.execute(
                """INSERT INTO protocol_tvl_daily (date, protocol_id, tvl_usd)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (date, protocol_id) DO UPDATE
                   SET tvl_usd = EXCLUDED.tvl_usd""",
                record
            )

    # Parse chain TVL breakdown
    current_chain_tvls = raw_data.get('currentChainTvls', {})
    snapshot_date = date.today()
    chain_records = []

    for chain_name, tvl_value in current_chain_tvls.items():
        if chain_name and tvl_value and isinstance(tvl_value, (int, float)):
            # Skip staking/pool2/borrowed entries
            if any(x in chain_name.lower() for x in ['-staking', '-pool2', '-borrowed']):
                continue
            tvl_usd = Decimal(str(tvl_value))
            chain_records.append((snapshot_date, protocol_id, chain_name, tvl_usd))

    with conn.cursor() as cur:
        for record in chain_records:
            cur.execute(
                """INSERT INTO protocol_chain_tvl_daily (date, protocol_id, chain, tvl_usd)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (date, protocol_id, chain) DO UPDATE
                   SET tvl_usd = EXCLUDED.tvl_usd""",
                record
            )

    print(f"    -> Loaded {len(tvl_records)} TVL + {len(chain_records)} chain records")
    return len(tvl_records) > 0
