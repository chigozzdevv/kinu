from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from connectors.http import HttpProviderConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ControlPreferences,
    CreateRunCommand,
    ManagedCredential,
    RunStatus,
    Trigger,
)
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from executor.authorization import Authorization
from executor.vault import Vault
from executor.versions import Versions
from hosting.control import mapped_variables
from hosting.mapping import Mappings
from hosting.models import Begin, Import, Mapping, Selection
from hosting.proof import Proof
from hosting.service import Service
from pydantic import SecretStr
from rotation.hosting import Effects
from rotation.rotate import Rotation
from rotation.snapshot import loader
from testkit import make_http_provider_api
from tests.storage.hosting.rotation.fixture import MANAGEMENT, NEW, OLD, Platform

pytestmark = pytest.mark.anyio


async def setup(
    database: Database,
    identity: AuthenticatedIdentity,
    http: httpx.AsyncClient,
    platform_name: str = "render",
    initial_controls: tuple[ControlPreferences | None, ControlPreferences | None] = (None, None),
) -> tuple[Service, Versions, HttpProviderConnector, ManagedCredential]:
    accounts = AccountStore(database, lambda: datetime.now(UTC))
    org = (
        await AccountService(accounts, lambda: datetime.now(UTC)).create_organisation(
            identity, "Hosting rotation"
        )
    ).organisation.id
    vault = Vault(database, SecretStr("private-version-vault-test-material-at-least-32"))
    await vault.initialize()
    versions = Versions(vault)
    await versions.initialize()
    provider = HttpProviderConnector(Authorization(vault, versions), http)
    hosts = Service(database, vault, http, "http://localhost:8080")
    await hosts.grants.initialize()
    if platform_name == "vercel":
        await vault.configure(
            "vercel", {"slug": "kinu", "client_id": "app", "client_secret": "private"}
        )
        begin = await hosts.begin(
            org, identity.actor_id, "a" * 64, Begin(platform="vercel", display_name="Vercel")
        )
        state = parse_qs(urlsplit(begin["authorization_url"]).query)["state"][0]
        draft = {
            "connection_id": await hosts.finish(
                org, "vercel", state, identity.actor_id, "a" * 64, "code"
            )
        }
    else:
        draft = await hosts.begin(
            org,
            identity.actor_id,
            "a" * 64,
            Begin(
                platform="render", display_name="Render", token=SecretStr("host-management-fixture")
            ),
        )
    host = await hosts.save(
        org,
        draft["connection_id"],
        identity.actor_id,
        "a" * 64,
        Selection(project_ids=("project-one", "project-two")),
    )
    now = datetime.now(UTC)
    api = make_http_provider_api("https://issuer.example").model_copy(
        update={"supports_overlap": True}
    )
    api = api.model_copy(
        update={
            "list_credentials": api.list_credentials.model_copy(
                update={
                    "metadata_fields": {
                        "kind": "kind",
                        "scopes": "scopes",
                        "status": "status",
                        "disabled": "disabled",
                    }
                }
            )
        }
    )
    connection = Connection(
        id="provider_one",
        organisation_id=org,
        platform="issuer",
        display_name="Issuer",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="private://provider_one",
        capabilities=HttpProviderConnector.tools,
        allowed_resources=("issuer:credentials:*",),
        http=api,
        status=ConnectionStatus.READY,
        region="global",
        created_at=now,
        updated_at=now,
    )
    await vault.put(org, connection.id, {"token": MANAGEMENT})
    await InventoryStore(database).add_connection(connection)
    mappings = Mappings(hosts, versions, provider)
    credential = None
    for project, preferences in zip(("project-one", "project-two"), initial_controls, strict=True):
        variables = await hosts.variables(org, host.id, project)
        imported = await mappings.save(
            org,
            host.id,
            identity.actor_id,
            Import(
                project_id=project,
                initial_controls=preferences,
                mappings=(
                    Mapping(
                        variable_reference=variables[0].reference,
                        provider_connection_id=connection.id,
                        provider_id="old",
                        health_url=f"https://{project}.workload.example/health",
                    ),
                ),
            ),
        )
        credential = imported[0]
    assert credential is not None
    assert (
        len(await InventoryStore(database).credentials(org)) == 1
        and len(credential.consumer_ids) == 2
    )
    return hosts, versions, provider, credential


