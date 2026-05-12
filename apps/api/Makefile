.PHONY: dev-light dev down migrate test-unit test-integration test-all lint typecheck demo

# Start only postgres, redis, and api; run workers on host for faster iteration
dev-light:
	docker compose up -d postgres redis api

# Start all 6 services (postgres, redis, api, worker_pipeline, worker_runtime, beat)
dev:
	docker compose up -d

# Stop and remove containers (keeps volumes)
down:
	docker compose down

# Run Alembic migrations inside the running api container
migrate:
	docker compose exec api alembic -c alembic.ini upgrade head

# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

# Fast feedback loop — unit tests only
test-unit:
	cd apps/api && python -m pytest tests/unit -x --tb=short -q

# Integration tests (requires postgres + redis running)
test-integration:
	cd apps/api && python -m pytest tests/integration -x --tb=short

# Full suite with coverage report
test-all:
	cd apps/api && python -m pytest tests/ -x --tb=short --cov=app --cov-report=term-missing

# ----------------------------------------------------------------------------
# Code quality
# ----------------------------------------------------------------------------

lint:
	cd apps/api && python -m ruff check app/ tests/

typecheck:
	cd apps/api && python -m mypy app/ --ignore-missing-imports

# ----------------------------------------------------------------------------
# Demo
# ----------------------------------------------------------------------------

# Run the end-to-end M1 demo (requires ADMIN_KEY env var and services running)
demo:
	bash scripts/demo_m1.sh
