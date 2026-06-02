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

