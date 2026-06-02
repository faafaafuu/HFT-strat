PY := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python; fi)

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

lint:
	$(PY) -m ruff check app tests

format:
	$(PY) -m black app tests
	$(PY) -m ruff check --fix app tests

typecheck:
	$(PY) -m mypy app

test:
	$(PY) -m pytest

security:
	$(PY) -m bandit -r app

audit:
	$(PY) -m pip_audit

check: lint typecheck test security audit
