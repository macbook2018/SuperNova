API_DIR := apps/api
COMPOSE := docker compose -f deploy/docker/compose.yaml

.PHONY: api-install api-lint api-test dev-up dev-down dev-logs

api-install:
	cd $(API_DIR) && python -m pip install -e ".[dev]"

api-lint:
	cd $(API_DIR) && ruff check app tests

api-test:
	cd $(API_DIR) && pytest

dev-up:
	$(COMPOSE) up --build -d

dev-down:
	$(COMPOSE) down

dev-logs:
	$(COMPOSE) logs -f --tail=200
