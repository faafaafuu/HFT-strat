PY := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python; fi)
GRAPHIFY := $(shell if [ -x .venv/bin/graphify ]; then echo .venv/bin/graphify; else echo graphify; fi)
TIMEFRAME ?= 1m
DAYS ?= 30
TLS_HOST ?= 84.247.166.53
TLS_PORT ?= 9443

dev:
	docker compose -f docker-compose.dev.yml up

dev-down:
	docker compose -f docker-compose.dev.yml down

prod:
	docker compose up -d --build

logs:
	docker logs -f market-heat-signal-bot

logs-web:
	docker logs -f market-heat-signal-bot-web

web-logs: logs-web

logs-dev:
	docker logs -f market-heat-signal-bot-dev

logs-web-dev:
	docker logs -f market-heat-signal-bot-web-dev

restart-dev:
	docker compose -f docker-compose.dev.yml restart

web-restart:
	docker compose restart web

web-health:
	curl -fsS http://127.0.0.1:8080/health

tls-cert:
	mkdir -p certs
	openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
		-keyout certs/dashboard.key \
		-out certs/dashboard.crt \
		-subj "/CN=$(TLS_HOST)" \
		-addext "subjectAltName=IP:$(TLS_HOST),IP:127.0.0.1,DNS:localhost"

https-health:
	curl -k -fsS https://127.0.0.1:$(TLS_PORT)/health

backup:
	$(PY) -m tools.backup_database

download-history:
	$(PY) -m tools.download_history --symbol $(SYMBOL) --timeframe $(TIMEFRAME) --days $(DAYS)

backtest:
	$(PY) -m tools.run_backtest --strategy $(STRATEGY) --symbol $(SYMBOL) --timeframe $(TIMEFRAME) --days $(DAYS)

job-worker:
	$(PY) -m tools.run_jobs

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
