from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    Confidence,
    ConsumerBinding,
    ConsumerService,
    ControlDefinition,
    ControlPreferences,
    ControlVersion,
    Incident,
    IncidentStatus,
    ManagedCredential,
    RecoveryMode,
    Severity,
    SourceResource,
)
from core.incident import IncidentService
from core.inventory.controls import _trigger_events
from core.workflow import RunWorkflow
from ingestion.automation import IncidentAutomation
from ingestion.sources import ProviderSource, ScheduleSource
from policy import REQUIRED_CHECKS, digest
from testkit import MemoryRunRepository

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_schedule_occurrence_is_stable_across_retries() -> None:
    source = ScheduleSource()
    payload = {"credential_id": "credential_one", "due_at": NOW.isoformat()}
    first = source.normalise("org_one", "schedule_one", payload, NOW)
    retry = source.normalise("org_one", "schedule_one", payload, NOW + timedelta(minutes=1))
    assert first.id == retry.id
    assert first.source_event_id == f"schedule_one:{NOW.isoformat()}"
    assert first.resource.credential_id == "credential_one"


def test_expiry_controls_include_explicit_due_schedules() -> None:
    events = _trigger_events(
        ControlPreferences(
            automatic_triggers=frozenset({"expiry"}),
            rotate_before_expiry_seconds=604800,
            maximum_observation_seconds=1800,
        )
    )
    assert events == frozenset({"credential-expiring", "credential-rotation-due"})


def test_provider_event_preserves_exact_provider_identifier() -> None:
    event = ProviderSource().normalise(
        "org_one",
        "sendgrid",
        {
            "event_id": "provider-event-one",
            "event_type": "credential.compromised",
            "provider_id": "provider-key-one",
            "observed_at": NOW.isoformat(),
        },
        NOW,
    )
    assert event.severity is Severity.CRITICAL
    assert event.confidence is Confidence.VERIFIED
    assert event.resource.provider_id == "provider-key-one"


@pytest.mark.anyio
async def test_automatic_controls_start_exactly_correlated_run() -> None:
    credential = _credential()
    inventory = Inventory(credential)
    incidents = Incidents()
    service = IncidentService(
        incidents, lambda: NOW, inventory, RunWorkflow(MemoryRunRepository(), clock=lambda: NOW)
    )
    automation = IncidentAutomation(service, inventory, Controls(_controls()))
    event = ProviderSource().normalise(
        "org_one",
        "sendgrid",
        {
            "event_id": "provider-event-one",
            "event_type": "credential.compromised",
            "provider_id": "provider-key-one",
            "observed_at": NOW.isoformat(),
        },
        NOW,
    )
    incident, applied = await automation.ingest(event)
    retry, retry_applied = await automation.ingest(
        event.model_copy(update={"received_at": NOW + timedelta(minutes=1)})
    )
    assert applied
    assert not retry_applied
    assert incident.status is IncidentStatus.ROTATING
    assert retry.run_id == incident.run_id


@pytest.mark.anyio
async def test_due_schedule_starts_rotation_under_expiry_controls() -> None:
    credential = _credential()
    inventory = Inventory(credential)
    incidents = Incidents()
    workflow = RunWorkflow(MemoryRunRepository(), clock=lambda: NOW)
    service = IncidentService(incidents, lambda: NOW, inventory, workflow)
    controls = _controls(frozenset({"credential-rotation-due"}))
    automation = IncidentAutomation(service, inventory, Controls(controls))
    event = ScheduleSource().normalise(
        "org_one", "schedule_one", {"credential_id": credential.id, "due_at": NOW.isoformat()}, NOW
    )
    incident, applied = await automation.ingest(event)
    assert applied
    assert incident.status is IncidentStatus.ROTATING
    run = await workflow.get("org_one", incident.run_id or "")
    assert run.trigger.source == "schedule"
    assert run.trigger.reason == "The configured rotation time was reached."


