"""
Main ingestion pipeline orchestrator
"""
import os
import sys
from datetime import datetime
from fetch_defillama import DeFiLlamaClient
from load_raw_to_pg_fixed import PostgresLoader


PROTOCOLS = ['aave', 'sky', 'uniswap-v3','curve-finance', 'lido', 'gmx']


def run_ingestion(git_sha: str = None):
    """
    Complete ingestion pipeline:
    1. Fetch data from DeFiLlama
    2. Load into PostgreSQL
    3. Track run status
    4. Fetch and load fees data
    """
    print("=" * 60)
    print("Protocol Health Monitor - Data Ingestion")
    print(f"Started: {datetime.now()}")
    print("=" * 60)
    
    # Step 1: Fetch TVL data
    print("\n[1/4] Fetching TVL data from DeFiLlama...")
    client = DeFiLlamaClient()
    raw_data = client.fetch_all_protocols(PROTOCOLS)
    
    if not raw_data:
        print("ERROR: No data fetched")
        return False
    
    print(f"✓ Fetched {len(raw_data)}/{len(PROTOCOLS)} protocols")
    
    # Step 2: Load TVL into database
    print("\n[2/4] Loading TVL data into PostgreSQL...")
    loader = PostgresLoader()
    
    try:
        loader.connect()
        run_id = loader.create_ingestion_run(git_sha)
        print(f"Created run_id: {run_id}")
        
        success_count = 0
        for slug, data in raw_data.items():
            if loader.load_protocol_data(run_id, slug, data):
                success_count += 1
        
        # Update run status
        status = 'success' if success_count == len(raw_data) else 'failed'
        notes = f"Loaded {success_count}/{len(raw_data)} protocols"
        loader.update_ingestion_run(run_id, status, notes)
        
        print(f"\n✓ Loaded {success_count}/{len(raw_data)} protocols")
        
        tvl_success = success_count == len(raw_data)
        
    except Exception as e:
        print(f"\nERROR: {e}")
        if 'run_id' in locals():
            loader.update_ingestion_run(run_id, 'failed', str(e))
        tvl_success = False
        
    finally:
        loader.close()
    
    # Step 3: Fetch and load fees data
    print("\n[3/4] Fetching fees data from DeFiLlama...")
    try:
        from ingest_fees import load_fees_to_db
        load_fees_to_db()
        fees_success = True
    except Exception as e:
        print(f"ERROR loading fees: {e}")
        fees_success = False
    
    # Step 4: Summary
    print("\n[4/4] Ingestion Summary")
    print(f"  TVL Data:  {'✓ Success' if tvl_success else '✗ Failed'}")
    print(f"  Fees Data: {'✓ Success' if fees_success else '✗ Failed'}")
    
    print("\n" + "=" * 60)
    print(f"Finished: {datetime.now()}")
    print("=" * 60)
    
    return tvl_success and fees_success


if __name__ == "__main__":
    git_sha = os.getenv('GITHUB_SHA', None)
    success = run_ingestion(git_sha)
    sys.exit(0 if success else 1)