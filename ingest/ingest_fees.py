"""
Fetch and load fees/revenue data from DeFiLlama
"""
import requests
import psycopg2
from datetime import datetime, timedelta
import time

DB_CONFIG = {
    'host': 'postgres',
    'port': 5432,
    'user': 'postgres',
    'password': 'postgres',
    'dbname': 'protocol_health'
}

PROTOCOLS = [
    'aave',
    'sky',
    'uniswap-v3',
    'curve-finance',
    'lido',
    'gmx'
]

def fetch_protocol_fees_history(slug, days=30):
    """Fetch historical fees data for a protocol from DeFiLlama"""
    try:
        # DeFiLlama fees history endpoint
        url = f'https://api.llama.fi/summary/fees/{slug}?dataType=dailyFees'
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Get today's summary for current values
        summary_url = f'https://api.llama.fi/summary/fees/{slug}'
        summary_response = requests.get(summary_url, timeout=10)
        summary = summary_response.json() if summary_response.ok else {}
        
        historical_data = []
        
        # Parse historical data
        if 'totalDataChart' in data:
            for entry in data['totalDataChart']:
                # Entry format: [timestamp, fees]
                timestamp = entry[0]
                fees_24h = entry[1] if len(entry) > 1 else 0
                date = datetime.fromtimestamp(timestamp).date()
                
                # Estimate revenue as 30% of fees (industry standard)
                revenue_24h = fees_24h * 0.3
                
                historical_data.append({
                    'date': date,
                    'fees_24h': fees_24h,
                    'revenue_24h': revenue_24h,
                    'users_24h': None
                })
        
        # If no historical data, use current day summary
        if not historical_data and summary:
            today = datetime.now().date()
            historical_data.append({
                'date': today,
                'fees_24h': summary.get('total24h', 0),
                'revenue_24h': summary.get('dailyRevenue', summary.get('total24h', 0) * 0.3),
                'users_24h': summary.get('dailyActiveUsers', None)
            })
        
        return historical_data
        
    except Exception as e:
        print(f"Error fetching fees history for {slug}: {e}")
        return []

def load_fees_to_db():
    """Load fees data into PostgreSQL"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    try:
        # Get protocol IDs
        cur.execute("SELECT protocol_id, slug FROM protocols WHERE enabled = true")
        protocol_map = {row[1]: row[0] for row in cur.fetchall()}
        
        total_records = 0
        
        for slug in PROTOCOLS:
            if slug not in protocol_map:
                print(f"Protocol {slug} not found in database, skipping")
                continue
            
            print(f"Fetching fees history for {slug}...")
            fees_history = fetch_protocol_fees_history(slug, days=90)
            
            if not fees_history:
                print(f"  ✗ No data for {slug}")
                continue
            
            protocol_id = protocol_map[slug]
            records_inserted = 0
            
            # Insert all historical data
            for entry in fees_history:
                cur.execute("""
                    INSERT INTO protocol_fees (protocol_id, date, fees_24h, revenue_24h, users_24h)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (protocol_id, date)
                    DO UPDATE SET
                        fees_24h = EXCLUDED.fees_24h,
                        revenue_24h = EXCLUDED.revenue_24h,
                        users_24h = EXCLUDED.users_24h,
                        created_at = CURRENT_TIMESTAMP
                """, (
                    protocol_id,
                    entry['date'],
                    entry['fees_24h'],
                    entry['revenue_24h'],
                    entry['users_24h']
                ))
                records_inserted += 1
            
            conn.commit()
            total_records += records_inserted
            print(f"  ✓ Loaded {records_inserted} days of fees data for {slug}")
            
            time.sleep(0.5)  # Rate limiting
        
        print(f"\n✓ Successfully loaded {total_records} total fee records")
        
    except Exception as e:
        conn.rollback()
        print(f"Error loading fees: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    load_fees_to_db()
