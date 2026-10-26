from datetime import UTC, datetime

import pytest
from connectors.base.errors import ConnectorAuthenticationError
from contracts import (
    Application,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlPreferences,
    ControlVersion,
    CredentialGeneration,
    Environment,
    GenerationState,
    ManagedCredential,
    PageCheckpoint,
    Playbook,
    PlaybookDraft,
    PlaybookEffect,
    PlaybookState,
    PlaybookStep,
    PlaybookVersion,
    ProbeKind,
    ProbeVersion,
    RuntimeConsumerSetup,
    SecureField,
    Selector,
    SelectorKind,
    Stage,
)
from core.audit import GENESIS, event_hash
from core.errors import PlaybookError, ResourceConflictError
from core.inventory import InventoryService
from core.inventory.controls import update_controls
from core.playbook import PlaybookService
from policy import digest
from testkit import make_http_provider_api

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class ImportMetadata:
    async def metadata(self, connection: Connection) -> tuple[dict[str, object], ...]:
        return (
            {
                "provider_id": "provider_key_one",
                "kind": "api-key",
                "scopes": ["messages.write"],
                "disabled": False,
            },
        )


class ImportVerifier:
    def __init__(self, identity: str = "provider_key_one") -> None:
        self.identity = identity

    async def credential_identity(
        self, connection: Connection, secret_connection: Connection, secret_reference: str
    ) -> str:
        assert connection.id == "provider_one"
        assert secret_connection.id == "secret_one"
        assert secret_reference == "secret://project-one/key/versions/1"
        return self.identity


class BrowserSecretMetadata:
    async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
        return ()

    async def versions_for(
        self, connection: Connection, secret: str
    ) -> tuple[dict[str, object], ...]:
        return ({"name": f"{secret}/versions/1", "state": "ENABLED"},)


class ImportRuntimeMetadata:
    async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
        return (
            {
                "reference": "runtime://project-one/us-east1/service-one",
                "display_name": "service-one",
                "secret_bindings": (
                    {
                        "name": "PROVIDER_KEY",
                        "secret": "secret://project-one/key",
                        "version": "1",
                        "container": None,
                    },
                ),
                "region": "us-east1",
            },
        )


