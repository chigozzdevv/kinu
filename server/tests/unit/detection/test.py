from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    CredentialGeneration,
    GenerationState,
    ManagedCredential,
)
from ingestion.detection import DetectionService
from testkit import make_control_version, make_http_provider_api

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_detection_records_expiry_and_reports_provider_and_runtime_drift() -> None:
    inventory = Inventory()
    provider = Provider(
        (
            {
                "provider_id": "provider-key-one",
                "scopes": ["mail.send", "admin"],
                "expires_at": (NOW + timedelta(days=1)).isoformat(),
            },
        )
    )
    service = DetectionService(
        inventory,
        Controls(),
        provider,
        {"runtime-service": Runtime(generation_id="generation-old")},
        lambda: NOW,
    )
    events = await service.detect("org_one")
    assert {event.kind for event in events} == {
        "credential-expiring",
        "credential-provider-drift",
        "credential-runtime-drift",
    }
    assert all(event.resource.credential_id == "credential_one" for event in events)
    assert inventory.observation is not None
    assert inventory.observation["expires_at"] == NOW + timedelta(days=1)
    assert len(inventory.observation["metadata_digest"]) == 64


@pytest.mark.anyio
async def test_detection_treats_missing_provider_identifier_as_disabled() -> None:
    service = DetectionService(
        Inventory(include_binding=False),
        Controls(require_runtime_alignment=False),
        Provider(()),
        {},
        lambda: NOW,
    )
    events = await service.detect("org_one")
    assert len(events) == 1
    assert events[0].kind == "credential-disabled"
    assert events[0].confidence.value == "verified"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("include_binding", "runtime_generation", "require_alignment", "observed"),
    [
        (True, "generation_one", True, True),
        (True, "generation_old", True, False),
        (True, None, True, False),
        (False, "generation_one", True, False),
        (True, "generation_one", False, False),
    ],
)
async def test_runtime_recovery_requires_an_enabled_and_aligned_consumer(
    include_binding: bool,
    runtime_generation: str | None,
    require_alignment: bool,
    observed: bool,
) -> None:
    inventory = Inventory(include_binding=include_binding)
    service = DetectionService(
        inventory,
        Controls(require_runtime_alignment=require_alignment),
        Provider(({"provider_id": "provider-key-one", "scopes": ["mail.send"]},)),
        {"runtime-service": Runtime(runtime_generation)} if runtime_generation else {},
        lambda: NOW,
    )
    await service.detect("org_one")
    assert (inventory.runtime_observation is not None) is observed


@pytest.mark.anyio
async def test_detection_uses_stored_metadata_for_browser_managed_credentials() -> None:
    inventory = Inventory(include_binding=False)
    inventory.credential = inventory.credential.model_copy(
        update={
            "connection_id": "browser_one",
            "expires_at": NOW + timedelta(days=1),
            "rotation_due_at": NOW,
            "last_observed_at": NOW,
        }
    )
    inventory.generation = inventory.generation.model_copy(
        update={"expires_at": NOW + timedelta(days=1), "last_observed_at": NOW}
    )

    async def browser_connections(organisation_id: str) -> tuple[Connection, ...]:
        return (
            Connection(
                id="browser_one",
                organisation_id=organisation_id,
                platform="sendgrid",
                display_name="SendGrid console",
                roles=frozenset({ConnectionRole.PROVIDER}),
                interface=ConnectionInterface.BROWSER,
                authorization=ConnectionAuthorization.BROWSER_SESSION,
                authorization_reference="secret://project-one/sendgrid-session/versions/1",
                capabilities=frozenset({"browser.execute"}),
                allowed_resources=("app.sendgrid.com",),
                playbook_id="playbook_sendgrid",
                playbook_version_id="playbook_sendgrid_v1",
                status=ConnectionStatus.READY,
                region="us-central1",
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    inventory.connections = browser_connections  # type: ignore[method-assign]
    provider = Provider(())
    service = DetectionService(
        inventory, Controls(require_runtime_alignment=False), provider, {}, lambda: NOW
    )
    events = await service.detect("org_one")
    assert [event.kind for event in events] == ["credential-expiring"]
    assert inventory.observation is None


@pytest.mark.anyio
async def test_fresh_provider_metadata_does_not_raise_a_stale_alert_after_a_monitoring_gap() -> (
    None
):
    inventory = Inventory(include_binding=False)
    inventory.credential = inventory.credential.model_copy(
        update={"created_at": NOW - timedelta(days=3), "last_observed_at": NOW - timedelta(days=2)}
    )
    provider = Provider(({"provider_id": "provider-key-one", "scopes": ["mail.send"]},))
    service = DetectionService(
        inventory, Controls(require_runtime_alignment=False), provider, {}, lambda: NOW
    )
    assert await service.detect("org_one") == ()
    assert inventory.observation is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"organisation_id": "org_other"},
        {"platform": "other-provider"},
        {"roles": frozenset({ConnectionRole.RUNTIME})},
        {"status": ConnectionStatus.DEGRADED},
        {"archived_at": NOW},
        {"authorization_expires_at": NOW},
        {"interface": ConnectionInterface.BROWSER},
    ],
)
async def test_browser_metadata_uses_only_its_live_authorized_api_verifier(
    changes: dict[str, Any],
) -> None:
    inventory = Inventory(include_binding=False)
    inventory.credential = inventory.credential.model_copy(
        update={
            "connection_id": "browser_one",
            "provider": "resend",
            "created_at": NOW - timedelta(days=3),
            "last_observed_at": NOW - timedelta(days=2),
        }
    )
    api = (await inventory.connections("org_one"))[0].model_copy(update={"platform": "resend"})
    browser = Connection.model_validate(
        api.model_dump()
        | {
            "id": "browser_one",
            "interface": ConnectionInterface.BROWSER,
            "authorization": ConnectionAuthorization.BROWSER_SESSION,
            "playbook_id": "playbook_one",
            "playbook_version_id": "version_one",
            "verification_connection_id": api.id,
            "http": None,
        }
    )

    async def connections(organisation_id: str) -> tuple[Connection, ...]:
        return browser, api.model_copy(update=changes)

    inventory.connections = connections  # type: ignore[method-assign]
    provider = Provider(({"provider_id": "provider-key-one", "scopes": ["mail.send"]},))
    service = DetectionService(
        inventory, Controls(require_runtime_alignment=False), provider, {}, lambda: NOW
    )
    events = await service.detect("org_one")
    if changes:
        assert [event.kind for event in events] == ["credential-metadata-stale"]
        assert provider.connections == []
        assert inventory.observation is None
    else:
        assert events == ()
        assert provider.connections == [api.id]
        assert inventory.observation is not None


