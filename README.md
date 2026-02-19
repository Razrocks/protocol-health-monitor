# Protocol Health Monitor

Category-aware DeFi risk monitor for 6 major protocols using the **DeFiLlama API**. Tracks TVL momentum, chain dependence, pool concentration, and lending stress (utilization, rate spreads, rate volatility). Produces daily risk scores with attribution + guardrails so missing data does not create fake alarms.

## What it does

Daily checks (auto refreshes ever 5 mins - ability to force refresh most recent data):

- Is protocol TVL breaking down (1D, 7D, 30D)?
- Is value concentrating on one chain (and is that shifting fast)?
- Is liquidity concentrated in a small number of pools?
- For lending protocols only: is utilization spiking or are rates unstable?
- Is the score trustworthy (coverage + materiality gates, never fabricated fields)?

Outputs:

- Overview dashboard
- Protocol detail pages (category-specific)
- Daily risk brief (Markdown export)

## Monitored protocols

| Protocol | Category | monitored |
|---|---|---|
| Aave V3 | Lending | Utilization, rate spread and volatility, pool concentration, chain share, TVL momentum |
| Sky | Lending | Same when data is available |
| Uniswap V3 | DEX | Pool concentration, chain share, TVL momentum |
| Curve | DEX | Pool concentration, chain share, TVL momentum |
| Lido | LSD | TVL trend + chain dependence treated as structural |
| GMX | PERP | TVL trend + chain dependence treated as structural |

## Data sources

This project uses DeFiLlama:

- Protocol TVL + chain breakdown: protocol endpoint (per protocol slug)
- Pool universe: yields pools endpoint (pool concentration + pool selection)
- Lending pool history (where available): lend/borrow chart endpoint (rate volatility + utilization when totals exist)


### Stack

- Ingestion + risk engine: Python
- Storage: PostgreSQL 15 (time series + JSONB snapshots)
- API: Flask REST
- Dashboard: single-file React + Chart.js (served via Nginx)
- Deployment: Docker Compose (db + api + dashboard)

## Risk scoring

Risk score = sum of alert points, capped at 100. Alerts are category-aware and guardrailed.

### Global alerts (all categories)

- TVL 7D Drop: TVL ≤ −8% over 7 days (+40)
- TVL 1D Drop: TVL ≤ −3% in 1 day (+20)
- Top Chain Concentration: top chain ≥ 70% of TVL (+15)
- Concentration Spike: top chain share change ≥ +10pp in 7D (+25)
- Low Coverage: pool field coverage < 50% (+10)
- Data Gap: missing historical comparison points (+10)

### Lending-only alerts (Aave, Sky)

- Utilization: avg ≥ 80% (+25), material pool ≥ 90% (+40)
- Rate spread: material pool spread ≥ 3% (+15), severe ≥ 6% (+25)
- Rate volatility (30D): CV ≥ 0.5 (+10), CV ≥ 1.0 (+20)
- Pool concentration: pool_count_80 < 3 (+25)
- Tail pool spike: non-material pool ≥ 90% util (low, capped impact)

### Structural labels (LSD, PERP)

Single-chain dominance is labeled **Structural Dependence** (informational) rather than penalized.

### Guardrails

- Materiality gate: CRIT requires pool share ≥ 5% of protocol TVL
- Coverage guardrail: if pool field coverage < 50%, lending-only points are multiplied by 0.7
- Utilization coverage guardrail: if utilization field coverage < 50%, CRIT is blocked

Every score stores flag → points → metric values → thresholds + guardrail adjustments.

## Dashboard

### Overview

- Protocol leaderboard (risk score + alert counts)
- Indexed TVL overlay (daily points, base=100; 30D/90D/1Y)
- Risk drivers chart (alert counts by type, stacked by severity)
- Concentration summary (top pool share vs top chain share)
- Protocol heatmap (TVL deltas, coverage, alerts, category metrics)

### Protocol detail

- Category-specific monitoring cards
- Coverage bar
- Top pools concentration (Top 1/5/10 share)
- Pool tables (with per-pool flags where applicable)
- 14D grouped alert history
- Score attribution (including guardrail effects)

### Daily brief

- Date-navigable alert summary + severity counts
- Markdown export

## Quick start

Run full stack:

```bash
docker compose up -d

Services (update if your compose differs):

Postgres: 5433

API: 5000

Dashboard: 8080

Dashboard: http://localhost:8080

Run pipeline:

docker exec -it protocol_health_api python /app/ingest/run_ingestion.py

API endpoints:

GET /api/overview

GET /api/protocol/<id>

GET /api/protocol/<id>/top-pools

GET /api/tvl-history?days=N

GET /api/alert-drivers

GET /api/daily-brief?date=YYYY-MM-DD