class Catalog:
    def __init__(self) -> None:
        self.version: PlaybookVersion | None = None
        self.playbook: Playbook | None = None
        self.credential_values: tuple[ManagedCredential, ...] = ()
        self.binding_values: tuple[ConsumerBinding, ...] = ()
        self.imported_controls: ControlVersion | None = None
        self.imported_probes: tuple[ProbeVersion, ...] = ()
        self.imported_service_setup: tuple[Application, Environment, ConsumerService] | None = None
        self.archived_resources: tuple[
            Connection | Application | Environment | ConsumerService | ManagedCredential, ...
        ] = ()
        self.archived_bindings: tuple[ConsumerBinding, ...] = ()
        self.connection_values: dict[str, Connection] = {
            "provider_one": _api_connection("provider_one", ConnectionRole.PROVIDER),
            "secret_one": _api_connection("secret_one", ConnectionRole.SECRET_STORE),
            "runtime_one": _api_connection("runtime_one", ConnectionRole.RUNTIME),
        }

    async def add_version(
        self,
        playbook_id: str,
        version_id: str,
        organisation_id: str,
        definition: PlaybookDraft,
        definition_digest: str,
        actor_id: str,
        created_at: datetime,
        source_ids: tuple[str, ...],
    ) -> tuple[Playbook, PlaybookVersion]:
        root = Playbook(
            id=playbook_id,
            organisation_id=organisation_id,
            name=definition.name,
            platform=definition.platform,
            latest_version=1,
            latest_version_id=version_id,
            created_at=created_at,
            updated_at=created_at,
        )
        self.playbook = root
        self.version = PlaybookVersion(
            id=version_id,
            organisation_id=organisation_id,
            playbook_id=playbook_id,
            number=1,
            definition=definition,
            digest=definition_digest,
            state=PlaybookState.DRAFT,
            source_ids=source_ids,
            created_by=actor_id,
            created_at=created_at,
        )
        return (root, self.version)

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]:
        return ()

    async def get(self, organisation_id: str, playbook_id: str) -> Playbook:
        assert self.playbook is not None
        return self.playbook

    async def replace(self, value: Playbook, expected_revision: int) -> Playbook:
        self.playbook = value
        return value

    async def get_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        assert self.version is not None
        return self.version

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
        published_at: datetime,
    ) -> PlaybookVersion:
        assert self.version is not None
        self.version = self.version.model_copy(
            update={
                "state": PlaybookState.PUBLISHED,
                "published_by": actor_id,
                "published_at": published_at,
            }
        )
        return self.version

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return self.connection_values[resource_id]

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential:
        raise ResourceConflictError("credential not found")

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        raise ResourceConflictError("control version not found")

    async def get_playbook_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        assert self.version is not None
        return self.version

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection:
        self.connection_values[value.id] = value
        return value

    async def replace_application(self, value: Application, expected_revision: int) -> Application:
        return value

    async def replace_environment(self, value: Environment, expected_revision: int) -> Environment:
        return value

    async def replace_service(
        self, value: ConsumerService, expected_revision: int
    ) -> ConsumerService:
        return value

    async def replace_credential(
        self, value: ManagedCredential, expected_revision: int
    ) -> ManagedCredential:
        return value

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection:
        connection = self.connection_values[connection_id]
        assert connection.revision == expected_revision
        connection = connection.model_copy(
            update={
                "playbook_id": playbook_id,
                "playbook_version_id": version_id,
                "status": ConnectionStatus.READY,
                "updated_at": updated_at,
                "revision": expected_revision + 1,
            }
        )
        self.connection_values[connection_id] = connection
        return connection

    async def detach_playbook(
        self, organisation_id: str, connection_id: str, expected_revision: int, updated_at: datetime
    ) -> Connection:
        connection = self.connection_values[connection_id]
        assert connection.revision == expected_revision
        connection = connection.model_copy(
            update={
                "playbook_id": None,
                "playbook_version_id": None,
                "status": ConnectionStatus.SETUP_REQUIRED,
                "updated_at": updated_at,
                "revision": expected_revision + 1,
            }
        )
        self.connection_values[connection_id] = connection
        return connection

    async def add_connection(self, value: Connection) -> Connection:
        self.connection_values[value.id] = value
        return value

    async def add_application(self, value: Application) -> Application:
        return value

    async def add_environment(self, value: Environment) -> Environment:
        return value

    async def add_service(self, value: ConsumerService) -> ConsumerService:
        return value

    async def add_application_setup(
        self, application: Application, environment: Environment, service: ConsumerService
    ) -> tuple[Application, Environment, ConsumerService]:
        return (application, environment, service)

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return Application(
            id=resource_id,
            organisation_id=organisation_id,
            display_name="Application one",
            created_at=NOW,
            updated_at=NOW,
        )

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return Environment(
            id=resource_id,
            organisation_id=organisation_id,
            application_id="application_one",
            display_name="Production",
            production=True,
            region="us-east1",
            created_at=NOW,
            updated_at=NOW,
        )

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        if resource_id != "service_one":
            raise ResourceConflictError("service not found")
        return ConsumerService(
            id="service_one",
            organisation_id=organisation_id,
            application_id="application_one",
            environment_id="environment_one",
            runtime_connection_id="runtime_one",
            runtime_resource="runtime://project-one/us-east1/service-one",
            display_name="Service one",
            endpoint="https://service.example",
            identity="service-one@example.identity.example.com",
            created_at=NOW,
            updated_at=NOW,
        )

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        raise NotImplementedError

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
        service_setup: tuple[Application, Environment, ConsumerService] | None = None,
    ) -> ManagedCredential:
        self.imported_controls = controls
        self.imported_probes = probes
        self.imported_service_setup = service_setup
        return credential

    async def replace_controls(
        self, credential: ManagedCredential, expected_revision: int, controls: ControlVersion
    ) -> tuple[ManagedCredential, ControlVersion]:
        return (credential, controls)

    async def archive_inventory(
        self,
        resources: tuple[
            Connection | Application | Environment | ConsumerService | ManagedCredential, ...
        ],
        bindings: tuple[ConsumerBinding, ...],
    ) -> None:
        self.archived_resources = resources
        self.archived_bindings = bindings

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return self.credential_values

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return tuple(self.connection_values.values())

    async def applications(self, organisation_id: str) -> tuple[Application, ...]:
        return ()

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return ()

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return ()

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return self.binding_values


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_playbook_publishes_then_attaches_to_browser_connection() -> None:
    repository = Catalog()
    repository.connection_values["browser_one"] = _browser_connection()
    service = PlaybookService(repository, clock=lambda: NOW, inventory=repository)
    _, version = await service.create_version(
        "org_one", "playbook_one", "version_one", _draft(), "author_one"
    )
    with pytest.raises(PlaybookError, match="published"):
        await service.attach("org_one", "browser_one", 0, "playbook_one", version.id)
    published = await service.publish("org_one", "playbook_one", version.id, "admin_one")
    attached = await service.attach("org_one", "browser_one", 0, "playbook_one", published.id)
    assert published.state is PlaybookState.PUBLISHED
    assert attached.playbook_version_id == version.id
    assert attached.status is ConnectionStatus.READY


@pytest.mark.anyio
async def test_confirmed_playbook_archive_detaches_browser_connection() -> None:
    repository = Catalog()
    repository.connection_values["browser_one"] = _browser_connection()
    service = PlaybookService(repository, clock=lambda: NOW, inventory=repository)
    root, version = await service.create_version(
        "org_one", "playbook_one", "version_one", _draft(), "author_one"
    )
    published = await service.publish("org_one", root.id, version.id, "admin_one")
    await service.attach("org_one", "browser_one", 0, root.id, published.id)
    archived = await service.archive("org_one", root.id, root.revision, cascade=True)
    connection = repository.connection_values["browser_one"]
    assert archived.archived_at == NOW
    assert connection.playbook_id is None
    assert connection.playbook_version_id is None
    assert connection.status is ConnectionStatus.SETUP_REQUIRED


