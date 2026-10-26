FROM python:3.12.13-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv==0.12.3

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY server ./server

RUN uv sync --frozen --no-dev --no-editable --package kinu-server --extra mcp \
    && find /app -type d -name __pycache__ -prune -exec rm -rf {} +

USER 65532:65532

EXPOSE 8080

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