class Inventory:
    def __init__(self, include_binding: bool = True) -> None:
        self.include_binding = include_binding
        self.observation: dict[str, Any] | None = None
        self.runtime_observation: tuple[ConsumerBinding, ...] | None = None
        self.credential = ManagedCredential(
            id="credential_one",
            organisation_id="org_one",
            connection_id="provider_one",
            secret_store_connection_id="secret_one",
            secret_resource="secret://project-one/mailer",
            secret_reference="secret://project-one/mailer",
            provider="sendgrid",
            kind="api-key",
            display_name="Production mailer",
            provider_id="provider-key-one",
            scopes=frozenset({"mail.send"}),
            consumer_ids=("service_one",) if include_binding else (),
            active_generation_id="generation_one",
            control_version="policy_one",
            created_at=NOW,
            updated_at=NOW,
        )
        self.generation = CredentialGeneration(
            id="generation_one",
            organisation_id="org_one",
            credential_id="credential_one",
            provider_id="provider-key-one",
            scopes=frozenset({"mail.send"}),
            state=GenerationState.ACTIVE,
            attempt_id="attempt_one",
            secret_reference="secret://project-one/mailer/versions/7",
            created_at=NOW,
        )

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return (self.credential,)

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        values = [
            Connection(
                id="provider_one",
                organisation_id="org_one",
                platform="sendgrid",
                display_name="SendGrid",
                roles=frozenset({ConnectionRole.PROVIDER}),
                interface=ConnectionInterface.API,
                authorization=ConnectionAuthorization.API_KEY,
                authorization_reference="secret://project-one/sendgrid/versions/1",
                capabilities=frozenset({"provider.listCredentialMetadata"}),
                allowed_resources=("provider-key-one",),
                http=make_http_provider_api(),
                status=ConnectionStatus.READY,
                region="us-central1",
                created_at=NOW,
                updated_at=NOW,
            )
        ]
        if self.include_binding:
            values.append(
                Connection(
                    id="runtime_one",
                    organisation_id="org_one",
                    platform="runtime-service",
                    display_name="Runtime service",
                    roles=frozenset({ConnectionRole.RUNTIME}),
                    interface=ConnectionInterface.API,
                    authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
                    authorization_reference="workload-identity://kinu-broker@project-one.identity.example.com",
                    capabilities=frozenset({"runtime.inspectSecretBindings"}),
                    allowed_resources=("runtime://project-one/us-central1/mailer",),
                    status=ConnectionStatus.READY,
                    region="us-central1",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        return tuple(values)

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        if not self.include_binding:
            return ()
        return (
            ConsumerBinding(
                id="binding_one",
                organisation_id="org_one",
                credential_id="credential_one",
                service_id="service_one",
                environment_id="environment_one",
                runtime_connection_id="runtime_one",
                runtime_resource="runtime://project-one/us-central1/mailer",
                runtime_secret_name="MAILER_API_KEY",
                secret_reference="secret://project-one/mailer/versions/7",
                current_generation_id="generation_one",
            ),
        )

    async def generations(self, organisation_id: str) -> tuple[CredentialGeneration, ...]:
        return (self.generation,)

    async def record_observation(self, *args: Any, **kwargs: Any) -> ManagedCredential:
        self.observation = kwargs or {"expires_at": args[5], "metadata_digest": args[7]}
        return self.credential

    async def record_runtime_observation(
        self,
        credential: ManagedCredential,
        bindings: tuple[ConsumerBinding, ...],
        observed_at: datetime,
    ) -> None:
        self.runtime_observation = bindings

    async def runtime_recovery_at(
        self, organisation_id: str, credential_id: str, generation_id: str
    ) -> datetime | None:
        return None


class Provider:
    def __init__(self, metadata: tuple[dict[str, Any], ...]) -> None:
        self.value = metadata
        self.connections: list[str] = []

    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        self.connections.append(connection.id)
        return self.value


class Runtime:
    def __init__(self, generation_id: str) -> None:
        self.generation_id = generation_id

    async def inspect(self, connection: Connection, service_name: str) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "reconciling": False,
            "secret_bindings": [
                {
                    "name": "SENDGRID_API_KEY",
                    "valueSource": {"secretKeyRef": {"secret": "mailer", "version": "7"}},
                }
            ],
        }


class Controls:
    def __init__(self, require_runtime_alignment: bool = True) -> None:
        controls = make_control_version(credential_id="credential_one", now=NOW)
        self.value = controls.model_copy(
            update={
                "definition": controls.definition.model_copy(
                    update={"require_runtime_alignment": require_runtime_alignment}
                )
            }
        )

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> Any:
        assert credential_id == self.value.credential_id
        return self.value
