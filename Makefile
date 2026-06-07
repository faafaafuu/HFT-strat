PY := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python; fi)
GRAPHIFY := $(shell if [ -x .venv/bin/graphify ]; then echo .venv/bin/graphify; else echo graphify; fi)

dev:
	docker compose -f docker-compose.dev.yml up

dev-down:
	docker compose -f docker-compose.dev.yml down

prod:
	docker compose up -d --build

logs:
	docker logs -f market-heat-signal-bot

logs-dev:
	docker logs -f market-heat-signal-bot-dev

restart-dev:
	docker compose -f docker-compose.dev.yml restart

backup:
	$(PY) -m tools.backup_database

verify-persistence:
	$(PY) -m tools.verify_persistence

graph:
	mkdir -p project_analysis
	$(PY) -c "import shutil; shutil.rmtree('graphify-out', ignore_errors=True)"
	$(GRAPHIFY) update . --force --no-cluster
	cp graphify-out/graph.json project_analysis/graphify_graph.json
	$(PY) -m tools.architecture_review --output project_analysis --render

graph-review: graph
	@sed -n '1,220p' project_analysis/architecture_review.md

lint:
	$(PY) -m ruff check app tests tools

format:
	$(PY) -m black app tests tools
	$(PY) -m ruff check --fix app tests tools

typecheck:
	$(PY) -m mypy app tools

test:
	$(PY) -m pytest

security:
	$(PY) -m bandit -r app

audit:
	$(PY) -m pip_audit

check: lint typecheck test security audit
