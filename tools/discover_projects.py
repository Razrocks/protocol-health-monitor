"""
Discover yields_project names from DeFiLlama Yields API.

Usage:
    python -m tools.discover_projects                   # search all configured slugs
    python -m tools.discover_projects --search aave     # free-text search
    python -m tools.discover_projects --list-all        # dump every unique project name

The yields API uses a `project` field that must exactly match the value in
config/protocols.yaml → yields_project.  This CLI helps you find that value.
"""

import argparse
import json
import os
import sys
from collections import defaultdict

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_CONFIG = os.path.join(ROOT, "config", "api.yaml")
PROTO_CONFIG = os.path.join(ROOT, "config", "protocols.yaml")


def load_yaml(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def fetch_pools(api_url: str) -> list[dict]:
    """Fetch the full pools snapshot from yields API."""
    print(f"  Fetching pools from {api_url} ...")
    resp = requests.get(api_url, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    pools = payload.get("data", payload) if isinstance(payload, dict) else payload
    print(f"  Got {len(pools)} pools")
    return pools


def unique_projects(pools: list[dict]) -> dict[str, dict]:
    """Return {project_name: {chains: set, pool_count: int, total_tvl: float}}."""
    info: dict[str, dict] = {}
    for p in pools:
        proj = p.get("project", "")
        if proj not in info:
            info[proj] = {"chains": set(), "pool_count": 0, "total_tvl": 0.0}
        info[proj]["chains"].add(p.get("chain", "unknown"))
        info[proj]["pool_count"] += 1
        info[proj]["total_tvl"] += float(p.get("tvlUsd", 0) or 0)
    return info


def search_projects(pools: list[dict], query: str) -> list[tuple[str, dict]]:
    """Case-insensitive search across project names."""
    query_lower = query.lower()
    proj_info = unique_projects(pools)
    matches = [
        (name, meta)
        for name, meta in proj_info.items()
        if query_lower in name.lower()
    ]
    matches.sort(key=lambda x: x[1]["total_tvl"], reverse=True)
    return matches


def format_tvl(tvl: float) -> str:
    if tvl >= 1_000_000_000:
        return f"${tvl / 1_000_000_000:.2f}B"
    if tvl >= 1_000_000:
        return f"${tvl / 1_000_000:.2f}M"
    if tvl >= 1_000:
        return f"${tvl / 1_000:.2f}K"
    return f"${tvl:.0f}"


def print_matches(matches: list[tuple[str, dict]], header: str = "Results"):
    if not matches:
        print("\n  No matches found.\n")
        return
    sep = "  " + "-" * 70
    print(f"\n  {header} ({len(matches)} found):")
    print(sep)
    print(f"  {'Project Name':<35} {'Pools':>6}  {'TVL':>12}  Chains")
    print(sep)
    for name, meta in matches:
        chains = ", ".join(sorted(meta["chains"]))
        if len(chains) > 30:
            chains = chains[:27] + "..."
        print(f"  {name:<35} {meta['pool_count']:>6}  {format_tvl(meta['total_tvl']):>12}  {chains}")
    print(sep + "\n")



def cmd_auto_discover(pools: list[dict]):
    """For each protocol in protocols.yaml, search for matching yields projects."""
    proto_cfg = load_yaml(PROTO_CONFIG)
    protocols = proto_cfg.get("protocols", [])

    print("")
    print("+" + "=" * 66 + "+")
    print("|          Protocol <-> Yields Project Discovery                  |")
    print("+" + "=" * 66 + "+")

    suggestions = {}

    for proto in protocols:
        name = proto["name"]
        slug = proto.get("protocol_slug", "")
        current = proto.get("yields_project", "")
        needs_update = (current == "REPLACE_ME" or not current)

        print(f"\n  Protocol: {name}  (slug: {slug})")
        if not needs_update:
            print(f"  [OK] Already configured: yields_project = \"{current}\"")
            proj_info = unique_projects(pools)
            if current in proj_info:
                meta = proj_info[current]
                print(f"    Confirmed: {meta['pool_count']} pools, {format_tvl(meta['total_tvl'])} TVL")
            else:
                print(f"    [!] WARNING: \"{current}\" not found in yields API!")
            continue

        search_terms = [name, slug.replace("-", " ")]
        for word in name.split():
            if len(word) > 2 and word.lower() not in ("v3", "v4", "the"):
                search_terms.append(word)

        all_matches = {}
        for term in search_terms:
            for match_name, match_meta in search_projects(pools, term):
                if match_name not in all_matches:
                    all_matches[match_name] = match_meta

        matches = sorted(all_matches.items(), key=lambda x: x[1]["total_tvl"], reverse=True)

        if matches:
            print_matches(matches, header=f"Candidates for {name}")
            top_name = matches[0][0]
            suggestions[name] = top_name
            print(f"  -> Suggested: yields_project = \"{top_name}\"")
        else:
            print(f"  [X] No matches found. Try: python -m tools.discover_projects --search <keyword>")

    if suggestions:
        print("\n" + "=" * 68)
        print("  SUMMARY - Suggested yields_project values:")
        print("=" * 68)
        for proto_name, proj_name in suggestions.items():
            print(f"    {proto_name:<20} -> \"{proj_name}\"")
        print()
        print("  Update config/protocols.yaml with these values, then run the pipeline.")
        print()


def cmd_search(pools: list[dict], query: str):
    """Free-text search across project names."""
    matches = search_projects(pools, query)
    print_matches(matches, header=f"Search results for '{query}'")


def cmd_list_all(pools: list[dict]):
    """List every unique project name, sorted by TVL."""
    proj_info = unique_projects(pools)
    matches = sorted(proj_info.items(), key=lambda x: x[1]["total_tvl"], reverse=True)
    print_matches(matches, header="All projects (sorted by TVL)")
    print(f"  Total unique projects: {len(matches)}")


def cmd_export_json(pools: list[dict], output_path: str):
    """Export project discovery data as JSON."""
    proj_info = unique_projects(pools)
    export = []
    for name, meta in sorted(proj_info.items(), key=lambda x: x[1]["total_tvl"], reverse=True):
        export.append({
            "project": name,
            "pool_count": meta["pool_count"],
            "total_tvl_usd": round(meta["total_tvl"], 2),
            "chains": sorted(meta["chains"]),
        })
    with open(output_path, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\n  Exported {len(export)} projects to {output_path}\n")



def main():
    parser = argparse.ArgumentParser(
        description="Discover yields_project names from DeFiLlama yields API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m tools.discover_projects                    # auto-discover for configured protocols
  python -m tools.discover_projects --search aave      # search for 'aave'
  python -m tools.discover_projects --search "curve"   # search for 'curve'
  python -m tools.discover_projects --list-all         # list every project
  python -m tools.discover_projects --export out.json  # export as JSON
        """,
    )
    parser.add_argument("--search", "-s", type=str, help="Free-text search for a project name")
    parser.add_argument("--list-all", "-l", action="store_true", help="List all unique project names")
    parser.add_argument("--export", "-e", type=str, help="Export project data to JSON file")

    args = parser.parse_args()

    api_cfg = load_yaml(API_CONFIG)
    yields_url = api_cfg.get("endpoints", {}).get("yields_pools", "https://yields.llama.fi/pools")

    pools = fetch_pools(yields_url)

    if args.search:
        cmd_search(pools, args.search)
    elif args.list_all:
        cmd_list_all(pools)
    elif args.export:
        cmd_export_json(pools, args.export)
    else:
        cmd_auto_discover(pools)


if __name__ == "__main__":
    main()
