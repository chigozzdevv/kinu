from datetime import UTC, datetime

import pytest
from contracts import (
    Confidence,
    ConsumerBinding,
    ConsumerService,
    ControlDefinition,
    ControlVersion,
    CorrelationCandidate,
    ExposureSource,
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
    RecoveryMode,
    Severity,
    SourceResource,
)
from core.errors import ResourceConflictError
from core.incident import IncidentService
from core.workflow import RunWorkflow
from policy import REQUIRED_CHECKS, digest
from testkit import MemoryRunRepository

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class Incidents:
    def __init__(self) -> None:
        self.values: dict[str, Incident] = {}

    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]:
        existing = self.values.get(incident.id)
        if existing is not None:
            return (existing, False)
        self.values[incident.id] = incident
        return (incident, True)

    async def get(self, organisation_id: str, incident_id: str) -> Incident:
        return self.values[incident_id]

    async def list_incidents(
        self,
        organisation_id: str,
        limit: int,
        *,
        statuses: frozenset[IncidentStatus] | None = None,
        before: str | None = None,
    ) -> tuple[Incident, ...]:
        anchor = await self.get(organisation_id, before) if before else None
        values = [
            item
            for item in self.values.values()
            if item.organisation_id == organisation_id
            and (statuses is None or item.status in statuses)
            and (anchor is None or (item.created_at, item.id) < (anchor.created_at, anchor.id))
        ]
        return tuple(
            sorted(values, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]
        )

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[CorrelationCandidate, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident:
        current = self.values[incident_id]
        if current.revision != expected_revision:
            raise ResourceConflictError("incident revision changed")
        changed = current.model_copy(
            update={
                "candidates": candidates,
                "credential_id": credential_id,
                "status": IncidentStatus.ACTION
                if credential_id is not None
                else IncidentStatus.CORRELATING,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.values[incident_id] = changed
        return changed

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident:
        current = self.values[incident_id]
        if current.revision != expected_revision:
            raise ResourceConflictError("incident revision changed")
        changed = current.model_copy(
            update={
                "credential_id": credential_id,
                "run_id": run_id,
                "status": IncidentStatus.ROTATING,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.values[incident_id] = changed
        return changed

    async def advance_run(
        self, organisation_id: str, run_id: str, status: IncidentStatus, updated_at: datetime
    ) -> tuple[Incident, ...]:
        values = []
        for incident_id, current in self.values.items():
            if current.run_id != run_id:
                continue
            changed = current.model_copy(
                update={
                    "status": status,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            self.values[incident_id] = changed
            values.append(changed)
        return tuple(values)

    async def dismiss(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        reason: str,
        updated_at: datetime,
    ) -> Incident:
        current = self.values[incident_id]
        changed = current.model_copy(
            update={
                "status": IncidentStatus.DISMISSED,
                "dismissal_reason": reason,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.values[incident_id] = changed
        return changed


class Inventory:
    def __init__(
        self,
        credentials: tuple[ManagedCredential, ...],
        exposure_sources: dict[str, tuple[ExposureSource, ...]] | None = None,
    ) -> None:
        self._credentials = credentials
        self._exposure_sources = exposure_sources or {}

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return self._credentials

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return (_service(),)

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return tuple(_binding(item.id) for item in self._credentials)

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        definition = ControlDefinition(
            required_checks=REQUIRED_CHECKS,
            allowed_tools=frozenset({"verification.run"}),
            allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
            maximum_observation_seconds=1800,
            require_revoke_approval=True,
            exposure_sources=self._exposure_sources.get(credential_id, ()),
        )
        return ControlVersion(
            id=version_id,
            organisation_id=organisation_id,
            credential_id=credential_id,
            number=1,
            definition=definition,
            digest=digest(definition),
            created_by="actor_one",
            created_at=NOW,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_exact_provider_identity_correlates_and_starts_rotation() -> None:
    repository = Incidents()
    workflow = RunWorkflow(MemoryRunRepository(), clock=lambda: NOW)
    service = IncidentService(
        repository,
        lambda: NOW,
        Inventory((_credential("credential_one", "provider-key-one"),)),
        workflow,
    )
    incident, applied = await service.ingest("incident_one", _event("provider-key-one"))
    assert applied
    assert incident.status is IncidentStatus.ACTION
    assert incident.credential_id == "credential_one"
    assert incident.candidates[0].confidence is Confidence.VERIFIED
    linked, run, created = await service.start_rotation(
        "org_one",
        incident.id,
        "command_one",
        "operator_one",
        "policy_one",
        "credential exposed",
        "critical",
        NOW,
    )
    assert created
    assert linked.status is IncidentStatus.ROTATING
    assert linked.run_id == run.id
    assert run.trigger.source == "github-secret-scanning"
    assert run.credential_id == "credential_one"
    contained = await service.advance_run("org_one", run.id, IncidentStatus.CONTAINED)
    resolved = await service.advance_run("org_one", run.id, IncidentStatus.RESOLVED)
    assert contained[0].status is IncidentStatus.CONTAINED
    assert resolved[0].status is IncidentStatus.RESOLVED


@pytest.mark.anyio
async def test_ambiguous_repository_match_requires_explicit_confirmation() -> None:
    repository = Incidents()
    service = IncidentService(
        repository,
        lambda: NOW,
        Inventory(
            (
                _credential("credential_one", "provider-key-one"),
                _credential("credential_two", "provider-key-two"),
            )
        ),
    )
    incident, _ = await service.ingest("incident_one", _event(None))
    assert incident.status is IncidentStatus.CORRELATING
    assert incident.credential_id is None
    assert {item.confidence for item in incident.candidates} == {Confidence.HIGH}
    confirmed = await service.confirm("org_one", incident.id, incident.revision, "credential_two")
    assert confirmed.status is IncidentStatus.ACTION
    assert confirmed.credential_id == "credential_two"
    assert sum(item.confidence is Confidence.VERIFIED for item in confirmed.candidates) == 1


@pytest.mark.anyio
async def test_incident_cannot_be_dismissed_after_rotation_starts() -> None:
    repository = Incidents()
    workflow = RunWorkflow(MemoryRunRepository(), clock=lambda: NOW)
    service = IncidentService(
        repository,
        lambda: NOW,
        Inventory((_credential("credential_one", "provider-key-one"),)),
        workflow,
    )
    incident, _ = await service.ingest("incident_one", _event("provider-key-one"))
    linked, _, _ = await service.start_rotation(
        "org_one",
        incident.id,
        "command_one",
        "operator_one",
        "policy_one",
        "credential exposed",
        "critical",
        NOW,
    )
    with pytest.raises(ResourceConflictError, match="after rotation starts"):
        await service.dismiss(
            "org_one", linked.id, linked.revision, "False positive", "operator_one"
        )


@pytest.mark.anyio
async def test_configured_exposure_source_correlates_one_credential() -> None:
    repository = Incidents()
    source = ExposureSource(connection_id="connection_github", resource="example/mailer")
    service = IncidentService(
        repository,
        lambda: NOW,
        Inventory(
            (
                _credential("credential_one", "provider-key-one"),
                _credential("credential_two", "provider-key-two"),
            ),
            {"credential_one": (source,)},
        ),
    )
    event = _event(None).model_copy(
        update={
            "resource": SourceResource(
                connection_id="connection_github", repository="example/mailer"
            )
        }
    )
    incident, _ = await service.ingest("incident_one", event)
    assert incident.status is IncidentStatus.ACTION
    assert incident.credential_id == "credential_one"
    assert incident.candidates[0].confidence is Confidence.VERIFIED


@pytest.mark.anyio
async def test_secret_resource_exactly_correlates_through_consumer_binding() -> None:
    repository = Incidents()
    service = IncidentService(
        repository, lambda: NOW, Inventory((_credential("credential_one", "provider-key-one"),))
    )
    event = _event(None).model_copy(
        update={
            "source": "secret-manager",
            "source_event_id": "message-one",
            "kind": "credential-rotation-due",
            "resource": SourceResource(
                provider="secret-store", provider_id="secret://project-one/mailer"
            ),
        }
    )
    incident, _ = await service.ingest("incident_one", event)
    assert incident.status is IncidentStatus.ACTION
    assert incident.credential_id == "credential_one"
    assert incident.candidates[0].confidence is Confidence.VERIFIED


def _credential(credential_id: str, provider_id: str) -> ManagedCredential:
    return ManagedCredential(
        id=credential_id,
        organisation_id="org_one",
        connection_id="connection_one",
        secret_store_connection_id="secret_one",
        secret_resource=f"secret://project-one/{credential_id}",
        secret_reference=f"secret://project-one/{credential_id}",
        provider="sendgrid",
        kind="api-key",
        display_name=credential_id,
        provider_id=provider_id,
        consumer_ids=("service_one",),
        control_version="policy_one",
        created_at=NOW,
        updated_at=NOW,
    )


def _service() -> ConsumerService:
    return ConsumerService(
        id="service_one",
        organisation_id="org_one",
        application_id="application_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="runtime://project-one/mailer",
        display_name="Mailer",
        repository="example/mailer",
        identity="mailer@example.identity.example.com",
        created_at=NOW,
        updated_at=NOW,
    )


def _binding(credential_id: str) -> ConsumerBinding:
    return ConsumerBinding(
        id=f"binding_{credential_id}",
        organisation_id="org_one",
        credential_id=credential_id,
        service_id="service_one",
        environment_id="environment_one",
        runtime_connection_id="runtime_one",
        runtime_resource="runtime://project-one/mailer",
        runtime_secret_name="MAILER_API_KEY",
        secret_reference="secret://project-one/mailer/versions/1",
        current_generation_id="generation_one",
    )


def _event(provider_id: str | None) -> IngestionEvent:
    return IngestionEvent(
        id="ingestion_one",
        organisation_id="org_one",
        source="github-secret-scanning",
        source_event_id="example/mailer#1",
        kind="credential-exposure-detected",
        observed_at=NOW,
        severity=Severity.CRITICAL,
        confidence=Confidence.HIGH,
        resource=SourceResource(
            repository="example/mailer", provider="sendgrid", provider_id=provider_id
        ),
        source_reference="https://github.com/example/mailer/security/secret-scanning/1",
        received_at=NOW,
    )
