import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from contracts import (
    Application,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    CredentialGeneration,
    Environment,
    GenerationState,
    ManagedCredential,
)
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity
from core.errors import ResourceConflictError, ResourceNotFoundError, RevisionConflictError
from core.inventory import InventoryService
from core.inventory.store import InventoryStore
from core.storage.database import Database
from psycopg.types.json import Jsonb
from testkit import make_control_version, make_http_provider_api
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 27, tzinfo=UTC)


@dataclass
class Inventory:
    org: str
    store: InventoryStore
    service: InventoryService
    credential: ManagedCredential
    generation: CredentialGeneration
    binding: ConsumerBinding
    controls: ControlVersion


@pytest.fixture
async def inventory(database: Database, identity: AuthenticatedIdentity) -> Inventory:
    accounts = AccountStore(database, lambda: NOW)
    workspace = await AccountService(accounts, lambda: NOW).create_organisation(
        identity, "Inventory"
    )
    org = workspace.organisation.id
    store = InventoryStore(database)
    for role in (ConnectionRole.PROVIDER, ConnectionRole.SECRET_STORE, ConnectionRole.RUNTIME):
        await store.add_connection(
            Connection(
                id=f"connection_{role.value}",
                organisation_id=org,
                platform="example",
                display_name=role.value,
                roles=frozenset({role}),
                interface=ConnectionInterface.API,
                authorization=ConnectionAuthorization.API_KEY,
                authorization_reference="secret://account/access/versions/one",
                capabilities=frozenset({"provider.testCredential"}),
                allowed_resources=("secret://account",)
                if role is ConnectionRole.SECRET_STORE
                else ("service",),
                http=make_http_provider_api() if role is ConnectionRole.PROVIDER else None,
                status=ConnectionStatus.READY,
                region="eu-west-2",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    await store.add_application_setup(
        Application(
            id="app_one", organisation_id=org, display_name="App", created_at=NOW, updated_at=NOW
        ),
        Environment(
            id="env_one",
            organisation_id=org,
            application_id="app_one",
            display_name="Staging",
            production=False,
            region="eu-west-2",
            created_at=NOW,
            updated_at=NOW,
        ),
        ConsumerService(
            id="service_one",
            organisation_id=org,
            application_id="app_one",
            environment_id="env_one",
            runtime_connection_id="connection_runtime",
            runtime_resource="service/one",
            display_name="API",
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    credential = ManagedCredential(
        id="credential_one",
        organisation_id=org,
        connection_id="connection_provider",
        secret_store_connection_id="connection_secret-store",
        secret_resource="secret://account/key",
        secret_reference="secret://account/key/versions/one",
        provider="example",
        kind="api-key",
        display_name="Provider access",
        provider_id="key_one",
        consumer_ids=("service_one",),
        active_generation_id="generation_one",
        control_version="controls_one",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_one",
        organisation_id=org,
        credential_id=credential.id,
        state=GenerationState.ACTIVE,
        attempt_id="attempt_import",
        provider_id="key_one",
        secret_reference=credential.secret_reference,
        created_at=NOW,
    )
    binding = ConsumerBinding(
        id="binding_one",
        organisation_id=org,
        credential_id=credential.id,
        service_id="service_one",
        environment_id="env_one",
        runtime_connection_id="connection_runtime",
        runtime_resource="service/one",
        runtime_secret_name="PROVIDER_KEY",
        secret_reference=credential.secret_reference,
        current_generation_id=generation.id,
    )
    controls = make_control_version(org, "controls_one", credential.id, NOW)
    return Inventory(
        org, store, InventoryService(store, lambda: NOW), credential, generation, binding, controls
    )


async def imported(inventory: Inventory) -> ManagedCredential:
    return await inventory.store.import_credential(
        inventory.credential, inventory.generation, (inventory.binding,), inventory.controls, ()
    )


async def test_inventory_survives_recomposition_and_is_workspace_scoped(
    database: Database, inventory: Inventory
) -> None:
    await imported(inventory)
    second = InventoryService(InventoryStore(database), lambda: NOW)
    credentials, services, bindings = await second.graph(inventory.org)
    assert credentials == (inventory.credential,)
    assert services[0].id == "service_one"
    assert bindings == (inventory.binding,)
    assert not (await second.graph("org_other"))[0]
    with pytest.raises(ResourceNotFoundError):
        await second.get_connection("org_other", "connection_provider")
    with pytest.raises(ResourceConflictError):
        await inventory.store.add_connection(
            await inventory.store.get_connection(inventory.org, "connection_provider")
        )


async def test_competing_inventory_updates_cannot_overwrite_each_other(
    inventory: Inventory,
) -> None:
    current = await inventory.store.get_application(inventory.org, "app_one")
    updates = [
        current.model_copy(update={"display_name": name, "revision": 1}) for name in ("One", "Two")
    ]
    results = await asyncio.gather(
        *(inventory.store.replace_application(update, 0) for update in updates),
        return_exceptions=True,
    )
    assert sum(isinstance(value, Application) for value in results) == 1
    assert sum(isinstance(value, RevisionConflictError) for value in results) == 1
    assert (await inventory.store.get_application(inventory.org, "app_one")).revision == 1


async def test_failed_credential_import_rolls_back_every_record(inventory: Inventory) -> None:
    invalid = inventory.binding.model_copy(update={"environment_id": "env_missing"})
    with pytest.raises(ResourceConflictError, match="lineage"):
        await inventory.store.import_credential(
            inventory.credential, inventory.generation, (invalid,), inventory.controls, ()
        )
    assert not await inventory.store.credentials(inventory.org)
    assert not await inventory.store.bindings(inventory.org)
    await imported(inventory)


async def test_application_setup_cannot_cross_workspace_boundaries(inventory: Inventory) -> None:
    app = Application(
        id="app_bad",
        organisation_id=inventory.org,
        display_name="Bad",
        created_at=NOW,
        updated_at=NOW,
    )
    env = Environment(
        id="env_bad",
        organisation_id="org_other",
        application_id=app.id,
        display_name="Bad",
        production=False,
        region="eu-west-2",
        created_at=NOW,
        updated_at=NOW,
    )
    service = (await inventory.store.services(inventory.org))[0]
    with pytest.raises(ResourceConflictError):
        await inventory.store.add_application_setup(app, env, service)
    with pytest.raises(ResourceNotFoundError):
        await inventory.store.get_application(inventory.org, app.id)


async def test_control_versions_are_immutable_and_revision_bound(inventory: Inventory) -> None:
    await imported(inventory)
    controls = inventory.controls.model_copy(update={"id": "controls_two", "number": 2})
    changed = inventory.credential.model_copy(
        update={"control_version": controls.id, "revision": 1}
    )
    await inventory.store.replace_controls(changed, 0, controls)
    assert (
        await inventory.store.get_control_version(inventory.org, changed.id, "controls_one")
        == inventory.controls
    )
    next_version = controls.model_copy(update={"id": "controls_three", "number": 3})
    with pytest.raises(RevisionConflictError):
        await inventory.store.replace_controls(
            changed.model_copy(update={"control_version": next_version.id}), 0, next_version
        )
    with pytest.raises(ResourceNotFoundError):
        await inventory.store.get_control_version(inventory.org, changed.id, next_version.id)
    with pytest.raises(ResourceConflictError):
        await inventory.store.get_control_version(inventory.org, "credential_other", controls.id)


async def test_disconnect_archives_dependents_and_removes_bindings(inventory: Inventory) -> None:
    await imported(inventory)
    with pytest.raises(ResourceConflictError, match="active inventory"):
        await inventory.service.archive_connection(inventory.org, "connection_provider", 0)
    await inventory.service.archive_connection(inventory.org, "connection_provider", 0, True)
    assert not await inventory.service.list_credentials(inventory.org)
    assert not await inventory.store.bindings(inventory.org)
    assert (await inventory.store.get_connection(inventory.org, "connection_provider")).archived_at
    assert len(await inventory.service.list_connections(inventory.org)) == 2


async def test_incomplete_archive_is_atomic(inventory: Inventory) -> None:
    await imported(inventory)
    connection = await inventory.store.get_connection(inventory.org, "connection_provider")
    archived = connection.model_copy(update={"archived_at": NOW, "revision": 1})
    with pytest.raises(ResourceConflictError, match="archived"):
        await inventory.store.archive_inventory((archived,), ())
    assert (await inventory.store.get_connection(inventory.org, connection.id)).archived_at is None


@pytest.mark.parametrize("connection_id", ["connection_runtime", "connection_secret-store"])
async def test_connection_edits_preserve_existing_scope(
    inventory: Inventory, connection_id: str
) -> None:
    await imported(inventory)
    connection = await inventory.store.get_connection(inventory.org, connection_id)
    changed = connection.model_copy(update={"allowed_resources": ("unrelated",), "revision": 1})
    with pytest.raises(ResourceConflictError, match="boundary"):
        await inventory.store.replace_connection(changed, 0)
    assert await inventory.store.get_connection(inventory.org, connection_id) == connection


async def test_service_edit_cannot_invalidate_existing_binding(inventory: Inventory) -> None:
    await imported(inventory)
    service = await inventory.store.get_service(inventory.org, "service_one")
    changed = service.model_copy(update={"runtime_resource": "service/two", "revision": 1})
    with pytest.raises(ResourceConflictError, match="lineage"):
        await inventory.store.replace_service(changed, 0)
    assert await inventory.store.get_service(inventory.org, service.id) == service


async def test_duplicate_consumer_binding_rolls_back_import(inventory: Inventory) -> None:
    duplicate = inventory.binding.model_copy(update={"id": "binding_duplicate"})
    with pytest.raises(ResourceConflictError, match="lineage"):
        await inventory.store.import_credential(
            inventory.credential,
            inventory.generation,
            (inventory.binding, duplicate),
            inventory.controls,
            (),
        )
    assert not await inventory.store.credentials(inventory.org)
    assert not await inventory.store.bindings(inventory.org)


async def test_inventory_api_enforces_workspace_roles_and_csrf(
    database: Database, identity: AuthenticatedIdentity, inventory: Inventory
) -> None:
    await imported(inventory)
    accounts = AccountStore(database, lambda: NOW)
    app = create_app(
        ApiServices(access=AccessControl(accounts), inventory=inventory.service),
        browser=make_browser(identity),
    )
    path = f"/v1/organisations/{inventory.org}/inventory"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://kinu.test"
    ) as http:
        graph = await http.get(f"{path}/graph", headers=HEADERS)
        assert graph.status_code == 200
        assert graph.json()["credentials"][0]["id"] == inventory.credential.id
        changed = await http.patch(
            f"{path}/connections/connection_provider",
            headers=HEADERS,
            json={"display_name": "Updated", "expected_revision": 0},
        )
        assert changed.status_code == 200
        rejected = await http.patch(
            f"{path}/connections/connection_provider",
            headers={"Cookie": HEADERS["Cookie"], "Origin": HEADERS["Origin"]},
            json={"display_name": "Forged", "expected_revision": 1},
        )
        assert rejected.status_code == 403
        assert (
            await http.patch(
                f"{path}/connections/connection_provider",
                headers=HEADERS,
                json={"display_name": "Stale", "expected_revision": 0},
            )
        ).status_code == 409
        assert (
            await http.get("/v1/organisations/org_other/inventory/graph", headers=HEADERS)
        ).status_code == 403
        async with database.connection() as connection:
            await connection.execute(
                "UPDATE kinu.members SET data = jsonb_set(data, '{roles}', %s) "
                "WHERE organisation_id = %s AND subject = %s",
                (Jsonb(["viewer"]), inventory.org, identity.subject),
            )
        assert (await http.get(f"{path}/graph", headers=HEADERS)).status_code == 200
        denied = await http.patch(
            f"{path}/connections/connection_provider",
            headers=HEADERS,
            json={"display_name": "Denied", "expected_revision": 1},
        )
        assert denied.status_code == 403
