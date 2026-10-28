import itertools
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from contracts import (
    Application,
    Approval,
    ApprovalDecision,
    AuditEvent,
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
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
    Playbook,
    PlaybookDraft,
    PlaybookVersion,
    ProbeVersion,
    Severity,
    SourceResource,
)
from contracts.incident import Confidence
from core.approval import ApprovalService
from core.audit import AuditWriter
from core.auth import AccessControl, AuthenticatedIdentity, PrincipalGrant, Role
from core.errors import ResourceNotFoundError
from core.incident import IncidentService
from core.inventory import InventoryService
from core.overview import OverviewService
from core.playbook import PlaybookService
from core.workflow import RunWorkflow
from testkit import MemoryRunRepository, make_control_version, make_http_provider_api
from tests.browser import HEADERS, make_browser

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
IDENTITY = AuthenticatedIdentity(
    subject="107777777777777777777",
    issuer="https://identity.example.com",
    email="workflow@example.identity.example.com",
)


class AccessRepository:
    def __init__(self, role: Role) -> None:
        self._role = role

    async def get(
        self, organisation_id: str, identity: AuthenticatedIdentity
    ) -> PrincipalGrant | None:
        if organisation_id != "org_one" or identity != IDENTITY:
            return None
        return PrincipalGrant(subject=identity.subject, roles=frozenset({self._role}))


class IncidentRepository:
    def __init__(self, credential_id: str | None = None) -> None:
        self.incidents: tuple[Incident, ...] = (
            _incident("incident_one", IncidentStatus.NEW, NOW, credential_id),
            _incident("incident_two", IncidentStatus.ACTION, NOW + timedelta(minutes=5)),
        )

    async def list_incidents(self, organisation_id: str, limit: int) -> tuple[Incident, ...]:
        return self.incidents[:limit]

    async def count_incidents(
        self, organisation_id: str, statuses: frozenset[IncidentStatus]
    ) -> int:
        return sum(1 for incident in self.incidents if incident.status in statuses)

    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]:
        raise AssertionError("not used")

    async def get(self, organisation_id: str, incident_id: str) -> Incident:
        return next(item for item in self.incidents if item.id == incident_id)

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[object, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident:
        raise AssertionError("not used")

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident:
        raise AssertionError("not used")

    async def advance_run(
        self, organisation_id: str, run_id: str, status: IncidentStatus, updated_at: datetime
    ) -> tuple[Incident, ...]:
        raise AssertionError("not used")

    async def dismiss(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        reason: str,
        updated_at: datetime,
    ) -> Incident:
        current = await self.get(organisation_id, incident_id)
        changed = current.model_copy(
            update={
                "status": IncidentStatus.DISMISSED,
                "dismissal_reason": reason,
                "updated_at": updated_at,
                "revision": current.revision + 1,
            }
        )
        self.incidents = tuple(
            changed if item.id == incident_id else item for item in self.incidents
        )
        return changed


class ApprovalRepository:
    def __init__(self, pending_run_id: str = "run_one") -> None:
        self.approvals: tuple[Approval, ...] = (
            _approval("approval_one", ApprovalDecision.PENDING, NOW, run_id=pending_run_id),
            _approval(
                "approval_two", ApprovalDecision.APPROVED, NOW + timedelta(minutes=5), decided=True
            ),
        )

    async def list_approvals(self, organisation_id: str, limit: int) -> tuple[Approval, ...]:
        return self.approvals[:limit]

    async def count_approvals(
        self,
        organisation_id: str,
        decisions: frozenset[ApprovalDecision],
        active_at: datetime | None = None,
    ) -> int:
        return sum(
            1
            for approval in self.approvals
            if approval.decision in decisions
            and (active_at is None or approval.expires_at > active_at)
        )

    async def create(self, approval: Approval, action: object) -> Approval:
        raise AssertionError("not used")

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
        decided_at: datetime,
    ) -> Approval:
        current = next(item for item in self.approvals if item.id == approval_id)
        changed = current.model_copy(
            update={
                "decision": decision,
                "approver_id": actor_id,
                "decided_at": decided_at,
                "revision": current.revision + 1,
            }
        )
        self.approvals = tuple(
            changed if item.id == approval_id else item for item in self.approvals
        )
        return changed

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability_hash: str,
        action_digest: str,
        plan_hash: str,
        evidence_hash: str,
        consumed_at: datetime,
    ) -> Approval:
        raise AssertionError("not used")


