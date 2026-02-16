"""
Fetch protocol data from DeFiLlama API
"""
import os
import json
import requests
from datetime import datetime
from typing import Dict, List, Optional
import time

class DeFiLlamaClient:
    """Client for DeFiLlama API"""
    
    BASE_URL = "https://api.llama.fi"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Protocol-Health-Monitor/1.0'
        })
    
    def get_protocol(self, slug: str) -> Optional[Dict]:
        """
        Fetch protocol data by slug
        
        Args:
            slug: Protocol slug (e.g., 'aave', 'uniswap-v3')
            
        Returns:
            Dict with protocol data including TVL history and chain breakdown
        """
        url = f"{self.BASE_URL}/protocol/{slug}"
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching {slug}: {e}")
            return None
    
    def fetch_all_protocols(self, slugs: List[str]) -> Dict[str, Dict]:
        """
        Fetch data for multiple protocols
        
        Args:
            slugs: List of protocol slugs
            
        Returns:
            Dict mapping slug to protocol data
        """
        results = {}
        
        for slug in slugs:
            print(f"Fetching {slug}...")
            data = self.get_protocol(slug)
            
            if data:
                results[slug] = data
                print(f"✓ {slug}: {len(data.get('tvl', []))} TVL datapoints")
            else:
                print(f"✗ {slug}: Failed to fetch")
            
            # Be nice to the API
            time.sleep(0.5)
        
        return results


def main():
    """Test the client"""
    protocols = ['aave', 'sky', 'uniswap-v3', 'curve', 'lido', 'gmx']
    
    client = DeFiLlamaClient()
    results = client.fetch_all_protocols(protocols)
    
    print(f"\nFetched {len(results)}/{len(protocols)} protocols")
    
    # Save to file for inspection
    output_path = "defillama_test_output.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"Results saved to {output_path}")


if __name__ == "__main__":
    main()
