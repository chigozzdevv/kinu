from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from contracts import ConnectionStatus
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError
from core.storage.database import Database
from executor.vault import Vault
from hosting.lifecycle import Lifecycle
from hosting.models import Begin, Selection
from hosting.service import Service
from pydantic import SecretStr
from tests.storage.hosting.test import SESSION, TOKEN, connect, response, workspace

pytestmark = pytest.mark.anyio


async def test_reconnect_recovers_uncertain_grants_preserves_scope_and_disconnect_erases_token(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    org = await workspace(database, identity)
    vault = Vault(database, SecretStr("private-hosting-key-material-at-least-32"))
    await vault.initialize()
    await vault.configure(
        "vercel", {"slug": "kinu", "client_id": "app-one", "client_secret": "app-secret-private"}
    )
    limited = False

    def remote(request: httpx.Request) -> httpx.Response:
        result = response(request)
        if limited and request.url.path == "/v9/projects":
            return httpx.Response(200, json={"projects": [{"id": "project-two", "name": "Other"}]})
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as http:
        service = Service(database, vault, http, "http://localhost:8080")
        await service.grants.initialize()
        connection_id = await connect(service, org)
        connection = await service.save(
            org, connection_id, "actor_one", SESSION, Selection(project_ids=("project-one",))
        )
        await service.grants.saved.update_one({"id": connection_id}, {"$set": {"uncertain": True}})

        async def reconnect() -> str:
            begin = await service.begin(
                org,
                "actor_one",
                SESSION,
                Begin(
                    platform="vercel",
                    display_name="New name",
                    connection_id=connection_id,
                    expected_revision=connection.revision,
                ),
            )
            return parse_qs(urlsplit(begin["authorization_url"]).query)["state"][0]

        limited = True
        with pytest.raises(ResourceConflictError, match="Authorize every project"):
            await service.finish(org, "vercel", await reconnect(), "actor_one", SESSION, "code")
        assert (await service.inventory.get_connection(org, connection_id)) == connection
        limited = False
        assert (
            await service.finish(org, "vercel", await reconnect(), "actor_one", SESSION, "code")
            == connection_id
        )
        saved = await service.inventory.get_connection(org, connection_id)
        assert saved.revision == connection.revision + 2
        assert (
            saved.status is ConnectionStatus.READY
            and saved.allowed_resources == connection.allowed_resources
        )
        assert saved.display_name == "Vercel"
        assert len(await service.inventory.connections(org)) == 1
        row, grant = await service.grants.get(org, connection_id)
        assert row["active"] and not row.get("uncertain") and grant["access_token"] == TOKEN
        with pytest.raises(ResourceConflictError, match="Connection changed"):
            await reconnect()
        with pytest.raises(ResourceConflictError):
            await Lifecycle(database, service.grants).forget(org, connection_id)
        await service.inventory.replace_connection(
            saved.model_copy(
                update={
                    "archived_at": datetime.now(UTC),
                    "status": ConnectionStatus.DISABLED,
                    "revision": saved.revision + 1,
                }
            ),
            saved.revision,
        )
        await Lifecycle(database, service.grants).forget(org, connection_id)
        assert await service.grants.saved.find_one({"id": connection_id}) is None
