#!/bin/bash
# deploy_fees_upgrade.sh
# Schema upgrade and fees integration deployment

set -e

echo "=== Protocol Health Monitor - Fees Schema Upgrade ==="
echo ""

# Step 1: Apply schema changes
echo "Step 1: Applying schema upgrade..."
docker compose exec -T postgres psql -U postgres -d protocol_health < schema_upgrade.sql
echo "✓ Schema upgraded"
echo ""

# Step 2: Copy fees ingestion script
echo "Step 2: Installing fees ingestion script..."
docker cp ingest_fees.py protocol_health_api:/app/ingest/
echo "✓ Fees ingestion script installed"
echo ""

# Step 3: Copy dbt model
echo "Step 3: Installing dbt fees model..."
docker cp mart_protocol_fees.sql protocol_health_api:/app/dbt/models/
echo "✓ dbt model installed"
echo ""

# Step 4: Run initial fees ingestion
echo "Step 4: Running initial fees data load..."
docker compose exec api python /app/ingest/ingest_fees.py
echo "✓ Initial fees data loaded"
echo ""

# Step 5: Run dbt to create fees mart
echo "Step 5: Running dbt transformations..."
docker compose exec api sh -c "cd /app/dbt && dbt run --profiles-dir . --models mart_protocol_fees"
echo "✓ dbt fees model created"
echo ""

# Step 6: Update API with new endpoint
echo "Step 6: Updating API..."
docker cp fees_endpoint.py protocol_health_api:/tmp/
docker compose exec api sh -c "cat /tmp/fees_endpoint.py >> /app/api/app.py"
docker compose restart api
echo "✓ API updated and restarted"
echo ""

echo "=== Upgrade Complete! ==="
echo ""
echo "New features available:"
echo "  • Historical fees data stored in database"
echo "  • Real 30-day fee trends (no more simulation)"
echo "  • API endpoint: /api/fees/<protocol_slug>"
echo "  • Auto-refresh every 5 minutes with main data"
echo ""
echo "Database stats:"
docker compose exec postgres psql -U postgres -d protocol_health -c "SELECT COUNT(*) as fees_records FROM protocol_fees;"
echo ""
echo "Next: Update dashboard to use /api/fees endpoint for real charts!"
