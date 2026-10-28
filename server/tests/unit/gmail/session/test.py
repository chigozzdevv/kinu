from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from core.errors import ResourceConflictError
from core.storage.database import Database
from mail.google import Google, RequestError
from mail.models import SCOPE, GoogleConfig
from mail.service import Service, digest
from mail.vault import Vault
from pydantic import SecretStr
from tests.mail import MemoryMail

pytestmark = pytest.mark.anyio
CONFIG = GoogleConfig(
    client_id="kinu.apps.googleusercontent.com",
    client_secret=SecretStr("fixture"),
    key=SecretStr("x" * 64),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_oauth_is_bound_to_actor_session_workspace_and_consumed_once() -> None:
    store = MemoryMail()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                    "scope": SCOPE,
                    "expires_in": 3600,
                },
            )
        return httpx.Response(200, json={"emailAddress": "owner@gmail.com"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = Service(
            store,
            Google(CONFIG, client),
            Vault(CONFIG),
            Database("postgresql://unused"),
            "https://kinu.example",
        )
        target = await service.begin("org_one", "actor_one", "session_one", None)
        state = parse_qs(urlsplit(target).query)["state"][0]
        for binding, actor in (("other", "actor_one"), ("session_one", "other")):
            with pytest.raises(ResourceConflictError):
                await service.owner(state, binding, actor)
        with pytest.raises(ResourceConflictError):
            await service.finish("org_two", "actor_one", "session_one", state, "code")
        box = await service.finish("org_one", "actor_one", "session_one", state, "code")
        assert box.status == "setup-required" and box.email == "owner@gmail.com"
        _, item = await store.mailbox("org_one", box.id)
        assert "fixture" not in str(item) and "code" not in str(item)
        assert (
            service.open_grant(box, item["grant"]).refresh_token.get_secret_value()
            == "fixture-refresh"
        )
        with pytest.raises(ResourceConflictError):
            await service.finish("org_one", "actor_one", "session_one", state, "code")
        target = await service.begin("org_one", "actor_one", "session_one", None)
        state = parse_qs(urlsplit(target).query)["state"][0]
        store.items["pending:" + digest(state)]["expires"] = int(datetime.now(UTC).timestamp()) - 1
        with pytest.raises(ResourceConflictError):
            await service.owner(state, "session_one", "actor_one")


async def test_quota_failure_does_not_require_reconnecting_the_account() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RequestError) as error:
            await Google(CONFIG, client).request("GET", "https://gmail.googleapis.com/test")
        assert error.value.status == 403
