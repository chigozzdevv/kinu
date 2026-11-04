from datetime import UTC, datetime

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from core.account import AccountService
from core.account.installation import Installation
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity
from core.private import PrivateClient
from core.storage.database import Database
from executor.app import app as executor
from executor.config import ExecutorSettings
from executor.runtime import Runtime
from executor.vault import Vault
from hosting.models import Platform
from jobs.queue import Jobs
from pydantic import SecretStr
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio


async def test_integration_settings_require_installation_owner_and_keep_secrets_private(
    database: Database,
    identity: AuthenticatedIdentity,
) -> None:
    accounts = AccountStore(database, lambda: datetime.now(UTC))
    membership = await AccountService(accounts, lambda: datetime.now(UTC)).create_organisation(
        identity, "Integrations"
    )
    org = membership.organisation.id
    installation = Installation(database)
    await installation.claim(identity, SecretStr("setup-token"), SecretStr("setup-token"))
    key = SecretStr("vault-test-encryption-material-at-least-32")
    vault = Vault(database, key)
    await vault.initialize()
    config = ExecutorSettings(
        vault_key=key,
        executor_token=SecretStr("executor-private"),
        native_token=SecretStr("native-private"),
    )
    queue = Jobs("redis://localhost:6379")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=executor), base_url="http://executor"
    ) as private_http:
        executor.state.service = Runtime(config, database, vault, private_http, queue)
        private = PrivateClient(private_http, "http://executor", SecretStr("executor-private"))
        app = create_app(
            ApiServices(access=AccessControl(accounts), private=private, installation=installation),
            browser=make_browser(identity),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            root = f"/v1/organisations/{org}/integrations"
            response = await http.get(root, headers=HEADERS)
            assert response.status_code == 200 and response.json()["editable"]
            body = {
                "values": {
                    "client_id": "test.apps.googleusercontent.com",
                    "client_secret": "secret-fixture-never-public",
                }
            }
            response = await http.put(root + "/gmail", headers=HEADERS, json=body)
            assert response.status_code == 200, response.text
            status = await http.get(root, headers=HEADERS)
            assert next(item for item in status.json()["integrations"] if item["id"] == "gmail")[
                "configured"
            ]
            assert "secret-fixture" not in status.text
            stored = await database.collection("integrations").find_one({"id": "gmail"})
            assert stored and "secret-fixture" not in str(stored)
            await database.collection("installation").update_one(
                {"_id": "main"}, {"$set": {"administrators": []}}
            )
            assert (await http.put(root + "/gmail", headers=HEADERS, json=body)).status_code == 403
            assert (
                await http.get("/v1/organisations/org_other/integrations", headers=HEADERS)
            ).status_code == 403
        assert (
            await private_http.post("/integrations", json={"operation": "list"})
        ).status_code == 401
    await queue.close()


async def test_provider_management_tokens_are_private_and_failed_setup_leaves_no_grant(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    from connectors.base.errors import ConnectorError
    from connectors.http import HttpProviderConnector
    from contracts import Connection
    from core.errors import ResourceNotFoundError
    from core.inventory.store import InventoryStore
    from testkit import make_http_provider_api

    accounts = AccountStore(database, lambda: datetime.now(UTC))
    membership = await AccountService(accounts, lambda: datetime.now(UTC)).create_organisation(
        identity, "Providers"
    )
    org = membership.organisation.id
    vault = Vault(database, SecretStr("vault-test-encryption-material-at-least-32"))
    await vault.initialize()
    queue = Jobs("redis://localhost:6379")
    token = "management-fixture-never-returned"
    received = []

    async def metadata(connector: HttpProviderConnector, connection: Connection) -> tuple[()]:
        secret = await connector._secrets.access_for(
            connection, connection.authorization_reference or ""
        )
        assert secret.bytes().decode() == token
        secret.clear()
        received.append(connection.id)
        if connection.platform == "unavailable":
            raise ConnectorError("unavailable", "Provider unavailable")
        return ()

    monkeypatch.setattr(HttpProviderConnector, "metadata", metadata)
    config = ExecutorSettings(
        vault_key=SecretStr("vault-test-encryption-material-at-least-32"),
        executor_token=SecretStr("executor-private"),
        native_token=SecretStr("native-private"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=executor), base_url="http://executor"
    ) as private_http:
        executor.state.service = Runtime(config, database, vault, private_http, queue)
        private = PrivateClient(private_http, "http://executor", SecretStr("executor-private"))
        app = create_app(
            ApiServices(access=AccessControl(accounts), private=private),
            browser=make_browser(identity),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            path = f"/v1/organisations/{org}/connections/provider"
            body = {
                "display_name": "Example",
                "platform": "example",
                "token": token,
                "http": make_http_provider_api().model_dump(mode="json"),
            }
            saved = await http.post(path, headers=HEADERS, json=body)
            assert saved.status_code == 201, saved.text
            connection = Connection.model_validate(saved.json())
            assert token not in saved.text
            assert token not in str(
                await database.collection("secrets").find_one({"id": connection.id})
            )
            assert (
                await InventoryStore(database).get_connection(org, connection.id)
            ).id == connection.id
            with pytest.raises(ResourceNotFoundError):
                await executor.state.service.authorizations.access_for(
                    connection.model_copy(update={"organisation_id": "org_other"}),
                    connection.authorization_reference,
                )
            denied = await http.post(path.replace(org, "org_other"), headers=HEADERS, json=body)
            assert denied.status_code == 403 and len(received) == 1
            failed = await http.post(path, headers=HEADERS, json=body | {"platform": "unavailable"})
            assert failed.status_code == 409
            assert await database.collection("secrets").find_one({"id": received[-1]}) is None
            invalid = await http.post(path, headers=HEADERS, json=body | {"token": token * 1000})
            assert invalid.status_code == 422 and token not in invalid.text
        invalid = await private_http.post(
            "/providers",
            headers={"authorization": "Bearer executor-private"},
            json={"token": token},
        )
        assert invalid.status_code == 422 and token not in invalid.text
    await queue.close()


@pytest.mark.parametrize("platform", ["vercel", "railway"])
async def test_host_configuration_drives_workspace_connect_without_exposing_app_credentials(
    database: Database, identity: AuthenticatedIdentity, platform: Platform
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from core.errors import ResourceConflictError
    from hosting.models import Begin

    accounts = AccountStore(database, lambda: datetime.now(UTC))
    org = (
        await AccountService(accounts, lambda: datetime.now(UTC)).create_organisation(
            identity, "Hosting"
        )
    ).organisation.id
    installation = Installation(database)
    key = SecretStr("vault-test-encryption-material-at-least-32")
    legacy = Vault(database, key)
    await legacy.initialize()
    await legacy.configure(
        platform, {"client_id": "old-app", "client_secret": "old-private", "slug": "old-slug"}
    )
    config = ExecutorSettings(
        public_url="https://kinu.example.com",
        environment="production",
        vault_key=key,
        executor_token=SecretStr("executor-private"),
        native_token=SecretStr("native-private"),
        vercel_slug="kinu",
        vercel_client_id=SecretStr("hosted-vercel-app"),
        vercel_client_secret=SecretStr("private-hosted-vercel"),
        railway_client_id=SecretStr("hosted-railway-app"),
        railway_client_secret=SecretStr("private-hosted-railway"),
    )
    vault = Vault(database, key, config.oauth_apps)
    # A runtime read cannot mutate the authoritative host configuration.
    copied = await vault.integration(platform)
    copied["client_id"] = "modified"
    assert (await vault.integration(platform))["client_id"] == f"hosted-{platform}-app"
    with pytest.raises(ResourceConflictError):
        await vault.configure(platform, {"client_id": "dashboard-override"})
    assert (await legacy.integration(platform))["client_id"] == "old-app"
    queue = Jobs("redis://localhost:6379")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=executor), base_url="http://executor"
    ) as private_http:
        service = Runtime(config, database, vault, private_http, queue)
        executor.state.service = service
        private = PrivateClient(private_http, "http://executor", SecretStr("executor-private"))
        app = create_app(
            ApiServices(access=AccessControl(accounts), private=private, installation=installation),
            browser=make_browser(identity),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            status = await http.get(f"/v1/organisations/{org}/integrations", headers=HEADERS)
            assert status.status_code == 200, status.text
            item = next(item for item in status.json()["integrations"] if item["id"] == platform)
            assert item["configured"]
            assert (
                item["callback_url"] == f"https://kinu.example.com/connections/{platform}/callback"
            )
            assert (
                "private-hosted" not in status.text and f"hosted-{platform}-app" not in status.text
            )
            assert not status.json()["editable"]
        result = await service.hosts.begin(
            org,
            identity.actor_id,
            "browser-session",
            Begin(platform=platform, display_name="Hosting"),
        )
        url = urlsplit(result["authorization_url"])
        assert "private-hosted" not in url.geturl()
        if platform == "vercel":
            assert url.hostname == "vercel.com" and url.path == "/integrations/kinu/new"
        else:
            query = parse_qs(url.query)
            assert query["client_id"] == ["hosted-railway-app"]
            assert query["redirect_uri"] == [item["callback_url"]]
            assert query["scope"] == ["openid offline_access project:member"]
    await queue.close()
