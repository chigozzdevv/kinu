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
    url = os.environ.get("KINU_TEST_MONGO_URL")
    if not url:
        pytest.skip("run make storage to verify MongoDB persistence")
    database = Database(url, "kinu_test_" + secrets.token_hex(8))
    await database.open()
    try:
        await database.migrate()
        yield database
    finally:
        cleanup = Database(url, database.documents.name)
        await cleanup.client.drop_database(cleanup.documents.name)
        await cleanup.close()
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