@pytest.mark.anyio
async def test_high_confidence_single_candidate_requires_explicit_controls_threshold() -> None:
    credential = _credential().model_copy(update={"provider_id": None})
    inventory = Inventory(credential, repository="example/mailer")
    incidents = Incidents()
    service = IncidentService(
        incidents, lambda: NOW, inventory, RunWorkflow(MemoryRunRepository(), clock=lambda: NOW)
    )
    automation = IncidentAutomation(service, inventory, Controls(_controls()))
    event = (
        ProviderSource()
        .normalise(
            "org_one",
            "sendgrid",
            {
                "event_id": "provider-event-two",
                "event_type": "credential.compromised",
                "provider_id": "unmapped-key",
                "observed_at": NOW.isoformat(),
            },
            NOW,
        )
        .model_copy(
            update={"resource": SourceResource(repository="example/mailer", provider="sendgrid")}
        )
    )
    incident, _ = await automation.ingest(event)
    assert incident.status is IncidentStatus.ROTATING
    assert incident.credential_id == "credential_one"


class Incidents:
    def __init__(self) -> None:
        self.values: dict[str, Incident] = {}

    async def ingest(self, incident: Incident, event: object) -> tuple[Incident, bool]:
        current = self.values.get(incident.id)
        if current is not None:
            return (current, False)
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
        candidates: tuple[object, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident:
        current = self.values[incident_id]
        changed = current.model_copy(
            update={
                "candidates": candidates,
                "credential_id": credential_id,
                "status": IncidentStatus.ACTION if credential_id else IncidentStatus.CORRELATING,
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
        return ()

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
    def __init__(self, credential: ManagedCredential, repository: str | None = None) -> None:
        self.credential = credential
        self.repository = repository

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return (self.credential,)

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        if self.repository is None:
            return ()
        return (
            ConsumerService(
                id="service_one",
                organisation_id="org_one",
                application_id="application_one",
                environment_id="environment_one",
                runtime_connection_id="runtime_one",
                runtime_resource="runtime://project-one/mailer",
                display_name="Mailer",
                repository=self.repository,
                identity="mailer@example.identity.example.com",
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        if self.repository is None:
            return ()
        return (
            ConsumerBinding(
                id="binding_one",
                organisation_id="org_one",
                credential_id=self.credential.id,
                service_id="service_one",
                environment_id="environment_one",
                runtime_connection_id="runtime_one",
                runtime_resource="runtime://project-one/mailer",
                runtime_secret_name="MAILER_API_KEY",
                secret_reference="secret://project-one/mailer/versions/1",
                current_generation_id="generation_one",
            ),
        )

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        controls = _controls()
        assert credential_id == controls.credential_id
        assert version_id == controls.id
        return controls


class Controls:
    def __init__(self, controls: ControlVersion) -> None:
        self.controls = controls

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        assert credential_id == self.controls.credential_id
        assert version_id == self.controls.id
        return self.controls


def _credential() -> ManagedCredential:
    return ManagedCredential(
        id="credential_one",
        organisation_id="org_one",
        connection_id="connection_one",
        secret_store_connection_id="secret_one",
        secret_resource="secret://project-one/mailer",
        secret_reference="secret://project-one/mailer",
        provider="sendgrid",
        kind="api-key",
        display_name="Production mailer",
        provider_id="provider-key-one",
        control_version="control_one",
        created_at=NOW,
        updated_at=NOW,
    )


def _controls(
    automatic_triggers: frozenset[str] = frozenset({"credential-exposure-detected"}),
) -> ControlVersion:
    definition = ControlDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
        require_revoke_approval=True,
        automatic_triggers=automatic_triggers,
        emergency_triggers=frozenset({"credential-exposure-detected"})
        if "credential-exposure-detected" in automatic_triggers
        else frozenset(),
        minimum_automatic_confidence=Confidence.HIGH,
    )
    return ControlVersion(
        id="control_one",
        organisation_id="org_one",
        credential_id="credential_one",
        number=1,
        definition=definition,
        digest=digest(definition),
        created_by="admin_one",
        created_at=NOW,
    )
