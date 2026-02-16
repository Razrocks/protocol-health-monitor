.PHONY: help setup start stop clean ingest migrate discover api dashboard logs brief test

help:
	@echo "Protocol Health Monitor - Available Commands:"
	@echo ""
	@echo "  Setup & Infrastructure"
	@echo "  ──────────────────────"
	@echo "  make setup       - Initial setup (install deps, init db)"
	@echo "  make start       - Start all services (docker compose)"
	@echo "  make stop        - Stop all services"
	@echo "  make clean       - Remove all containers and volumes"
	@echo ""
	@echo "  Data Pipeline"
	@echo "  ──────────────────────"
	@echo "  make discover    - Discover yields_project names from DeFiLlama"
	@echo "  make migrate     - Run database migrations"
	@echo "  make ingest      - Run full pipeline (fetch, normalize, score)"
	@echo ""
	@echo "  Development"
	@echo "  ──────────────────────"
	@echo "  make api         - Start API server (dev mode)"
	@echo "  make dashboard   - Start dashboard server"
	@echo "  make logs        - View docker compose logs"
	@echo "  make brief       - Generate daily risk brief"
	@echo "  make test        - Run tests"
	@echo ""

setup:
	@echo "Installing Python dependencies..."
	pip install -r requirements.txt
	@echo "Starting PostgreSQL..."
	docker compose up -d postgres
	@echo "Waiting for PostgreSQL..."
	sleep 5
	@echo "Initializing database schema..."
	PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d protocol_health -f db/schema.sql
	@echo "Setup complete!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Run 'make discover' to find yields_project names"
	@echo "  2. Update config/protocols.yaml with discovered names"
	@echo "  3. Run 'make ingest' to start the pipeline"

start:
	@echo "Starting all services..."
	docker compose up -d
	@echo "Services started:"
	@echo "  - PostgreSQL: localhost:5433"
	@echo "  - API: http://localhost:5000"
	@echo "  - Dashboard: http://localhost:8080"

stop:
	@echo "Stopping all services..."
	docker compose down

clean:
	@echo "Removing all containers and volumes..."
	docker compose down -v
	@echo "Cleaned!"

discover:
	@echo "Discovering yields_project names from DeFiLlama..."
	python -m tools.discover_projects

migrate:
	@echo "Running database migrations..."
	PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d protocol_health -f db/migrations/001_category_pools_upgrade.sql
	@echo "Migration complete!"

ingest:
	@echo "Running full data pipeline..."
	python -m ingest.run_pipeline

api:
	@echo "Starting API server (development mode)..."
	cd api && python app.py

dashboard:
	@echo "Starting dashboard server..."
	cd dashboard && python -m http.server 8080

logs:
	docker compose logs -f

brief:
	@echo "Generating daily risk brief..."
	python scripts/generate_risk_brief.py

test:
	@echo "Running tests..."
	pytest tests/ -v
