API_DIR := apps/api
COMPOSE_FILE := deploy/docker/compose.yaml
COMPOSE := docker compose -f $(COMPOSE_FILE)

.PHONY: api-install api-lint api-test api-migrate api-revision console-install console-dev console-build dev-up dev-down dev-logs dev-migrate

api-install:
	python -m pip install -e "$(API_DIR)[dev]"

api-lint:
	ruff check --config $(API_DIR)/pyproject.toml $(API_DIR)/app $(API_DIR)/tests $(API_DIR)/migrations

api-test:
	pytest -c $(API_DIR)/pyproject.toml $(API_DIR)/tests

api-migrate:
	alembic -c $(API_DIR)/alembic.ini upgrade head

api-revision:
	test -n "$(name)" || (echo "Usage: make api-revision name='describe change'"; exit 1)
	alembic -c $(API_DIR)/alembic.ini revision --autogenerate -m "$(name)"

console-install:
	npm install --prefix apps/console

console-dev:
	npm run dev --prefix apps/console -- --host 0.0.0.0

console-build:
	npm run build --prefix apps/console

dev-up:
	$(COMPOSE) up --build -d

dev-migrate:
	$(COMPOSE) exec -T api alembic upgrade head

dev-down:
	$(COMPOSE) down

dev-logs:
	$(COMPOSE) logs -f --tail=200