@pytest.mark.anyio
async def test_playbook_rejects_checkpoints_outside_its_domains() -> None:
    service = PlaybookService(Catalog(), clock=lambda: NOW)
    escaped = _draft().model_copy(update={"login_url_pattern": "https://untrusted.example/login"})
    with pytest.raises(PlaybookError, match="escapes"):
        await service.create_version(
            "org_one", "playbook_one", "version_one", escaped, "author_one"
        )


@pytest.mark.anyio
async def test_playbook_rejects_navigation_outside_its_domains() -> None:
    service = PlaybookService(Catalog(), clock=lambda: NOW)
    navigation = PlaybookStep(
        id="open_keys",
        stage=Stage.CREATE,
        tool="browser.navigate",
        operation="navigate",
        objective="Open the API keys page",
        parameters={"url": "https://untrusted.example/keys"},
        checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
        evidence_checks=frozenset({"page-confirmed"}),
    )
    escaped = _draft().model_copy(update={"steps": (navigation, *_draft().steps)})
    with pytest.raises(PlaybookError, match="escapes"):
        await service.create_version(
            "org_one", "playbook_one", "version_one", escaped, "author_one"
        )


@pytest.mark.anyio
async def test_inventory_rejects_missing_consumer_binding() -> None:
    repository = Catalog()
    service = InventoryService(repository)
    credential = ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="provider_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        secret_reference="secret://project-one/key/versions/1",
        provider="provider",
        kind="api-key",
        display_name="Production key",
        consumer_ids=("service_one",),
        active_generation_id="generation_one",
        control_version="policy_one",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_one",
        organisation_id="org_one",
        credential_id=credential.id,
        state=GenerationState.ACTIVE,
        attempt_id="attempt_one",
        secret_reference=credential.secret_reference,
        created_at=NOW,
    )
    with pytest.raises(ResourceConflictError, match="must match"):
        await service.import_credential(
            credential,
            generation,
            (),
            ControlPreferences(
                automatic_triggers=frozenset({"expiry"}),
                rotate_before_expiry_seconds=604800,
                maximum_observation_seconds=1800,
            ),
            "actor_one",
        )


@pytest.mark.anyio
async def test_inventory_compiles_connector_probes_and_recovery_from_service_choices() -> None:
    repository = Catalog()
    service = InventoryService(
        repository,
        clock=lambda: NOW,
        runtime_metadata=ImportRuntimeMetadata(),
        provider_metadata=ImportMetadata(),
        credential_verifier=ImportVerifier(),
    )
    credential = ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="provider_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        secret_reference="secret://project-one/key/versions/1",
        provider="provider",
        kind="api-key",
        display_name="Production key",
        provider_id="provider_key_one",
        scopes=frozenset({"messages.write"}),
        consumer_ids=("service_one",),
        active_generation_id="generation_one",
        control_version="control_one",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_one",
        organisation_id="org_one",
        credential_id=credential.id,
        provider_id="provider_key_one",
        state=GenerationState.ACTIVE,
        attempt_id="attempt_one",
        secret_reference=credential.secret_reference,
        created_at=NOW,
    )
    binding = ConsumerBinding(
        id="binding_one",
        organisation_id="org_one",
        credential_id=credential.id,
        service_id="service_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="runtime://project-one/us-east1/service-one",
        runtime_secret_name="PROVIDER_KEY",
        secret_reference=credential.secret_reference,
        current_generation_id=generation.id,
    )
    imported = await service.import_credential(
        credential,
        generation,
        (binding,),
        ControlPreferences(
            automatic_triggers=frozenset({"expiry"}),
            rotate_before_expiry_seconds=604800,
            maximum_observation_seconds=1800,
        ),
        "actor_one",
    )
    assert imported == credential
    assert repository.imported_controls is not None
    assert repository.imported_controls.definition.require_revoke_approval is False
    assert repository.imported_controls.definition.protected_tools == frozenset()
    assert (
        "approvers-known"
        not in repository.imported_controls.definition.required_checks[Stage.PREFLIGHT]
    )
    assert repository.imported_controls.definition.required_checks[Stage.APPROVAL] == frozenset(
        {"approval-not-required", "evidence-current"}
    )
    assert repository.imported_controls.definition.require_generation_telemetry is False
    assert (
        "telemetry-healthy"
        not in repository.imported_controls.definition.required_checks[Stage.VERIFY]
    )
    verify_kinds = {
        probe.definition.kind
        for probe in repository.imported_probes
        if probe.id in repository.imported_controls.definition.probe_versions[Stage.VERIFY]
    }
    assert verify_kinds == {
        ProbeKind.PROVIDER,
        ProbeKind.CREDENTIAL,
        ProbeKind.SECRET,
        ProbeKind.RUNTIME,
    }


