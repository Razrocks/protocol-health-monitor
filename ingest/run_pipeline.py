"""
Main ingestion pipeline orchestrator (10-step pipeline)
Replaces run_ingestion.py with category-aware risk monitoring pipeline
"""
import os
import sys
import json
import psycopg2
import requests
from datetime import datetime, date
from psycopg2.extras import Json

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ingest.protocol_fetch import load_config, validate_config, fetch_all_protocols, load_protocol_tvl
from ingest.pools_fetch import fetch_pools, store_raw_pools, map_and_store_pools, select_top_pools
from ingest.pool_history_fetch import fetch_lending_pool_history
from ingest.normalize import compute_lending_metrics, compute_tvl_deltas, compute_chain_concentration
from risk.alerts import generate_alerts
from risk.scoring import compute_risk_scores


def get_connection():
    """Get database connection"""
    conn_string = os.getenv(
        'DATABASE_URL',
        'postgresql://postgres:postgres@localhost:5433/protocol_health'
    )
    conn = psycopg2.connect(conn_string)
    conn.autocommit = False
    return conn


def create_run(conn, git_sha=None):
    """Step A: Start a new pipeline run"""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ingestion_runs (started_at, status, git_sha)
               VALUES (%s, %s, %s) RETURNING run_id""",
            (datetime.now(), 'running', git_sha)
        )
        run_id = cur.fetchone()[0]
        conn.commit()
    return run_id


def finish_run(conn, run_id, status, error_message=None, notes=None):
    """Step J: Mark run complete"""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE ingestion_runs
               SET finished_at = %s, status = %s, error_message = %s, notes = %s
               WHERE run_id = %s""",
            (datetime.now(), status, error_message, notes, run_id)
        )
        conn.commit()


