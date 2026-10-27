import os
import secrets
from collections.abc import AsyncIterator

import pytest
from core.auth import AuthenticatedIdentity
from core.config import Settings
from core.storage.database import Database
from core.storage.factory import application_database


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    url = os.environ.get("KINU_TEST_DATABASE_URL")
    cluster = os.environ.get("KINU_TEST_DATA_API_CLUSTER_ARN")
    if not url and not cluster:
        pytest.skip("run make storage to verify PostgreSQL persistence")
    database = (
        Database(url)
        if url
        else application_database(
            Settings(
                data_api_cluster_arn=cluster,
                data_api_secret_arn=os.environ.get("KINU_TEST_DATA_API_SECRET_ARN"),
                aws_region=os.environ.get("AWS_REGION", "eu-west-2"),
            )
        )
    )
    await database.open()
    try:
        if not cluster:
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