@pytest.mark.anyio
async def test_inventory_imports_new_runtime_service_with_credential() -> None:

    class RuntimeMetadata:
        async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
            return (
                {
                    "reference": "runtime://project-one/us-east1/service-two",
                    "display_name": "service-two",
                    "endpoint": "https://service-two.runtime.example.com",
                    "identity": "service-two@example.identity.example.com",
                    "region": "us-east1",
                    "environment_name": "Production",
                    "production": True,
                    "secret_bindings": (
                        {
                            "name": "PROVIDER_KEY",
                            "secret": "secret://project-one/key",
                            "version": "1",
                            "container": None,
                        },
                    ),
                },
            )

    repository = Catalog()
    repository.connection_values["runtime_one"] = repository.connection_values[
        "runtime_one"
    ].model_copy(update={"capabilities": frozenset({"runtime.listServices"})})
    service = InventoryService(
        repository,
        clock=lambda: NOW,
        runtime_metadata=RuntimeMetadata(),
        provider_metadata=ImportMetadata(),
        credential_verifier=ImportVerifier(),
    )
    credential = ManagedCredential(
        id="credential_two",
        organisation_id="org_one",
        connection_id="provider_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        secret_reference="secret://project-one/key/versions/1",
        provider="provider",
        kind="api-key",
        display_name="Production key",
        provider_id="provider_key_one",
        scopes=frozenset({"messages.write"}),
        consumer_ids=("service_two",),
        active_generation_id="generation_two",
        control_version="control_two",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_two",
        organisation_id="org_one",
        credential_id=credential.id,
        provider_id="provider_key_one",
        state=GenerationState.ACTIVE,
        attempt_id="attempt_two",
        secret_reference=credential.secret_reference,
        created_at=NOW,
    )
    consumer = RuntimeConsumerSetup(
        application_id="application_two",
        environment_id="environment_two",
        service_id="service_two",
        binding_id="binding_two",
        runtime_connection_id="runtime_one",
        runtime_resource="runtime://project-one/us-east1/service-two",
        runtime_secret_name="PROVIDER_KEY",
    )
    preferences = ControlPreferences(
        automatic_triggers=frozenset({"expiry"}),
        rotate_before_expiry_seconds=604800,
        maximum_observation_seconds=1800,
    )
    with pytest.raises(ResourceConflictError, match="does not use the selected secret version"):
        await service.import_discovered_credential(
            credential,
            generation,
            consumer.model_copy(update={"runtime_secret_name": "UNRELATED_KEY"}),
            preferences,
            "actor_one",
        )
    imported = await service.import_discovered_credential(
        credential, generation, consumer, preferences, "actor_one"
    )
    assert imported == credential
    assert repository.imported_service_setup is not None
    application, environment, imported_service = repository.imported_service_setup
    assert application.display_name == "service-two"
    assert environment.display_name == "Production"
    assert imported_service.runtime_resource == "runtime://project-one/us-east1/service-two"


@pytest.mark.anyio
async def test_inventory_rejects_a_secret_for_another_provider_credential() -> None:
    repository = Catalog()
    service = InventoryService(
        repository,
        clock=lambda: NOW,
        runtime_metadata=ImportRuntimeMetadata(),
        provider_metadata=ImportMetadata(),
        credential_verifier=ImportVerifier("another_provider_key"),
    )
    credential = ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="provider_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        secret_reference="secret://project-one/key/versions/1",
        provider="provider",
        kind="api-key",
        display_name="Production key",
        provider_id="provider_key_one",
        scopes=frozenset({"messages.write"}),
        consumer_ids=("service_one",),
        active_generation_id="generation_one",
        control_version="control_one",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_one",
        organisation_id="org_one",
        credential_id=credential.id,
        provider_id=credential.provider_id,
        state=GenerationState.ACTIVE,
        attempt_id="attempt_one",
        secret_reference=credential.secret_reference,
        created_at=NOW,
    )
    service_value = await repository.get_service("org_one", "service_one")
    binding = ConsumerBinding(
        id="binding_one",
        organisation_id="org_one",
        credential_id=credential.id,
        service_id=service_value.id,
        environment_id=service_value.environment_id,
        runtime_connection_id=service_value.runtime_connection_id,
        runtime_resource=service_value.runtime_resource,
        runtime_secret_name="PROVIDER_KEY",
        secret_reference=credential.secret_reference,
        current_generation_id=generation.id,
    )
    with pytest.raises(ResourceConflictError, match="not managed"):
        await service.import_credential(
            credential,
            generation,
            (binding,),
            ControlPreferences(
                automatic_triggers=frozenset({"expiry"}),
                rotate_before_expiry_seconds=604800,
                maximum_observation_seconds=1800,
            ),
            "actor_one",
        )


@pytest.mark.anyio
async def test_confirmed_service_archive_includes_bound_credential_and_binding() -> None:
    repository = Catalog()
    credential = ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="provider_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        secret_reference="secret://project-one/key/versions/1",
        provider="provider",
        kind="api-key",
        display_name="Production key",
        consumer_ids=("service_one",),
        active_generation_id="generation_one",
        control_version="control_one",
        created_at=NOW,
        updated_at=NOW,
    )
    binding = ConsumerBinding(
        id="binding_one",
        organisation_id="org_one",
        credential_id=credential.id,
        service_id="service_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="runtime://project-one/us-east1/service-one",
        runtime_secret_name="PROVIDER_KEY",
        secret_reference=credential.secret_reference,
        current_generation_id="generation_one",
    )
    repository.credential_values = (credential,)
    repository.binding_values = (binding,)
    inventory = InventoryService(repository, clock=lambda: NOW)
    archived = await inventory.archive_service("org_one", "service_one", 0, cascade=True)
    assert archived.archived_at == NOW
    assert {resource.id for resource in repository.archived_resources} == {
        "service_one",
        "credential_one",
    }
    assert repository.archived_bindings == (binding,)


