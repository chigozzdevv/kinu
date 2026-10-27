import os
import secrets
from collections.abc import AsyncIterator

import pytest
from core.auth import AuthenticatedIdentity
from core.storage.database import Database


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    url = os.environ.get("KINU_TEST_DATABASE_URL")
    if not url:
        pytest.skip("run make storage to verify PostgreSQL persistence")
    database = Database(url)
    await database.open()
    try:
        await database.migrate()
        yield database
    finally:
        await database.close()


@pytest.fixture
def identity() -> AuthenticatedIdentity:
    return AuthenticatedIdentity(
        subject=f"issuer#{secrets.token_hex(8)}",
        issuer="https://identity.example",
        email=f"{secrets.token_hex(8)}@example.com",
        email_verified=True,
        display_name="Workspace owner",
    )
