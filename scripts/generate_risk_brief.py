"""
Generate Daily Risk Brief in Markdown format.

Updated for the category-aware risk monitor:
  - Queries protocol_risk_metrics_daily for risk scores
  - Includes CRIT severity level and points per alert
  - Reports coverage issues (missing data, low pool coverage)
  - Shows risk score attribution breakdown
"""
import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date


def get_db_connection():
    """Get PostgreSQL connection."""
    return psycopg2.connect(
        os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5433/protocol_health'),
        cursor_factory=RealDictCursor
    )


def generate_risk_brief(target_date=None):
    """Generate markdown risk brief with category-aware risk data."""
    if target_date is None:
        target_date = date.today()

    conn = get_db_connection()
    cur = conn.cursor()

    # ── 1. Fetch alerts with points ──────────────────────────────────────
    cur.execute("""
        SELECT
            a.date,
            p.name          AS protocol_name,
            p.category,
            a.alert_type,
            a.severity,
            a.points,
            a.value_numeric,
            a.message
        FROM alerts_daily a
        JOIN protocols p ON a.protocol_id = p.protocol_id
        WHERE a.date = %s
        ORDER BY
            CASE a.severity
                WHEN 'crit' THEN 0
                WHEN 'high' THEN 1
                WHEN 'med'  THEN 2
                WHEN 'low'  THEN 3
            END,
            a.points DESC NULLS LAST,
            p.name
    """, (target_date,))
    alerts = cur.fetchall()

    # ── 2. Severity summary ──────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(DISTINCT protocol_id)                                   AS protocols_with_alerts,
            COUNT(*)                                                       AS total_alerts,
            SUM(CASE WHEN severity = 'crit' THEN 1 ELSE 0 END)           AS crit_count,
            SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END)           AS high_count,
            SUM(CASE WHEN severity = 'med'  THEN 1 ELSE 0 END)           AS med_count,
            SUM(CASE WHEN severity = 'low'  THEN 1 ELSE 0 END)           AS low_count,
            COALESCE(SUM(points), 0)                                       AS total_points
        FROM alerts_daily
        WHERE date = %s
    """, (target_date,))
    summary = cur.fetchone()

    # ── 3. Per-protocol risk scores ──────────────────────────────────────
    cur.execute("""
        SELECT
            p.name          AS protocol_name,
            p.category,
            r.risk_score,
            r.coverage_pct,
            r.pool_count_80,
            r.risk_flags
        FROM protocol_risk_metrics_daily r
        JOIN protocols p ON r.protocol_id = p.protocol_id
        WHERE r.date = %s
        ORDER BY r.risk_score DESC NULLS LAST
    """, (target_date,))
    risk_rows = cur.fetchall()

    # ── 4. Coverage issues (protocols with low coverage or missing data) ─
    coverage_issues = []
    for row in risk_rows:
        flags = row.get("risk_flags") or {}
        if isinstance(flags, str):
            try:
                flags = json.loads(flags)
            except (json.JSONDecodeError, TypeError):
                flags = {}
        cov = float(row.get("coverage_pct") or 0)
        issues = []
        if cov < 0.5:
            issues.append(f"low pool coverage ({cov:.0%})")
        if flags.get("MISSING_BORROW_FIELDS"):
            issues.append("missing borrow fields")
        if flags.get("MISSING_TOTALS_FIELDS"):
            issues.append("missing totals fields")
        if flags.get("INSUFFICIENT_HISTORY"):
            issues.append("insufficient history")
        if flags.get("coverage_guardrail_applied"):
            issues.append("coverage guardrail applied (lending points ×0.7)")
        if issues:
            coverage_issues.append((row["protocol_name"], issues))

    cur.close()
    conn.close()

    # ── Build Markdown ───────────────────────────────────────────────────
    md = "# Protocol Risk Monitor — Daily Brief\n\n"
    md += f"**Date:** {target_date}\n\n"
    md += "---\n\n"

    # Executive summary
    md += "## Executive Summary\n\n"
    if summary["total_alerts"] == 0:
        md += "✅ **All systems healthy** — No alerts detected today.\n\n"
    else:
        md += (
            f"⚠️ **{summary['total_alerts']} alerts** across "
            f"**{summary['protocols_with_alerts']} protocols** "
            f"({summary['total_points']} total risk points)\n\n"
        )
        md += "| Severity | Count |\n"
        md += "|----------|------:|\n"
        md += f"| 🔴 CRIT  | {summary['crit_count']} |\n"
        md += f"| 🟠 HIGH  | {summary['high_count']} |\n"
        md += f"| 🟡 MED   | {summary['med_count']} |\n"
        md += f"| 🟢 LOW   | {summary['low_count']} |\n\n"

    # Risk score leaderboard
    if risk_rows:
        md += "---\n\n"
        md += "## Risk Scores\n\n"
        md += "| Protocol | Category | Risk Score | Coverage | Pools (80%) |\n"
        md += "|----------|----------|----------:|----------:|------------:|\n"
        for row in risk_rows:
            score = row["risk_score"] or 0
            cov = float(row["coverage_pct"] or 0)
            pools = row["pool_count_80"] or 0
            score_icon = "🔴" if score >= 60 else ("🟠" if score >= 35 else ("🟡" if score >= 15 else "🟢"))
            md += (
                f"| {row['protocol_name']} | {row['category']} "
                f"| {score_icon} {score} | {cov:.0%} | {pools} |\n"
            )
        md += "\n"

    # Coverage issues
    if coverage_issues:
        md += "---\n\n"
        md += "## ⚠️ Coverage Issues\n\n"
        for proto_name, issues in coverage_issues:
            md += f"- **{proto_name}**: {'; '.join(issues)}\n"
        md += "\n"

    # Detailed alerts
    if alerts:
        md += "---\n\n"
        md += "## Detailed Alerts\n\n"
        md += "| # | Protocol | Category | Alert | Severity | Points | Details |\n"
        md += "|--:|----------|----------|-------|----------|-------:|--------:|\n"

        for i, alert in enumerate(alerts, 1):
            severity_emoji = {
                'crit': '🔴',
                'high': '🟠',
                'med': '🟡',
                'low': '🟢',
            }
            emoji = severity_emoji.get(alert["severity"], "⚪")
            pts = alert.get("points") or 0
            value = f"{float(alert['value_numeric']):.2f}" if alert.get("value_numeric") else "—"
            msg = (alert.get("message") or "").replace("|", "/")
            # Truncate long messages for table
            if len(msg) > 60:
                msg = msg[:57] + "..."
            md += (
                f"| {i} | {alert['protocol_name']} | {alert['category']} "
                f"| `{alert['alert_type']}` | {emoji} {alert['severity'].upper()} "
                f"| {pts} | {msg} |\n"
            )
        md += "\n"

    # Next steps
    md += "---\n\n"
    md += "## Next Steps\n\n"

    if summary["crit_count"] and summary["crit_count"] > 0:
        md += "1. **🔴 CRITICAL alerts require immediate investigation**\n"
        md += "2. Review affected protocols for potential exploits, depegs, or liquidity crises\n"
        md += "3. Cross-reference with on-chain data and protocol governance channels\n\n"
    elif summary["high_count"] and summary["high_count"] > 0:
        md += "1. **Investigate all HIGH severity alerts** within the next few hours\n"
        md += "2. Monitor protocols with significant TVL drops for potential migrations\n"
        md += "3. Review chain concentration changes for liquidity risk\n\n"
    elif summary["total_alerts"] and summary["total_alerts"] > 0:
        md += "1. Monitor flagged protocols over the next 24-48 hours\n"
        md += "2. Review historical trends for context\n\n"
    else:
        md += "1. Continue normal monitoring\n"
        md += "2. Review weekly trends on next scheduled report\n\n"

    if coverage_issues:
        md += (
            f"**Note:** {len(coverage_issues)} protocol(s) have coverage issues. "
            "Run `make discover` and update `config/protocols.yaml` if yields_project "
            "names need correction.\n\n"
        )

    md += "---\n\n"
    md += "*Generated by Protocol Risk Monitor*\n"

    # Save to file
    filename = f"risk-brief-{target_date}.md"
    with open(filename, "w") as f:
        f.write(md)

    print(f"✓ Risk brief generated: {filename}")
    print(f"  Alerts: {summary['total_alerts']}  "
          f"(CRIT:{summary['crit_count']} HIGH:{summary['high_count']} "
          f"MED:{summary['med_count']} LOW:{summary['low_count']})")
    print(f"  Total risk points: {summary['total_points']}")
    if coverage_issues:
        print(f"  ⚠ Coverage issues: {len(coverage_issues)} protocol(s)")
    return filename


if __name__ == "__main__":
    generate_risk_brief()