async def test_second_workload_keeps_the_first_credentials_rotation_rules(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    initial = ControlPreferences(
        automatic_triggers=frozenset({"drift"}),
        rotate_before_expiry_seconds=604800,
        maximum_observation_seconds=180,
        require_revoke_approval=True,
    )
    other = initial.model_copy(update={"require_revoke_approval": False})
    async with httpx.AsyncClient(transport=httpx.MockTransport(Platform().response)) as http:
        hosts, _, _, credential = await setup(
            database, identity, http, initial_controls=(initial, other)
        )
        version = await hosts.inventory.get_control_version(
            credential.organisation_id, credential.id, credential.control_version
        )
        assert Mappings.preferences(version) == initial


@pytest.mark.parametrize(
    "failure", [None, "proof", "unknown", "pending", "pending-unknown", "ready-not-live"]
)
async def test_rotation_updates_all_selected_consumers_or_keeps_old_key_safe(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    platform = Platform(failure)
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_host",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="event_host",
                        actor_id=identity.actor_id,
                        reason="Rotate selected hosting variables",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run

        async def sleep(seconds: float) -> None:
            assert seconds == 20

        monkeypatch.setattr("rotation.hosting.sleep", sleep)

        async def polling(seconds: float) -> None:
            assert seconds == 2

        monkeypatch.setattr("hosting.rollout.sleep", polling)
        result = await Rotation(
            database, Effects(hosts, versions, provider, Proof(http)), loader(database), "global"
        ).execute(org, run.id, "worker_one")
        expected = (
            RunStatus.CLEANUP
            if failure in {"unknown", "pending", "pending-unknown", "ready-not-live"}
            else RunStatus.FAILED
            if failure
            else RunStatus.COMPLETED
        )
        assert result.status is expected, result.model_dump_json()
        if failure is None:
            assert platform.revoked == ["old"]
            assert set(platform.loaded.values()) == {NEW}
            saved = await InventoryStore(database).get_credential(org, credential.id)
            bindings = await InventoryStore(database).bindings(org)
            assert len(bindings) == 2 and all(
                item.current_generation_id == saved.active_generation_id for item in bindings
            )
        elif failure == "proof":
            assert platform.keys["old"]["active"] and not platform.keys["new"]["active"]
            assert set(platform.loaded.values()) == {OLD}
        else:
            assert platform.keys["old"]["active"] and platform.keys["new"]["active"]
            assert not platform.revoked
        history = (await HistoryStore(database).get(org, run.id)).model_dump_json()
        assert all(secret not in history for secret in (OLD, NEW, MANAGEMENT))
        private = await versions.collection.find({}).to_list()
        assert all(secret not in str(private) for secret in (OLD, NEW, MANAGEMENT))


async def test_mapping_is_idempotent_and_failed_group_rolls_back_inventory(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    from core.errors import ResourceConflictError

    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        host = next(
            item for item in await hosts.inventory.connections(org) if item.platform == "render"
        )
        selected = (await hosts.variables(org, host.id, "project-one"))[0]
        registered = await mapped_variables(hosts, org, host.id)
        record = next(item for item in registered if item["reference"] == selected.reference)
        assert record["project"] == {"id": "project-one", "name": "project-one"}
        assert record["variable"]["name"] == selected.name
        assert record["variable"]["environment_name"] is None
        assert OLD not in str(registered)
        mappings = Mappings(hosts, versions, provider)
        before = (await hosts.inventory.get_credential(org, credential.id)).revision
        saved = await mappings.save(
            org,
            host.id,
            identity.actor_id,
            Import(
                project_id="project-one",
                mappings=(
                    Mapping(
                        variable_reference=selected.reference,
                        provider_connection_id="provider_one",
                        provider_id="old",
                        health_url="https://project-one.workload.example/health",
                    ),
                ),
            ),
        )
        assert saved[0].revision == before and len(await hosts.inventory.bindings(org)) == 2
        with pytest.raises(ResourceConflictError):
            await mappings.save(
                org,
                host.id,
                identity.actor_id,
                Import(
                    project_id="project-one",
                    mappings=(
                        Mapping(
                            variable_reference=selected.reference,
                            provider_connection_id="provider_one",
                            provider_id="old",
                            health_url="https://changed.workload.example/health",
                        ),
                    ),
                ),
            )
        assert (await hosts.inventory.get_credential(org, credential.id)).revision == before
        with pytest.raises(ResourceConflictError):
            await versions.access_for(Mappings.store("org_other"), credential.secret_reference)


async def test_missing_or_replayed_workload_proof_stops_before_creating_a_key(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    from contracts import ConsumerService
    from core.storage.records import Records

    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        async with Records(database).write(org) as rows:
            service = await rows.get(ConsumerService, org, credential.consumer_ids[0])
            await rows.replace(
                service.model_copy(update={"endpoint": None, "revision": service.revision + 1}),
                service.revision,
            )
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_missing",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="event_missing",
                        actor_id=identity.actor_id,
                        reason="Probe gate",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run
        result = await Rotation(
            database, Effects(hosts, versions, provider, Proof(http)), loader(database), "global"
        ).execute(org, run.id, "worker_one")
        assert result.status is RunStatus.FAILED and "new" not in platform.keys


async def test_archived_credentials_can_be_imported_again_without_reusing_old_secret_records(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    from core.inventory import InventoryService

    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        await InventoryService(hosts.inventory).archive_credential(
            org, credential.id, credential.revision, cascade=True
        )
        assert (
            await database.collection("host_bindings").count_documents({"organisation_id": org})
            == 0
        )
        host = next(
            item for item in await hosts.inventory.connections(org) if item.platform == "render"
        )
        variable = (await hosts.variables(org, host.id, "project-one"))[0]
        saved = await Mappings(hosts, versions, provider).save(
            org,
            host.id,
            identity.actor_id,
            Import(
                project_id="project-one",
                mappings=(
                    Mapping(
                        variable_reference=variable.reference,
                        provider_connection_id="provider_one",
                        provider_id="old",
                        health_url="https://project-one.workload.example/health",
                    ),
                ),
            ),
        )
        assert (
            saved[0].id != credential.id
            and saved[0].secret_reference != credential.secret_reference
        )