@pytest.mark.anyio
async def test_inventory_lists_provider_credential_metadata_without_secret_values() -> None:

    class Metadata:
        async def metadata(self, connection: Connection) -> tuple[dict[str, object], ...]:
            assert connection.id == "provider_one"
            return (
                {
                    "provider_id": "provider-key-one",
                    "name": "Production key",
                    "kind": "api-key",
                    "scopes": ["messages.write"],
                },
            )

    repository = Catalog()
    repository.connection_values["provider_one"] = repository.connection_values[
        "provider_one"
    ].model_copy(
        update={
            "capabilities": frozenset(
                {"provider.listCredentialMetadata", "provider.createCredential"}
            )
        }
    )
    service = InventoryService(repository, provider_metadata=Metadata())
    metadata = await service.list_provider_credentials("org_one", "provider_one")
    assert metadata[0].provider_id == "provider-key-one"
    assert metadata[0].scopes == ("messages.write",)
    assert "secret" not in metadata[0].model_dump()
    repository.connection_values["provider_one"] = repository.connection_values[
        "provider_one"
    ].model_copy(update={"status": ConnectionStatus.DEGRADED})
    with pytest.raises(ResourceConflictError, match="not ready"):
        await service.list_provider_credentials("org_one", "provider_one")


@pytest.mark.anyio
async def test_inventory_resolves_provider_credential_from_stored_secret() -> None:
    repository = Catalog()
    service = InventoryService(
        repository, provider_metadata=ImportMetadata(), credential_verifier=ImportVerifier()
    )
    metadata = await service.resolve_credential(
        "org_one", "provider_one", "secret_one", "secret://project-one/key/versions/1"
    )
    assert metadata.provider_id == "provider_key_one"
    assert metadata.kind == "api-key"
    assert metadata.scopes == ("messages.write",)


@pytest.mark.anyio
async def test_inventory_resolves_browser_credential_from_enabled_secret_version() -> None:
    repository = Catalog()
    repository.connection_values["browser_one"] = _browser_connection().model_copy(
        update={
            "status": ConnectionStatus.READY,
            "playbook_id": "playbook_one",
            "playbook_version_id": "version_one",
            "authorization_reference": "secret://project-one/browser-session/versions/1",
        }
    )
    service = InventoryService(repository, secret_metadata=BrowserSecretMetadata())
    metadata = await service.resolve_credential(
        "org_one", "browser_one", "secret_one", "secret://project-one/key/versions/1"
    )
    assert metadata.provider_id.startswith("browser-secret-")
    assert metadata.kind == "api-key"
    assert metadata.scopes == ()
    assert "secret" not in metadata.model_dump()


@pytest.mark.anyio
async def test_inventory_rejects_disabled_browser_credential_version() -> None:

    class DisabledSecretMetadata(BrowserSecretMetadata):
        async def versions_for(
            self, connection: Connection, secret: str
        ) -> tuple[dict[str, object], ...]:
            return ({"name": f"{secret}/versions/1", "state": "DISABLED"},)

    repository = Catalog()
    repository.connection_values["browser_one"] = _browser_connection().model_copy(
        update={
            "status": ConnectionStatus.READY,
            "playbook_id": "playbook_one",
            "playbook_version_id": "version_one",
            "authorization_reference": "secret://project-one/browser-session/versions/1",
        }
    )
    service = InventoryService(repository, secret_metadata=DisabledSecretMetadata())
    with pytest.raises(ResourceConflictError, match="not enabled"):
        await service.resolve_credential(
            "org_one", "browser_one", "secret_one", "secret://project-one/key/versions/1"
        )