class PlaybookRepository:
    def __init__(self) -> None:
        self.playbooks: tuple[Playbook, ...] = (
            _playbook("playbook_one", NOW),
            _playbook("playbook_two", NOW + timedelta(minutes=5)),
        )

    async def list_playbooks(self, organisation_id: str, limit: int) -> tuple[Playbook, ...]:
        return self.playbooks[:limit]

    async def get(self, organisation_id: str, playbook_id: str) -> Playbook:
        return next(playbook for playbook in self.playbooks if playbook.id == playbook_id)

    async def replace(self, value: Playbook, expected_revision: int) -> Playbook:
        self.playbooks = tuple(
            value if playbook.id == value.id else playbook for playbook in self.playbooks
        )
        return value

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
        raise AssertionError("not used")

    async def get_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        raise AssertionError("not used")

    async def publish(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
        actor_id: str,
        published_at: datetime,
    ) -> PlaybookVersion:
        raise AssertionError("not used")


class AuditRepository:
    def __init__(self) -> None:
        self.events: tuple[AuditEvent, ...] = (
            _audit_event("audit_one", 0, "run.created", "0" * 64, NOW, run_id="run_one"),
            _audit_event(
                "audit_two",
                1,
                "approval.approved",
                "a" * 64,
                NOW + timedelta(minutes=1),
                run_id="run_one",
            ),
            _audit_event("audit_three", 2, "run.completed", "b" * 64, NOW + timedelta(minutes=2)),
        )

    async def list_events(self, organisation_id: str, limit: int) -> tuple[AuditEvent, ...]:
        return self.events[:limit]

    async def append(
        self,
        event_id: str,
        organisation_id: str,
        kind: str,
        actor_id: str,
        resource: str,
        run_id: str | None,
        payload: dict[str, str | int | float | bool | None],
        evidence_ids: tuple[str, ...],
        occurred_at: datetime,
        region: str,
    ) -> AuditEvent:
        event = AuditEvent(
            id=event_id,
            organisation_id=organisation_id,
            sequence=len(self.events),
            kind=kind,
            actor_id=actor_id,
            resource=resource,
            run_id=run_id,
            payload=payload,
            evidence_ids=evidence_ids,
            previous_hash=self.events[-1].event_hash if self.events else "0" * 64,
            event_hash="f" * 64,
            occurred_at=occurred_at,
            region=region,
        )
        self.events = (*self.events, event)
        return event


