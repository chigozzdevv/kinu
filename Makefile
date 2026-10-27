.PHONY: lock sync format lint type test client native storage integration verify api agent db migrate image

UV_CACHE_DIR ?= .cache/uv
UV_PYTHON_INSTALL_DIR ?= .cache/python
UV_ENV = UV_CACHE_DIR=$(UV_CACHE_DIR) UV_PYTHON_INSTALL_DIR=$(UV_PYTHON_INSTALL_DIR)

lock:
	$(UV_ENV) uv lock

sync:
	$(UV_ENV) uv sync --all-packages --all-extras --locked
	npm --prefix client ci
	npm --prefix agent ci

format:
	$(UV_ENV) uv run ruff format server packages infra
	$(UV_ENV) uv run ruff check --fix server packages infra

lint:
	$(UV_ENV) uv run ruff format --check server packages infra
	$(UV_ENV) uv run ruff check server packages infra

type:
	$(UV_ENV) uv run mypy server packages

test:
	$(UV_ENV) uv run pytest

client:
	npm --prefix client run check
	npm --prefix client test

native:
	npm --prefix agent run check
	npm --prefix agent test
	npm --prefix agent run build

storage:
	npm --prefix agent run test:storage

integration:
	npm --prefix agent run test:integration

verify: lint type test client native storage integration

api:
	$(UV_ENV) uv run --env-file .env uvicorn api.main:app --host 127.0.0.1 --port 8000 --no-access-log

agent:
	npm --prefix agent run dev

db:
	npm --prefix agent run db

migrate:
	$(UV_ENV) uv run --env-file .env python -m core.storage.migrate

image:
	docker build --file server/api/Dockerfile --tag kinu-api:local .