@pytest.mark.anyio
async def test_inventory_imports_existing_browser_credential_without_provider_id() -> None:
    repository = Catalog()
    repository.connection_values["browser_one"] = _browser_connection().model_copy(
        update={
            "status": ConnectionStatus.READY,
            "playbook_id": "playbook_one",
            "playbook_version_id": "version_one",
            "authorization_reference": "secret://project-one/browser-session/versions/1",
        }
    )
    service = InventoryService(
        repository,
        clock=lambda: NOW,
        secret_metadata=BrowserSecretMetadata(),
        runtime_metadata=ImportRuntimeMetadata(),
    )
    credential = ManagedCredential(
        id="credential_browser",
        organisation_id="org_one",
        connection_id="browser_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/key",
        secret_reference="secret://project-one/key/versions/1",
        provider="vendor",
        kind="api-key",
        display_name="Production key",
        provider_id=None,
        scopes=frozenset(),
        consumer_ids=("service_one",),
        active_generation_id="generation_browser",
        control_version="control_browser",
        created_at=NOW,
        updated_at=NOW,
    )
    generation = CredentialGeneration(
        id="generation_browser",
        organisation_id="org_one",
        credential_id=credential.id,
        provider_id=None,
        scopes=frozenset(),
        state=GenerationState.ACTIVE,
        attempt_id="attempt_browser",
        secret_reference=credential.secret_reference,
        created_at=NOW,
    )
    consumer = RuntimeConsumerSetup(
        application_id="application_browser",
        environment_id="environment_browser",
        service_id="service_one",
        binding_id="binding_browser",
        runtime_connection_id="runtime_one",
        runtime_resource="runtime://project-one/us-east1/service-one",
        runtime_secret_name="PROVIDER_KEY",
        environment_name="Production",
    )
    imported = await service.import_discovered_credential(
        credential,
        generation,
        consumer,
        ControlPreferences(
            automatic_triggers=frozenset({"expiry"}),
            rotate_before_expiry_seconds=604800,
            maximum_observation_seconds=1800,
        ),
        "actor_one",
    )
    assert imported == credential
    assert repository.imported_controls is not None
    assert repository.imported_controls.definition.allowed_tools.intersection(
        {"browser.secure-capture", "browser.revokeCredential"}
    ) == {"browser.secure-capture", "browser.revokeCredential"}
    assert repository.imported_controls.definition.protected_tools == frozenset(
        {"browser.secure-capture"}
    )
    approval_controls = update_controls(
        repository.imported_controls.definition,
        ControlPreferences(
            automatic_triggers=frozenset({"expiry"}),
            rotate_before_expiry_seconds=604800,
            maximum_observation_seconds=1800,
            require_revoke_approval=True,
        ),
    )
    assert approval_controls.protected_tools.issuperset(
        {
            "browser.secure-capture",
            "browser.revokeCredential",
            "secretStore.disableVersion",
            "secretStore.destroyVersion",
        }
    )
    assert approval_controls.require_revoke_approval is True
    assert "approvers-known" in approval_controls.required_checks[Stage.PREFLIGHT]
    automatic_controls = update_controls(
        approval_controls,
        ControlPreferences(
            automatic_triggers=frozenset({"expiry"}),
            rotate_before_expiry_seconds=604800,
            maximum_observation_seconds=1800,
        ),
    )
    assert automatic_controls.protected_tools == frozenset({"browser.secure-capture"})
    assert automatic_controls.require_revoke_approval is False
    assert "approvers-known" not in automatic_controls.required_checks[Stage.PREFLIGHT]
    verify_kinds = {
        probe.definition.kind
        for probe in repository.imported_probes
        if probe.id in repository.imported_controls.definition.probe_versions[Stage.VERIFY]
    }
    assert verify_kinds == {ProbeKind.SECRET, ProbeKind.RUNTIME}


@pytest.mark.anyio
async def test_inventory_lists_runtime_resources_inside_connection_boundary() -> None:

    class RuntimeMetadata:
        async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
            assert connection.id == "runtime_one"
            return (
                {
                    "reference": "runtime://project-one/us-east1/service-two",
                    "display_name": "service-two",
                    "endpoint": "https://service-two.runtime.example.com",
                    "identity": "service-two@example.identity.example.com",
                    "region": "us-east1",
                },
            )

    repository = Catalog()
    repository.connection_values["runtime_one"] = repository.connection_values[
        "runtime_one"
    ].model_copy(update={"capabilities": frozenset({"runtime.listServices"})})
    service = InventoryService(repository, runtime_metadata=RuntimeMetadata())
    resources = await service.list_runtime_resources("org_one", "runtime_one")
    assert resources[0].display_name == "service-two"
    assert resources[0].identity == "service-two@example.identity.example.com"


@pytest.mark.anyio
async def test_inventory_reports_identity_authorization_failure_as_connection_conflict() -> None:

    class UnavailableSecretMetadata:
        async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
            del connection
            raise ConnectorAuthenticationError("Workload identity could not be authorized")

        async def versions_for(
            self, connection: Connection, secret: str
        ) -> tuple[dict[str, object], ...]:
            del connection, secret
            return ()

    service = InventoryService(Catalog(), secret_metadata=UnavailableSecretMetadata())
    with pytest.raises(
        ResourceConflictError,
        match="Secret discovery unavailable: Workload identity could not be authorized",
    ):
        await service.list_secret_resources("org_one", "secret_one")