class InventoryRepository:
    def __init__(self) -> None:
        self.stored_connections: tuple[Connection, ...] = (
            Connection(
                id="connection_one",
                organisation_id="org_one",
                platform="sendgrid",
                display_name="SendGrid Admin",
                roles=frozenset({ConnectionRole.PROVIDER}),
                interface=ConnectionInterface.API,
                authorization=ConnectionAuthorization.API_KEY,
                authorization_reference="secret://org-one/sendgrid-admin/versions/1",
                capabilities=frozenset({"create", "revoke"}),
                allowed_resources=("sendgrid:*",),
                http=make_http_provider_api(),
                status=ConnectionStatus.READY,
                region="us-east1",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_applications: tuple[Application, ...] = (
            Application(
                id="application_one",
                organisation_id="org_one",
                display_name="Acme Store",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_environments: tuple[Environment, ...] = (
            Environment(
                id="environment_one",
                organisation_id="org_one",
                application_id="application_one",
                display_name="Production",
                production=True,
                region="us-east1",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_credentials: tuple[ManagedCredential, ...] = (
            ManagedCredential(
                id="credential_one",
                organisation_id="org_one",
                connection_id="connection_one",
                secret_store_connection_id="connection_secret",
                secret_resource="secret://org-one/sendgrid",
                secret_reference="secret://org-one/sendgrid",
                provider="sendgrid",
                kind="api-key",
                display_name="production-password-emailer",
                control_version="control_version_one",
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        self.stored_controls: tuple[ControlVersion, ...] = (
            make_control_version(
                version_id="control_version_one", credential_id="credential_one", now=NOW
            ),
        )

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return self.stored_connections

    async def applications(self, organisation_id: str) -> tuple[Application, ...]:
        return self.stored_applications

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return self.stored_environments

    async def add_connection(self, value: Connection) -> Connection:
        raise AssertionError("not used")

    async def add_application(self, value: Application) -> Application:
        raise AssertionError("not used")

    async def add_environment(self, value: Environment) -> Environment:
        raise AssertionError("not used")

    async def add_service(self, value: ConsumerService) -> ConsumerService:
        raise AssertionError("not used")

    async def add_application_setup(
        self, application: Application, environment: Environment, service: ConsumerService
    ) -> tuple[Application, Environment, ConsumerService]:
        raise AssertionError("not used")

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return next(item for item in self.stored_applications if item.id == resource_id)

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return next(item for item in self.stored_environments if item.id == resource_id)

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        raise AssertionError("not used")

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return next(item for item in self.stored_connections if item.id == resource_id)

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential:
        return next(item for item in self.stored_credentials if item.id == resource_id)

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        return next(
            item
            for item in self.stored_controls
            if item.credential_id == credential_id and item.id == version_id
        )

    async def get_playbook_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        raise ResourceNotFoundError("playbook version not found")

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection:
        self.stored_connections = tuple(
            value if item.id == value.id else item for item in self.stored_connections
        )
        return value

    async def replace_application(self, value: Application, expected_revision: int) -> Application:
        self.stored_applications = tuple(
            value if item.id == value.id else item for item in self.stored_applications
        )
        return value

    async def replace_environment(self, value: Environment, expected_revision: int) -> Environment:
        self.stored_environments = tuple(
            value if item.id == value.id else item for item in self.stored_environments
        )
        return value

    async def replace_service(
        self, value: ConsumerService, expected_revision: int
    ) -> ConsumerService:
        return value

    async def replace_credential(
        self, value: ManagedCredential, expected_revision: int
    ) -> ManagedCredential:
        self.stored_credentials = tuple(
            value if item.id == value.id else item for item in self.stored_credentials
        )
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
        raise AssertionError("not used")

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        raise AssertionError("not used")

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
        service_setup: tuple[Application, Environment, ConsumerService] | None = None,
    ) -> ManagedCredential:
        raise AssertionError("not used")

    async def replace_controls(
        self, credential: ManagedCredential, expected_revision: int, controls: ControlVersion
    ) -> tuple[ManagedCredential, ControlVersion]:
        self.stored_credentials = tuple(
            credential if item.id == credential.id else item for item in self.stored_credentials
        )
        self.stored_controls = (*self.stored_controls, controls)
        return (credential, controls)

    async def archive_inventory(
        self,
        resources: tuple[
            Connection | Application | Environment | ConsumerService | ManagedCredential, ...
        ],
        bindings: tuple[ConsumerBinding, ...],
    ) -> None:
        for resource in resources:
            if isinstance(resource, Connection):
                self.stored_connections = tuple(
                    resource if item.id == resource.id else item for item in self.stored_connections
                )
            elif isinstance(resource, Application):
                self.stored_applications = tuple(
                    resource if item.id == resource.id else item
                    for item in self.stored_applications
                )
            elif isinstance(resource, Environment):
                self.stored_environments = tuple(
                    resource if item.id == resource.id else item
                    for item in self.stored_environments
                )
            elif isinstance(resource, ManagedCredential):
                self.stored_credentials = tuple(
                    resource if item.id == resource.id else item for item in self.stored_credentials
                )

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return self.stored_credentials

    async def count_credentials(self, organisation_id: str) -> int:
        return sum(
            credential.archived_at is None for credential in await self.credentials(organisation_id)
        )

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return ()

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return ()


def headers(key: str = "request-one") -> dict[str, str]:
    return {**HEADERS, "Idempotency-Key": key}


def _incident(
    incident_id: str,
    incident_status: IncidentStatus,
    created_at: datetime,
    credential_id: str | None = None,
) -> Incident:
    return Incident(
        id=incident_id,
        organisation_id="org_one",
        event_id=f"event_{incident_id}",
        source="github_secret_scanning",
        source_event_id=f"alert-{incident_id}",
        severity=Severity.CRITICAL,
        confidence=Confidence.HIGH,
        status=incident_status,
        resource=SourceResource(repository="acme/store-api", provider="sendgrid"),
        credential_id=credential_id,
        created_at=created_at,
        updated_at=created_at,
    )


def _approval(
    approval_id: str,
    decision: ApprovalDecision,
    created_at: datetime,
    decided: bool = False,
    run_id: str = "run_one",
) -> Approval:
    return Approval(
        id=approval_id,
        organisation_id="org_one",
        run_id=run_id,
        action_id="action_one",
        action_digest="a" * 64,
        plan_hash="b" * 64,
        evidence_hash="c" * 64,
        generation_id="gen_one",
        requested_by="service_one",
        capability_hash="d" * 64,
        decision=decision,
        approver_id="approver_one" if decided else None,
        expires_at=created_at + timedelta(hours=1),
        created_at=created_at,
        decided_at=created_at if decided else None,
    )


def _playbook(playbook_id: str, created_at: datetime) -> Playbook:
    return Playbook(
        id=playbook_id,
        organisation_id="org_one",
        name=f"SendGrid Mail API Key Rotation {playbook_id}",
        platform="sendgrid",
        created_at=created_at,
        updated_at=created_at,
    )


def _audit_event(
    event_id: str,
    sequence: int,
    kind: str,
    previous_hash: str,
    occurred_at: datetime,
    run_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        organisation_id="org_one",
        sequence=sequence,
        kind=kind,
        actor_id="service_one",
        resource="runs/run_one",
        run_id=run_id,
        previous_hash=previous_hash,
        event_hash="e" * 64,
        occurred_at=occurred_at,
        region="us-east1",
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_overview_does_not_count_expired_pending_approvals() -> None:
    summary = await OverviewService(
        InventoryRepository(),
        MemoryRunRepository(),
        IncidentRepository(),
        ApprovalRepository(),
        lambda: NOW + timedelta(hours=2),
    ).summary("org_one")
    assert summary.pending_approvals == 0


@pytest.mark.anyio
async def test_setup_without_browser_runtime_reports_required_setup() -> None:
    sequence = itertools.count(1)
    repository = MemoryRunRepository()
    workflow = RunWorkflow(
        repository, clock=lambda: NOW, id_factory=lambda prefix: f"{prefix}_{next(sequence)}"
    )
    inventory = InventoryRepository()
    services = ApiServices(
        workflow=workflow,
        access=AccessControl(AccessRepository(Role.ADMINISTRATOR)),
        inventory=InventoryService(inventory),
        playbooks=PlaybookService(PlaybookRepository(), clock=lambda: NOW),
        approvals=ApprovalService(ApprovalRepository(), clock=lambda: NOW),
        incidents=IncidentService(IncidentRepository(), clock=lambda: NOW),
        audit=AuditWriter(AuditRepository(), "us-east1", lambda: NOW),
        overview=OverviewService(
            inventory, repository, IncidentRepository(), ApprovalRepository(), lambda: NOW
        ),
    )
    transport = httpx.ASGITransport(
        app=create_app(services, browser=make_browser(IDENTITY)), raise_app_exceptions=False
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        begun = await client.post(
            "/v1/organisations/org_one/inventory/connections/connection_browser/setup",
            headers=headers(),
            json={"extra_domains": []},
        )
    assert begun.status_code == 409
    assert begun.json()["code"] == "setup-required"


@pytest.mark.anyio
async def test_unconfigured_api_reports_readiness_without_accepting_identity() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        readiness = await client.get("/health/ready")
        assert readiness.status_code == 503
        assert readiness.json() == {"status": "unconfigured"}
        assert (await client.get("/v1/session")).status_code == 401
        response = await client.get("/v1/session", headers={"Cookie": "kinu.session=arbitrary"})
        assert response.status_code == 503
        assert response.json()["code"] == "integration-not-configured"


@pytest.mark.anyio
async def test_incident_rotation_dispatches_and_duplicate_request_reuses_the_run() -> None:
    class Incidents(IncidentRepository):
        async def link_run(
            self,
            organisation_id: str,
            incident_id: str,
            expected_revision: int,
            credential_id: str,
            run_id: str,
            updated_at: datetime,
        ) -> Incident:
            incident = await self.get(organisation_id, incident_id)
            changed = incident.model_copy(
                update={
                    "status": IncidentStatus.ROTATING,
                    "run_id": run_id,
                    "revision": incident.revision + 1,
                }
            )
            self.incidents = (changed,)
            return changed

    class Dispatcher:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def require_scope(self, organisation_id: str, credential_id: str) -> None:
            assert (organisation_id, credential_id) == ("org_one", "credential_one")

        async def submit(self, organisation_id: str, run_id: str) -> None:
            self.sent.append(run_id)

    inventory, repository, incidents = (
        InventoryRepository(),
        MemoryRunRepository(),
        Incidents("credential_one"),
    )
    incidents.incidents = (
        incidents.incidents[0].model_copy(update={"status": IncidentStatus.ACTION}),
    )
    controls = inventory.stored_controls[0]
    inventory.stored_controls = (
        controls.model_copy(
            update={
                "definition": controls.definition.model_copy(
                    update={"require_revoke_approval": False}
                )
            }
        ),
    )
    dispatcher = Dispatcher()
    workflow = RunWorkflow(repository, clock=lambda: NOW)
    configured = ApiServices(
        access=AccessControl(AccessRepository(Role.ADMINISTRATOR)),
        inventory=InventoryService(inventory),
        workflow=workflow,
        incidents=IncidentService(incidents, lambda: NOW, inventory, workflow),
        dispatcher=dispatcher,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(configured, browser=make_browser(IDENTITY))),
        base_url="http://test",
    ) as client:
        for expected in (201, 200):
            response = await client.post(
                "/v1/organisations/org_one/incidents/incident_one/rotate",
                headers=headers(),
                json={
                    "control_version": controls.id,
                    "reason": "Confirmed exposure",
                    "urgency": "urgent",
                    "received_at": NOW.isoformat(),
                },
            )
            assert response.status_code == expected, response.text
    assert len(dispatcher.sent) == 2 and dispatcher.sent[0] == dispatcher.sent[1]


@pytest.mark.anyio
async def test_database_outage_fails_readiness_without_exposing_error_details() -> None:
    from types import SimpleNamespace

    async def unavailable(command: str) -> None:
        raise RuntimeError("private database connection details")

    app = create_app(
        ApiServices(access=AccessControl(AccessRepository(Role.ADMINISTRATOR))),
        browser=make_browser(IDENTITY),
    )
    app.state.database = SimpleNamespace(documents=SimpleNamespace(command=unavailable))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/health/ready")
        assert response.status_code == 503 and response.json() == {"status": "unavailable"}
        assert "private" not in response.text
        assert (await http.get("/health/live")).status_code == 200
