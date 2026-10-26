.PHONY: lock sync format lint type test client verify api image

UV_CACHE_DIR ?= .cache/uv
UV_PYTHON_INSTALL_DIR ?= .cache/python
UV_ENV = UV_CACHE_DIR=$(UV_CACHE_DIR) UV_PYTHON_INSTALL_DIR=$(UV_PYTHON_INSTALL_DIR)

lock:
	$(UV_ENV) uv lock

sync:
	$(UV_ENV) uv sync --all-packages --all-extras --locked
	npm --prefix client ci

format:
	$(UV_ENV) uv run ruff format server packages
	$(UV_ENV) uv run ruff check --fix server packages

lint:
	$(UV_ENV) uv run ruff format --check server packages
	$(UV_ENV) uv run ruff check server packages

type:
	$(UV_ENV) uv run mypy server packages

test:
	$(UV_ENV) uv run pytest

client:
	npm --prefix client run check

verify: lint type test client

api:
	$(UV_ENV) uv run uvicorn api.main:app --host 127.0.0.1 --port 8000

image:
	docker build --file server/api/Dockerfile --tag kinu-api:local .