def run_pipeline(git_sha=None):
    """Execute the full 10-step pipeline"""
    print("=" * 60)
    print("Protocol Health Monitor - Risk Pipeline v2")
    print(f"Started: {datetime.now()}")
    print("=" * 60)

    # Load configuration
    api_config, protocols_config = load_config()

    # Validate config (warn about REPLACE_ME but don't block)
    config_errors = validate_config(protocols_config)
    if config_errors:
        print("\nWARNING: Some protocols have incomplete config:")
        for err in config_errors:
            print(f"  - {err}")
        print("Pipeline will skip these protocols for pool mapping.\n")

    conn = get_connection()
    run_id = None
    session = requests.Session()
    session.headers.update({'User-Agent': 'Protocol-Health-Monitor/2.0'})

    try:
        # ========================================
        # Step A: Start run
        # ========================================
        print("\n[A] Starting pipeline run...")
        run_id = create_run(conn, git_sha)
        print(f"  Run ID: {run_id}")

        # ========================================
        # Step B: Fetch protocol-level TVL JSON
        # ========================================
        print("\n[B] Fetching protocol TVL data...")
        protocol_data = fetch_all_protocols(api_config, protocols_config)

        if not protocol_data:
            raise RuntimeError("No protocol data fetched")

        # Load TVL into database
        for slug, entry in protocol_data.items():
            config = entry['config']
            data = entry['data']

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT protocol_id FROM protocols WHERE protocol_slug = %s OR slug = %s",
                    (slug, slug)
                )
                row = cur.fetchone()

            if row:
                load_protocol_tvl(conn, run_id, row[0], data)

        conn.commit()
        print(f"  Loaded {len(protocol_data)} protocols")

        # ========================================
        # Step C: Fetch pools JSON
        # ========================================
        print("\n[C] Fetching pools snapshot...")
        raw_pools = fetch_pools(session, api_config)

        if raw_pools:
            store_raw_pools(conn, run_id, raw_pools)
            conn.commit()

            # ========================================
            # Step D: Map pools to protocols
            # ========================================
            print("\n[D] Mapping pools to monitored protocols...")
            mapped_count = map_and_store_pools(conn, run_id, raw_pools, protocols_config)
            conn.commit()

            # ========================================
            # Step E: Select top pools (80% cumulative TVL)
            # ========================================
            print("\n[E] Selecting top pools per protocol...")
            pool_selections = select_top_pools(conn, run_id, protocols_config, api_config)
            conn.commit()
        else:
            print("  WARNING: Failed to fetch pools, skipping pool-level analysis")
            pool_selections = {}

        # ========================================
        # Step F: Fetch pool history (lending only)
        # ========================================
        print("\n[F] Fetching lending pool history...")
        try:
            history_stats = fetch_lending_pool_history(
                conn, run_id, api_config, protocols_config, pool_selections
            )
            conn.commit()
        except Exception as e:
            print(f"  WARNING: Pool history fetch failed: {e}")
            print("  Continuing with available data...")
            history_stats = {}

        # ========================================
        # Step G: Compute lending metrics
        # ========================================
        print("\n[G] Computing lending metrics...")
        lending_results = {}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT protocol_id, name, category FROM protocols WHERE enabled = true"
            )
            all_protocols = cur.fetchall()

        for protocol_id, name, category in all_protocols:
            if category == 'LENDING':
                metrics = compute_lending_metrics(conn, protocol_id, pool_selections)
                lending_results[protocol_id] = metrics
                flags = metrics.get('flags', [])
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                util_str = f"util_avg={metrics['lending_util_avg']:.2%}" if metrics['lending_util_avg'] else "no util data"
                print(f"  {name}: {util_str}{flag_str}")

        # ========================================
        # Step H: Compute protocol-level deltas
        # ========================================
        print("\n[H] Computing TVL deltas and chain concentration...")
        tvl_results = {}
        chain_results = {}

        for protocol_id, name, category in all_protocols:
            tvl_deltas = compute_tvl_deltas(conn, protocol_id)
            chain_conc = compute_chain_concentration(conn, protocol_id)

            tvl_results[protocol_id] = tvl_deltas
            chain_results[protocol_id] = chain_conc

            tvl_str = f"1d={tvl_deltas['tvl_1d_pct']:+.2f}%" if tvl_deltas['tvl_1d_pct'] is not None else "1d=N/A"
            print(f"  {name}: {tvl_str}, top_chain={chain_conc.get('top_chain', 'N/A')}")

        # ========================================
        # Step I: Compute alerts + risk score
        # ========================================
        print("\n[I] Computing alerts and risk scores...")

        # Build combined metrics per protocol and store to protocol_risk_metrics_daily
        today = date.today()
        for protocol_id, name, category in all_protocols:
            tvl = tvl_results.get(protocol_id, {})
            chain = chain_results.get(protocol_id, {})
            lending = lending_results.get(protocol_id, {})
            pool_sel = pool_selections.get(protocol_id, {})

            # Compute top_pool_share_total (top pool as % of ALL pool TVL, not just selected)
            top_pool_share_total = None
            if pool_sel.get('top_pool_share') is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COALESCE(SUM(tvl_usd), 0) FROM pools_current
                           WHERE run_id = %s AND protocol_id = %s AND tvl_usd > 0""",
                        (run_id, protocol_id)
                    )
                    total_all = cur.fetchone()[0]
                    if total_all and float(total_all) > 0:
                        # top pool TVL = top_pool_share * selected_tvl_sum
                        sel_sum = float(pool_sel.get('selected_pool_tvl_sum', 0) or 0)
                        if sel_sum > 0 and pool_sel['top_pool_share'] is not None:
                            top_tvl = pool_sel['top_pool_share'] * sel_sum
                            top_pool_share_total = top_tvl / float(total_all)

            metrics = {
                'protocol_name': name,
                'tvl_usd': tvl.get('tvl_usd'),
                'tvl_1d_pct': tvl.get('tvl_1d_pct'),
                'tvl_7d_pct': tvl.get('tvl_7d_pct'),
                'tvl_30d_pct': tvl.get('tvl_30d_pct'),
                'has_data_gap': tvl.get('has_data_gap', False),
                'top_chain': chain.get('top_chain'),
                'top_chain_share_pct': chain.get('top_chain_share_pct'),
                'top_chain_share_change_7d_pp': chain.get('top_chain_share_change_7d_pp'),
                'coverage_pct': pool_sel.get('coverage_pct'),
                'pool_count_80': pool_sel.get('pool_count_80'),
                'top_pool_share': pool_sel.get('top_pool_share'),
                'top_pool_share_total': top_pool_share_total,
                'lending_util_avg': lending.get('lending_util_avg'),
                'lending_util_max': lending.get('lending_util_max'),
                'lending_spread_max': lending.get('lending_spread_max'),
                'lending_spread_median': lending.get('lending_spread_median'),
                'lending_ratio_max': lending.get('lending_ratio_max'),
                'lending_ratio_median': lending.get('lending_ratio_median'),
                'lending_rate_cv_supply_30d': lending.get('lending_rate_cv_supply_30d'),
                'lending_rate_cv_borrow_30d': lending.get('lending_rate_cv_borrow_30d'),
                'metric_coverage_util': lending.get('metric_coverage_util'),
                'metric_coverage_rate': lending.get('metric_coverage_rate'),
                'pool_details': lending.get('pool_details', []),
                'lending_flags': lending.get('flags', []),
            }

            # Generate alerts
            alerts = generate_alerts(protocol_id, category, metrics)

            # Compute risk score
            risk_score, risk_flags = compute_risk_scores(
                protocol_id, category, alerts, metrics
            )

            # Add data flags to risk_flags
            for flag in metrics.get('lending_flags', []):
                risk_flags[flag] = {'points': 0, 'info': True}

            # Store to protocol_risk_metrics_daily
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO protocol_risk_metrics_daily
                       (date, protocol_id, category,
                        tvl_usd, tvl_1d_pct, tvl_7d_pct, tvl_30d_pct,
                        top_chain, top_chain_share_pct, top_chain_share_change_7d_pp,
                        coverage_pct, pool_count_80, top_pool_share, top_pool_share_total,
                        lending_util_avg, lending_util_max,
                        lending_spread_max, lending_spread_median,
                        lending_ratio_max, lending_ratio_median,
                        lending_rate_cv_supply_30d, lending_rate_cv_borrow_30d,
                        metric_coverage_util, metric_coverage_rate,
                        risk_score, risk_flags)
                       VALUES (%s, %s, %s,
                               %s, %s, %s, %s,
                               %s, %s, %s,
                               %s, %s, %s, %s,
                               %s, %s,
                               %s, %s,
                               %s, %s,
                               %s, %s,
                               %s, %s,
                               %s, %s)
                       ON CONFLICT (date, protocol_id) DO UPDATE SET
                        tvl_usd = EXCLUDED.tvl_usd,
                        tvl_1d_pct = EXCLUDED.tvl_1d_pct,
                        tvl_7d_pct = EXCLUDED.tvl_7d_pct,
                        tvl_30d_pct = EXCLUDED.tvl_30d_pct,
                        top_chain = EXCLUDED.top_chain,
                        top_chain_share_pct = EXCLUDED.top_chain_share_pct,
                        top_chain_share_change_7d_pp = EXCLUDED.top_chain_share_change_7d_pp,
                        coverage_pct = EXCLUDED.coverage_pct,
                        pool_count_80 = EXCLUDED.pool_count_80,
                        top_pool_share = EXCLUDED.top_pool_share,
                        top_pool_share_total = EXCLUDED.top_pool_share_total,
                        lending_util_avg = EXCLUDED.lending_util_avg,
                        lending_util_max = EXCLUDED.lending_util_max,
                        lending_spread_max = EXCLUDED.lending_spread_max,
                        lending_spread_median = EXCLUDED.lending_spread_median,
                        lending_ratio_max = EXCLUDED.lending_ratio_max,
                        lending_ratio_median = EXCLUDED.lending_ratio_median,
                        lending_rate_cv_supply_30d = EXCLUDED.lending_rate_cv_supply_30d,
                        lending_rate_cv_borrow_30d = EXCLUDED.lending_rate_cv_borrow_30d,
                        metric_coverage_util = EXCLUDED.metric_coverage_util,
                        metric_coverage_rate = EXCLUDED.metric_coverage_rate,
                        risk_score = EXCLUDED.risk_score,
                        risk_flags = EXCLUDED.risk_flags""",
                    (today, protocol_id, category,
                     metrics['tvl_usd'], metrics['tvl_1d_pct'],
                     metrics['tvl_7d_pct'], metrics['tvl_30d_pct'],
                     metrics['top_chain'], metrics['top_chain_share_pct'],
                     metrics['top_chain_share_change_7d_pp'],
                     metrics['coverage_pct'], metrics['pool_count_80'],
                     metrics['top_pool_share'], metrics.get('top_pool_share_total'),
                     metrics['lending_util_avg'], metrics['lending_util_max'],
                     metrics.get('lending_spread_max'), metrics.get('lending_spread_median'),
                     metrics['lending_ratio_max'], metrics['lending_ratio_median'],
                     metrics['lending_rate_cv_supply_30d'],
                     metrics['lending_rate_cv_borrow_30d'],
                     metrics.get('metric_coverage_util'), metrics.get('metric_coverage_rate'),
                     risk_score, Json(risk_flags))
                )

            # Remove stale alerts for this protocol+date before inserting fresh set
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM alerts_daily WHERE date = %s AND protocol_id = %s",
                    (today, protocol_id)
                )

            # Store alerts to alerts_daily
            for alert in alerts:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO alerts_daily
                           (date, protocol_id, alert_type, severity,
                            value_numeric, threshold_numeric, points, message)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (date, protocol_id, alert_type) DO UPDATE SET
                            severity = EXCLUDED.severity,
                            value_numeric = EXCLUDED.value_numeric,
                            threshold_numeric = EXCLUDED.threshold_numeric,
                            points = EXCLUDED.points,
                            message = EXCLUDED.message""",
                        (today, protocol_id, alert['alert_type'],
                         alert['severity'], alert['value'],
                         alert['threshold'], alert['points'],
                         alert['message'])
                    )

            alert_str = f"{len(alerts)} alerts" if alerts else "no alerts"
            print(f"  {name}: score={risk_score}, {alert_str}")

        conn.commit()

        # ========================================
        # Step J: Mark run success
        # ========================================
        print("\n[J] Finalizing run...")
        notes = f"Protocols: {len(protocol_data)}, Pools mapped: {mapped_count if raw_pools else 0}"
        finish_run(conn, run_id, 'success', notes=notes)

        # Optional: Run fees ingestion
        print("\n[+] Running fees ingestion (optional)...")
        try:
            sys.path.insert(0, os.path.dirname(__file__))
            from ingest_fees import load_fees_to_db
            load_fees_to_db()
            print("  Fees loaded successfully")
        except Exception as e:
            print(f"  Fees ingestion skipped: {e}")

        print("\n" + "=" * 60)
        print(f"Pipeline completed successfully at {datetime.now()}")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()

        if run_id:
            try:
                finish_run(conn, run_id, 'failed', error_message=str(e))
            except Exception:
                pass

        return False

    finally:
        conn.close()


if __name__ == "__main__":
    git_sha = os.getenv('GITHUB_SHA', None)
    success = run_pipeline(git_sha)
    sys.exit(0 if success else 1)
