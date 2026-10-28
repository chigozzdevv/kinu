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
	$(UV_ENV) uv run ruff format server packages deploy
	$(UV_ENV) uv run ruff check --fix server packages deploy

lint:
	$(UV_ENV) uv run ruff format --check server packages deploy
	$(UV_ENV) uv run ruff check server packages deploy

type:
	$(UV_ENV) uv run mypy server packages deploy

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
	docker compose up -d --build api

agent:
	docker compose up -d --build agent

db:
	docker compose up -d mongo-setup redis

migrate:
	docker compose run --rm migrate

image:
	docker compose build
