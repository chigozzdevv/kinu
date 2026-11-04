import json
from datetime import UTC, datetime

import httpx
import pytest
from agents.playbooks import PlaybookBuilder
from api.app import create_app
from api.deps import ApiServices
from connectors.http import HttpProviderConnector
from contracts import Connection
from contracts.api_playbook import ApiPlaybookDraft
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity
from core.errors import PlaybookError, ResourceConflictError, ResourceNotFoundError
from core.inventory.store import InventoryStore
from core.playbook import WalkthroughService
from core.playbook.api_store import ApiPlaybookStore
from core.playbook.sources import Sources
from core.private import PrivateClient
from core.storage.database import Database
from executor.app import app as executor
from executor.config import ExecutorSettings
from executor.runtime import Runtime
from executor.vault import Vault
from jobs.queue import Jobs
from policy import digest
from pydantic import SecretStr
from testkit import make_http_provider_api
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio


async def test_api_playbook_generation_publish_and_private_connection_pinning(
    database: Database, identity: AuthenticatedIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    accounts = AccountStore(database, lambda: now)
    org = (
        await AccountService(accounts, lambda: now).create_organisation(identity, "API books")
    ).organisation.id
    definition = ApiPlaybookDraft(
        name="Example key rotation", platform="example", http=make_http_provider_api()
    )
    token = "management-fixture-private"
    metadata_calls: list[Connection] = []

    def native(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/_internal/playbooks"
        assert body["input"]["execution"] == "api"
        assert "http" in body["input"]["schema"]["properties"]
        assert "steps" not in body["input"]["schema"]["properties"]
        assert "fixture-password" not in json.dumps(body)
        return httpx.Response(
            200,
            json={
                "succeeded": True,
                "output": {"playbook_draft": definition.model_dump(mode="json")},
                "completed_at": now.isoformat(),
            },
        )

    async def metadata(connector: HttpProviderConnector, connection: Connection) -> tuple[()]:
        with await connector._secrets.access_for(
            connection, connection.authorization_reference or ""
        ) as secret:
            assert secret.bytes().decode() == token
        assert connection.http == definition.http
        metadata_calls.append(connection)
        return ()

    monkeypatch.setattr(HttpProviderConnector, "metadata", metadata)
    config = ExecutorSettings(
        vault_key=SecretStr("vault-api-playbooks-test-material-at-least-32"),
        executor_token=SecretStr("executor-private"),
        native_token=SecretStr("native-private"),
    )
    assert config.vault_key and config.executor_token
    vault = Vault(database, config.vault_key)
    await vault.initialize()
    queue = Jobs("redis://localhost:6379")
    store = ApiPlaybookStore(database)
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(native)) as native_http,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=executor), base_url="http://executor"
        ) as private_http,
    ):
        executor.state.service = Runtime(config, database, vault, native_http, queue)
        private = PrivateClient(private_http, "http://executor", config.executor_token)
        app = create_app(
            ApiServices(
                access=AccessControl(accounts),
                api_playbooks=store,
                walkthroughs=WalkthroughService(Sources(database), lambda: now),
                playbook_builder=PlaybookBuilder(private),
                private=private,
            ),
            browser=make_browser(identity),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            root = f"/v1/organisations/{org}"
            source = await http.post(
                root + "/playbooks/playbook_api/walkthroughs/references",
                headers=HEADERS,
                json={
                    "source_id": "source_api",
                    "kind": "text",
                    "content": 'Create and revoke keys. password="fixture-password".',
                },
            )
            assert source.status_code == 201, source.text
            draft = await http.post(
                root + "/api-playbooks/playbook_api/draft",
                headers=HEADERS | {"Idempotency-Key": "generate-api-one"},
                json={"objective": "Build an API rotation playbook", "source_ids": ["source_api"]},
            )
            assert draft.status_code == 200, draft.text
            created = await http.post(
                root + "/api-playbooks/playbook_api/versions",
                headers=HEADERS,
                json={
                    "version_id": "api_version_one",
                    "definition": draft.json()["definition"],
                    "source_ids": ["source_api"],
                },
            )
            assert created.status_code == 201, created.text
            body = {
                "display_name": "Example",
                "platform": "example",
                "api_playbook_id": "playbook_api",
                "api_playbook_version_id": "api_version_one",
                "token": token,
            }
            path = root + "/connections/provider"
            unpublished = await http.post(path, headers=HEADERS, json=body)
            assert unpublished.status_code == 409 and not metadata_calls
            published = await http.post(
                root + "/api-playbooks/playbook_api/versions/api_version_one/publish",
                headers=HEADERS,
                json={},
            )
            assert published.status_code == 200, published.text
            saved = await http.post(path, headers=HEADERS, json=body)
            assert saved.status_code == 201, saved.text
            connection = Connection.model_validate(saved.json())
            assert connection.api_playbook_version_id == "api_version_one"
            assert token not in saved.text
            assert token not in str(
                await database.collection("secrets").find_one({"id": connection.id})
            )
            mismatch = await http.post(path, headers=HEADERS, json=body | {"platform": "another"})
            assert mismatch.status_code == 409 and len(metadata_calls) == 1
            override = await http.post(
                path, headers=HEADERS, json=body | {"http": definition.http.model_dump(mode="json")}
            )
            assert override.status_code == 422
            denied = await http.get(
                root.replace(org, "org_other") + "/api-playbooks/playbook_api", headers=HEADERS
            )
            assert denied.status_code == 403
            changed = definition.model_copy(
                update={"http": make_http_provider_api("https://new-provider.example")}
            )
            await store.add_version(
                "playbook_api",
                "api_version_two",
                org,
                changed,
                digest(changed),
                identity.actor_id,
                now,
                (),
            )
            await store.publish(org, "playbook_api", "api_version_two", identity.actor_id, now)
            stale = await http.post(path, headers=HEADERS, json=body)
            assert stale.status_code == 409
            persisted = await InventoryStore(database).get_connection(org, connection.id)
            assert (
                persisted.http == definition.http
                and persisted.api_playbook_version_id == "api_version_one"
            )
    await queue.close()


async def test_api_playbook_versions_are_immutable_and_scoped(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    now = datetime.now(UTC)
    membership = await AccountService(
        AccountStore(database, lambda: now), lambda: now
    ).create_organisation(identity, "API versions")
    org = membership.organisation.id
    store = ApiPlaybookStore(database)
    definition = ApiPlaybookDraft(name="Example", platform="example", http=make_http_provider_api())
    args = (
        "playbook_api",
        "api_version_one",
        org,
        definition,
        digest(definition),
        "actor_one",
        now,
        (),
    )
    book, version = await store.add_version(*args)
    assert (await store.add_version(*args)) == (book, version)
    with pytest.raises(ResourceConflictError):
        await store.add_version(
            "playbook_api",
            version.id,
            org,
            definition.model_copy(
                update={"http": make_http_provider_api("https://changed.example")}
            ),
            digest(
                definition.model_copy(
                    update={"http": make_http_provider_api("https://changed.example")}
                )
            ),
            "actor_one",
            now,
            (),
        )
    with pytest.raises(ResourceNotFoundError):
        await store.get_version("org_other", book.id, version.id)
    with pytest.raises(PlaybookError):
        await store.published(org, book.id, version.id)
    await store.publish(org, book.id, version.id, "actor_one", now)
    assert (await store.published(org, book.id, version.id)).id == version.id
    refreshed = await store.get(org, book.id)
    await store.replace(
        refreshed.model_copy(update={"archived_at": now, "revision": refreshed.revision + 1}),
        refreshed.revision,
    )
    with pytest.raises(PlaybookError):
        await store.published(org, book.id, version.id)