@pytest.mark.anyio
async def test_service_runtime_must_stay_inside_its_connection_boundary() -> None:
    service = InventoryService(Catalog())
    escaped = ConsumerService(
        id="service_one",
        organisation_id="org_one",
        application_id="application_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="runtime://another-project/us-east1/service-one",
        display_name="Service one",
        identity="service-one@example.identity.example.com",
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(ResourceConflictError, match="escapes"):
        await service.add_service(escaped)


@pytest.mark.anyio
async def test_application_setup_validates_relationships_before_atomic_write() -> None:
    repository = Catalog()
    inventory = InventoryService(repository)
    application = await repository.get_application("org_one", "application_one")
    environment = await repository.get_environment("org_one", "environment_one")
    consumer = await repository.get_service("org_one", "service_one")
    created = await inventory.add_application_setup(application, environment, consumer)
    assert created == (application, environment, consumer)
    with pytest.raises(ResourceConflictError, match="does not belong"):
        await inventory.add_application_setup(
            application,
            environment.model_copy(update={"application_id": "application_other"}),
            consumer,
        )


@pytest.mark.anyio
async def test_application_setup_derives_runtime_service_metadata() -> None:

    class RuntimeMetadata:
        async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
            return (
                {
                    "reference": "runtime://project-one/us-east1/service-two",
                    "display_name": "service-two",
                    "endpoint": "https://service-two.runtime.example.com",
                    "identity": "service-two@example.identity.example.com",
                    "region": "us-east1",
                    "environment_name": None,
                    "production": None,
                },
            )

    repository = Catalog()
    repository.connection_values["runtime_one"] = repository.connection_values[
        "runtime_one"
    ].model_copy(update={"capabilities": frozenset({"runtime.listServices"})})
    inventory = InventoryService(repository, clock=lambda: NOW, runtime_metadata=RuntimeMetadata())
    application, environment, service = await inventory.add_discovered_application_setup(
        "org_one",
        "application_two",
        "environment_two",
        "service_two",
        "runtime_one",
        "runtime://project-one/us-east1/service-two",
        "Production",
    )
    assert application.display_name == "service-two"
    assert environment.region == "us-east1"
    assert environment.production is True
    assert service.display_name == "service-two"
    assert service.endpoint == "https://service-two.runtime.example.com"


@pytest.mark.anyio
async def test_service_setup_derives_runtime_metadata() -> None:

    class RuntimeMetadata:
        async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
            return (
                {
                    "reference": "runtime://project-one/us-east1/service-two",
                    "display_name": "service-two",
                    "endpoint": "https://service-two.runtime.example.com",
                    "identity": "service-two@example.identity.example.com",
                    "region": "us-east1",
                },
            )

    repository = Catalog()
    repository.connection_values["runtime_one"] = repository.connection_values[
        "runtime_one"
    ].model_copy(update={"capabilities": frozenset({"runtime.listServices"})})
    inventory = InventoryService(repository, clock=lambda: NOW, runtime_metadata=RuntimeMetadata())
    service = await inventory.add_discovered_service(
        "org_one",
        "service_two",
        "application_one",
        "environment_one",
        "runtime_one",
        "runtime://project-one/us-east1/service-two",
    )
    assert service.display_name == "service-two"
    assert service.identity == "service-two@example.identity.example.com"


@pytest.mark.anyio
async def test_browser_connection_requires_domains_and_capability() -> None:
    service = InventoryService(Catalog())
    browser = _browser_connection().model_copy(update={"allowed_resources": ("invalid",)})
    with pytest.raises(ResourceConflictError, match="allowed domains"):
        await service.add_connection(browser)
    without_capability = _browser_connection().model_copy(
        update={"capabilities": frozenset({"provider.createCredential"})}
    )
    with pytest.raises(ResourceConflictError, match="browser capability"):
        await service.add_connection(without_capability)


@pytest.mark.anyio
async def test_provider_connection_is_ready_only_after_live_metadata_validation() -> None:
    repository = Catalog()
    candidate = _api_connection("provider_new", ConnectionRole.PROVIDER).model_copy(
        update={
            "status": ConnectionStatus.SETUP_REQUIRED,
            "capabilities": frozenset(
                {
                    "provider.listCredentialMetadata",
                    "provider.createCredential",
                    "provider.getCredentialStatus",
                    "provider.revokeCredential",
                    "provider.testCredential",
                }
            ),
            "authenticated_at": None,
            "last_validated_at": None,
        }
    )
    created = await InventoryService(
        repository, clock=lambda: NOW, provider_metadata=ImportMetadata()
    ).add_connection(candidate)
    assert created.status is ConnectionStatus.READY
    assert created.authenticated_at == NOW
    assert created.last_validated_at == NOW


@pytest.mark.anyio
async def test_runtime_connections_are_ready_only_after_read_only_discovery() -> None:

    class RuntimeMetadata:
        async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
            assert connection.platform == "runtime-service"
            return ()

    class SecretMetadata:
        async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]:
            assert connection.platform == "secret-store"
            return ()

        async def versions_for(
            self, connection: Connection, secret: str
        ) -> tuple[dict[str, object], ...]:
            return ()

    repository = Catalog()
    runtime = _api_connection("runtime_new", ConnectionRole.RUNTIME).model_copy(
        update={
            "status": ConnectionStatus.SETUP_REQUIRED,
            "authenticated_at": None,
            "last_validated_at": None,
        }
    )
    secret_store = _api_connection("secret_new", ConnectionRole.SECRET_STORE).model_copy(
        update={
            "status": ConnectionStatus.SETUP_REQUIRED,
            "authenticated_at": None,
            "last_validated_at": None,
        }
    )
    service = InventoryService(
        repository,
        clock=lambda: NOW,
        runtime_metadata=RuntimeMetadata(),
        secret_metadata=SecretMetadata(),
    )
    created_runtime = await service.add_connection(runtime)
    created_secret_store = await service.add_connection(secret_store)
    assert created_runtime.status is ConnectionStatus.READY
    assert created_runtime.authenticated_at == NOW
    assert created_runtime.last_validated_at == NOW
    assert created_secret_store.status is ConnectionStatus.READY
    assert created_secret_store.authenticated_at == NOW
    assert created_secret_store.last_validated_at == NOW


