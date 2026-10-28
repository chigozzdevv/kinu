import asyncio
from dataclasses import replace

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from core.account.installation import Installation
from core.auth import AuthenticatedIdentity
from core.config import Settings
from core.errors import AuthorizationError, ResourceConflictError
from core.storage.database import Database
from pydantic import SecretStr
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
TOKEN = "installation-test-token-with-at-least-32-characters"


async def test_only_verified_setup_link_holder_can_claim_and_initialization_is_atomic(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    installation = Installation(database)
    with pytest.raises(AuthorizationError):
        await installation.claim(
            replace(identity, email_verified=False), SecretStr(TOKEN), SecretStr(TOKEN)
        )
    with pytest.raises(AuthorizationError):
        await installation.claim(identity, SecretStr("wrong-token"), SecretStr(TOKEN))
    assert not await installation.is_admin(identity)
    other = replace(identity, subject="user_other", email="other@example.com")
    results = await asyncio.gather(
        installation.claim(identity, SecretStr(TOKEN), SecretStr(TOKEN)),
        installation.claim(other, SecretStr(TOKEN), SecretStr(TOKEN)),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, ResourceConflictError) for result in results) == 1
    winner, loser = (
        (identity, other) if await installation.is_admin(identity) else (other, identity)
    )
    assert not await installation.is_admin(loser)
    await installation.claim(winner, SecretStr(TOKEN), SecretStr(TOKEN))
    saved = await database.collection("installation").find_one({"_id": "main"})
    assert saved and saved["administrators"] == [winner.actor_id]
    assert TOKEN not in str(saved)


async def test_setup_endpoint_requires_authenticated_csrf_and_ignores_email_configuration(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    from datetime import UTC, datetime

    from core.account.store import AccountStore
    from core.auth import AccessControl

    installation = Installation(database)
    app = create_app(
        ApiServices(
            access=AccessControl(AccountStore(database, lambda: datetime.now(UTC))),
            installation=installation,
        ),
        browser=make_browser(identity),
        settings=Settings(setup_token=SecretStr(TOKEN)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        path, body = "/v1/installation/claim", {"token": TOKEN}
        assert (await http.post(path, json=body)).status_code == 401
        assert (
            await http.post(path, headers={"cookie": HEADERS["Cookie"]}, json=body)
        ).status_code == 403
        response = await http.post(path, headers=HEADERS, json=body)
        assert response.status_code == 200 and response.json() == {"administrator": True}
        assert TOKEN not in response.text
        assert await installation.is_admin(identity)
        assert (await http.post(path, headers=HEADERS, json=body)).status_code == 200
