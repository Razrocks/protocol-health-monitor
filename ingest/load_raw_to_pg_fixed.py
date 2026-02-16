"""
Load raw DeFiLlama data into PostgreSQL - FIXED VERSION
"""
import os
import json
import psycopg2
from psycopg2.extras import Json
from datetime import datetime, date
from typing import Dict, List, Optional
from decimal import Decimal


class PostgresLoader:
    """Load data into PostgreSQL"""
    
    def __init__(self, connection_string: str = None):
        if connection_string is None:
            connection_string = os.getenv(
                'DATABASE_URL',
                'postgresql://postgres:postgres@localhost:5432/protocol_health'
            )
        self.conn_string = connection_string
        self.conn = None
    
    def connect(self):
        """Establish database connection"""
        self.conn = psycopg2.connect(self.conn_string)
        self.conn.autocommit = False
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
    
    def get_protocol_id(self, slug: str) -> Optional[int]:
        """Get protocol_id by slug"""
        with self.conn.cursor() as cur:
            cur.execute("SELECT protocol_id FROM protocols WHERE slug = %s", (slug,))
            result = cur.fetchone()
            return result[0] if result else None
    
    def create_ingestion_run(self, git_sha: str = None) -> int:
        """Create a new ingestion run record"""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingestion_runs (started_at, status, git_sha)
                VALUES (%s, %s, %s)
                RETURNING run_id
                """,
                (datetime.now(), 'running', git_sha)
            )
            run_id = cur.fetchone()[0]
            self.conn.commit()
            return run_id
    
    def update_ingestion_run(self, run_id: int, status: str, notes: str = None):
        """Update ingestion run status"""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_runs
                SET finished_at = %s, status = %s, notes = %s
                WHERE run_id = %s
                """,
                (datetime.now(), status, notes, run_id)
            )
            self.conn.commit()
    
    def save_raw_snapshot(self, run_id: int, protocol_id: int, raw_data: Dict):
        """Save raw protocol snapshot"""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw_protocol_snapshots (run_id, protocol_id, fetched_at, raw_json)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id, protocol_id) DO UPDATE
                SET raw_json = EXCLUDED.raw_json, fetched_at = EXCLUDED.fetched_at
                """,
                (run_id, protocol_id, datetime.now(), Json(raw_data))
            )
    
    def parse_and_load_tvl_history(self, protocol_id: int, raw_data: Dict):
        """Parse TVL history from raw data and load into protocol_tvl_daily"""
        tvl_data = raw_data.get('tvl', [])
        
        if not tvl_data:
            print(f"No TVL data for protocol {protocol_id}")
            return
        
        # Deduplicate by date before inserting
        seen_dates = set()
        records = []
        
        for point in tvl_data:
            if isinstance(point, dict) and 'date' in point and 'totalLiquidityUSD' in point:
                point_date = date.fromtimestamp(int(point['date']))
                
                # Skip duplicates
                if point_date in seen_dates:
                    continue
                seen_dates.add(point_date)
                
                tvl_usd = Decimal(str(point['totalLiquidityUSD']))
                records.append((point_date, protocol_id, tvl_usd))
        
        if not records:
            print(f"No valid TVL records for protocol {protocol_id}")
            return
        
        # Insert one by one to handle conflicts properly
        with self.conn.cursor() as cur:
            for record in records:
                cur.execute(
                    """
                    INSERT INTO protocol_tvl_daily (date, protocol_id, tvl_usd)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (date, protocol_id) DO UPDATE
                    SET tvl_usd = EXCLUDED.tvl_usd
                    """,
                    record
                )
        
        print(f"Loaded {len(records)} TVL records for protocol {protocol_id}")
    
    def parse_and_load_chain_tvl(self, protocol_id: int, raw_data: Dict):
        """Parse chain TVL breakdown and load into protocol_chain_tvl_daily"""
        current_chain_tvls = raw_data.get('currentChainTvls', {})
        
        snapshot_date = date.today()
        
        records = []
        for chain, tvl_value in current_chain_tvls.items():
            if chain and tvl_value and isinstance(tvl_value, (int, float)):
                tvl_usd = Decimal(str(tvl_value))
                records.append((snapshot_date, protocol_id, chain, tvl_usd))
        
        if not records:
            print(f"No chain TVL data for protocol {protocol_id}")
            return
        
        # Insert one by one to handle conflicts properly
        with self.conn.cursor() as cur:
            for record in records:
                cur.execute(
                    """
                    INSERT INTO protocol_chain_tvl_daily (date, protocol_id, chain, tvl_usd)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (date, protocol_id, chain) DO UPDATE
                    SET tvl_usd = EXCLUDED.tvl_usd
                    """,
                    record
                )
        
        print(f"Loaded {len(records)} chain TVL records for protocol {protocol_id}")
    
    def load_protocol_data(self, run_id: int, slug: str, raw_data: Dict):
        """Complete loading pipeline for one protocol"""
        protocol_id = self.get_protocol_id(slug)
        
        if not protocol_id:
            print(f"Protocol not found: {slug}")
            return False
        
        try:
            self.save_raw_snapshot(run_id, protocol_id, raw_data)
            self.parse_and_load_tvl_history(protocol_id, raw_data)
            self.parse_and_load_chain_tvl(protocol_id, raw_data)
            
            self.conn.commit()
            print(f"✓ Loaded {slug}")
            return True
            
        except Exception as e:
            self.conn.rollback()
            print(f"✗ Error loading {slug}: {e}")
            return False