@pytest.mark.anyio
async def test_runtime_connections_fail_closed_when_discovery_is_unavailable() -> None:
    runtime = _api_connection("runtime_new", ConnectionRole.RUNTIME).model_copy(
        update={"status": ConnectionStatus.SETUP_REQUIRED}
    )
    secret_store = _api_connection("secret_new", ConnectionRole.SECRET_STORE).model_copy(
        update={"status": ConnectionStatus.SETUP_REQUIRED}
    )
    service = InventoryService(Catalog())
    with pytest.raises(ResourceConflictError, match="runtime connection validation"):
        await service.add_connection(runtime)
    with pytest.raises(ResourceConflictError, match="secret-store connection validation"):
        await service.add_connection(secret_store)


def test_audit_hash_binds_sequence_and_previous_event() -> None:
    first = event_hash(
        "org_one",
        0,
        "credential.imported",
        "user_one",
        "credentials/credential_one",
        None,
        {"provider": "vendor"},
        (),
        GENESIS,
        NOW,
        "us-east1",
    )
    second = event_hash(
        "org_one",
        1,
        "credential.updated",
        "user_one",
        "credentials/credential_one",
        None,
        {"provider": "vendor"},
        (),
        first,
        NOW,
        "us-east1",
    )
    assert first != second


def test_legacy_connections_migrate_to_the_explicit_connection_model() -> None:
    connection = Connection.model_validate(
        {
            "id": "connection_provider",
            "organisation_id": "org_one",
            "kind": "provider",
            "provider": "vendor",
            "display_name": "Vendor API",
            "auth_reference": "secret://p/admin/versions/1",
            "capabilities": ["provider.listCredentialMetadata"],
            "allowed_resources": ["account-one"],
            "http": make_http_provider_api().model_dump(mode="json"),
            "status": "ready",
            "region": "us-east1",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    assert connection.platform == "vendor"
    assert connection.roles == frozenset({ConnectionRole.PROVIDER})
    assert connection.interface is ConnectionInterface.API
    assert connection.authorization is ConnectionAuthorization.API_KEY


def _browser_connection() -> Connection:
    return Connection(
        id="browser_one",
        organisation_id="org_one",
        platform="vendor",
        display_name="Vendor console",
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.BROWSER,
        authorization=ConnectionAuthorization.BROWSER_SESSION,
        authorization_reference="secret://p/session/versions/1",
        capabilities=frozenset({"browser.execute", "browser.authenticate"}),
        allowed_resources=("vendor.example.com", "*.vendor.example.com"),
        status=ConnectionStatus.SETUP_REQUIRED,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _api_connection(identifier: str, role: ConnectionRole) -> Connection:
    platform = {
        ConnectionRole.PROVIDER: "provider",
        ConnectionRole.SECRET_STORE: "secret-store",
        ConnectionRole.RUNTIME: "runtime-service",
    }[role]
    allowed = {
        ConnectionRole.PROVIDER: ("account-one",),
        ConnectionRole.SECRET_STORE: ("secret://project-one",),
        ConnectionRole.RUNTIME: ("runtime://project-one/us-east1",),
    }[role]
    return Connection(
        id=identifier,
        organisation_id="org_one",
        platform=platform,
        display_name=identifier,
        roles=frozenset({role}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY
        if role is ConnectionRole.PROVIDER
        else ConnectionAuthorization.WORKLOAD_IDENTITY,
        authorization_reference="secret://project-one/provider-admin/versions/1"
        if role is ConnectionRole.PROVIDER
        else "workload-identity://service",
        capabilities=frozenset(
            {
                f"{role.value}.operate",
                *({"runtime.listServices"} if role is ConnectionRole.RUNTIME else set()),
            }
        ),
        allowed_resources=allowed,
        http=make_http_provider_api() if role is ConnectionRole.PROVIDER else None,
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def _draft() -> PlaybookDraft:
    checkpoint = PageCheckpoint(url_pattern="https://vendor.example.com/keys")
    selector = Selector(kind=SelectorKind.TEST_ID, value="key-control")
    return PlaybookDraft(
        name="Vendor browser rotation",
        platform="vendor",
        allowed_domains=("vendor.example.com",),
        login_url_pattern="https://vendor.example.com/login",
        steps=(
            PlaybookStep(
                id="create_key",
                stage=Stage.CREATE,
                effect=PlaybookEffect.CREATE_CREDENTIAL,
                tool="browser.secure-capture",
                operation="capture",
                objective="Submit the credential creation form",
                selectors=(Selector(kind=SelectorKind.TEST_ID, value="create-credential"),),
                checkpoint=checkpoint,
                secure_field=SecureField(
                    name="api_key",
                    selector=selector,
                    provider_id_selector=Selector(kind=SelectorKind.TEST_ID, value="key-id"),
                ),
                evidence_checks=frozenset({"captured"}),
            ),
            PlaybookStep(
                id="revoke_key",
                stage=Stage.REVOKE,
                effect=PlaybookEffect.REVOKE_CREDENTIAL,
                tool="browser.revokeCredential",
                operation="revoke",
                objective="Revoke the prior credential",
                selectors=(selector,),
                checkpoint=checkpoint,
                evidence_checks=frozenset({"revoked"}),
            ),
        ),
    )


def test_playbook_digest_matches_immutable_definition() -> None:
    assert len(digest(_draft())) == 64
