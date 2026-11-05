import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    Confidence,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    CorrelationCandidate,
    CreateRunCommand,
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
    NotificationKind,
    RotationRun,
    Severity,
    Trigger,
)

from core.audit.writer import AuditWriter
from core.errors import ResourceConflictError
from core.storage.repository import MutationResult


class IncidentRepository(Protocol):
    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]: ...

    async def get(self, organisation_id: str, incident_id: str) -> Incident: ...

    async def list_incidents(
        self,
        organisation_id: str,
        limit: int,
        *,
        statuses: frozenset[IncidentStatus] | None = None,
        before: str | None = None,
    ) -> tuple[Incident, ...]: ...

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[CorrelationCandidate, ...],
        credential_id: str | None,
        updated_at: datetime,
    ) -> Incident: ...

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident: ...

    async def advance_run(
        self,
        organisation_id: str,
        run_id: str,
        status: IncidentStatus,
        updated_at: datetime,
    ) -> tuple[Incident, ...]: ...

    async def dismiss(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        reason: str,
        updated_at: datetime,
    ) -> Incident: ...


class IncidentInventory(Protocol):
    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]: ...

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]: ...

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion: ...


class IncidentWorkflow(Protocol):
    async def create(self, command: CreateRunCommand) -> MutationResult: ...


class IncidentNotifier(Protocol):
    async def emit(
        self,
        event_id: str,
        organisation_id: str,
        kind: NotificationKind,
        severity: Severity,
        title: str,
        body: str,
        link_path: str,
        resource_id: str,
        run_id: str | None = None,
        incident_id: str | None = None,
        approval_id: str | None = None,
    ) -> tuple[object, bool]: ...


class IncidentService:
    def __init__(
        self,
        repository: IncidentRepository,
        clock: Callable[[], datetime],
        inventory: IncidentInventory | None = None,
        workflow: IncidentWorkflow | None = None,
        notifier: IncidentNotifier | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._inventory = inventory
        self._workflow = workflow
        self._notifier = notifier
        self._audit = audit

    async def ingest(self, incident_id: str, event: IngestionEvent) -> tuple[Incident, bool]:
        incident = Incident(
            id=incident_id,
            organisation_id=event.organisation_id,
            event_id=event.id,
            source=event.source,
            kind=event.kind,
            description=event.description,
            source_event_id=event.source_event_id,
            severity=event.severity,
            confidence=event.confidence,
            status=IncidentStatus.NEW,
            resource=event.resource,
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        stored, applied = await self._repository.ingest(incident, event)
        if not applied or self._inventory is None:
            await self._notify(stored, event)
            return stored, applied
        candidates = await self._candidates(event)
        correlated = await self.correlate(
            event.organisation_id, stored.id, stored.revision, candidates
        )
        await self._notify(correlated, event)
        if self._audit is not None:
            await self._audit.append(
                _audit_id(correlated.id, str(correlated.revision)),
                correlated.organisation_id,
                "incident.ingested",
                "ingestion_one",
                f"incidents/{correlated.id}",
                {
                    "source": correlated.source,
                    "severity": correlated.severity.value,
                    "confidence": correlated.confidence.value,
                    "status": correlated.status.value,
                    "credential_id": correlated.credential_id,
                },
                occurred_at=correlated.updated_at,
            )
        return correlated, applied

    async def get(self, organisation_id: str, incident_id: str) -> Incident:
        return await self._repository.get(organisation_id, incident_id)

    async def list_incidents(
        self,
        organisation_id: str,
        statuses: frozenset[IncidentStatus] | None = None,
        limit: int = 100,
        before: str | None = None,
    ) -> tuple[Incident, ...]:
        return await self._repository.list_incidents(
            organisation_id,
            min(max(limit, 1), 500),
            statuses=statuses,
            before=before,
        )

    async def correlate(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        candidates: tuple[CorrelationCandidate, ...],
    ) -> Incident:
        verified = tuple(
            candidate for candidate in candidates if candidate.confidence is Confidence.VERIFIED
        )
        if len(verified) > 1:
            raise ResourceConflictError("an incident cannot have multiple verified credentials")
        credential_id = verified[0].credential_id if verified else None
        return await self._repository.correlate(
            organisation_id,
            incident_id,
            expected_revision,
            candidates,
            credential_id,
            self._clock(),
        )

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
    ) -> Incident:
        return await self._repository.link_run(
            organisation_id,
            incident_id,
            expected_revision,
            credential_id,
            run_id,
            self._clock(),
        )

    async def confirm(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        actor_id: str = "ingestion_one",
    ) -> Incident:
        incident = await self._repository.get(organisation_id, incident_id)
        if incident.revision != expected_revision:
            raise ResourceConflictError(
                f"incident expected revision {expected_revision}, found {incident.revision}"
            )
        if incident.status not in {IncidentStatus.CORRELATING, IncidentStatus.ACTION}:
            raise ResourceConflictError("incident cannot be confirmed in its current state")
        if credential_id not in {item.credential_id for item in incident.candidates}:
            raise ResourceConflictError("credential is not a correlated incident candidate")
        candidates = tuple(
            item.model_copy(
                update={
                    "confidence": (
                        Confidence.VERIFIED
                        if item.credential_id == credential_id
                        else Confidence.HIGH
                        if item.confidence is Confidence.VERIFIED
                        else item.confidence
                    )
                }
            )
            for item in incident.candidates
        )
        confirmed = await self.correlate(
            organisation_id, incident_id, expected_revision, candidates
        )
        if self._audit is not None:
            await self._audit.append(
                _audit_id(confirmed.id, str(confirmed.revision)),
                confirmed.organisation_id,
                "incident.confirmed",
                actor_id,
                f"incidents/{confirmed.id}",
                {"credential_id": credential_id, "revision": confirmed.revision},
                occurred_at=confirmed.updated_at,
            )
        return confirmed

    async def start_rotation(
        self,
        organisation_id: str,
        incident_id: str,
        command_id: str,
        actor_id: str,
        control_version: str,
        reason: str,
        urgency: str,
        received_at: datetime,
    ) -> tuple[Incident, RotationRun, bool]:
        if self._workflow is None:
            raise RuntimeError("incident rotation workflow is not configured")
        incident = await self._repository.get(organisation_id, incident_id)
        if incident.credential_id is None or incident.status not in {
            IncidentStatus.ACTION,
            IncidentStatus.ROTATING,
        }:
            raise ResourceConflictError("incident has no verified credential for rotation")
        run_id = f"run_{hashlib.sha256(command_id.encode()).hexdigest()[:40]}"
        if incident.run_id is not None and incident.run_id != run_id:
            raise ResourceConflictError("incident is already linked to another rotation")
        result = await self._workflow.create(
            CreateRunCommand(
                id=command_id,
                organisation_id=organisation_id,
                credential_id=incident.credential_id,
                control_version=control_version,
                run_id=run_id,
                trigger=Trigger(
                    source=incident.source,
                    kind=incident.kind,
                    event_id=incident.event_id,
                    actor_id=actor_id,
                    reason=reason,
                    urgency=urgency,
                    received_at=received_at,
                ),
            )
        )
        if incident.status is IncidentStatus.ROTATING:
            return incident, result.run, False
        linked = await self.link_run(
            organisation_id,
            incident.id,
            incident.revision,
            incident.credential_id,
            result.run.id,
        )
        if self._audit is not None:
            await self._audit.append(
                _audit_id(linked.id, str(linked.revision)),
                linked.organisation_id,
                "incident.rotation-started",
                actor_id,
                f"incidents/{linked.id}",
                {"credential_id": linked.credential_id, "run_id": result.run.id},
                run_id=result.run.id,
                occurred_at=linked.updated_at,
            )
        return linked, result.run, result.applied

    async def advance_run(
        self,
        organisation_id: str,
        run_id: str,
        status: IncidentStatus,
    ) -> tuple[Incident, ...]:
        if status not in {IncidentStatus.CONTAINED, IncidentStatus.RESOLVED}:
            raise ValueError("run-linked incidents only advance to contained or resolved")
        return await self._repository.advance_run(organisation_id, run_id, status, self._clock())

    async def dismiss(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        reason: str,
        actor_id: str,
    ) -> Incident:
        incident = await self._repository.get(organisation_id, incident_id)
        if incident.revision != expected_revision:
            raise ResourceConflictError(
                f"incident expected revision {expected_revision}, found {incident.revision}"
            )
        if incident.status is IncidentStatus.DISMISSED:
            return incident
        if incident.status not in {
            IncidentStatus.NEW,
            IncidentStatus.CORRELATING,
            IncidentStatus.ACTION,
        }:
            raise ResourceConflictError("incident cannot be dismissed after rotation starts")
        dismissed = await self._repository.dismiss(
            organisation_id,
            incident_id,
            expected_revision,
            reason,
            self._clock(),
        )
        if self._audit is not None:
            await self._audit.append(
                _audit_id(dismissed.id, str(dismissed.revision)),
                dismissed.organisation_id,
                "incident.dismissed",
                actor_id,
                f"incidents/{dismissed.id}",
                {"reason": reason, "revision": dismissed.revision},
                run_id=dismissed.run_id,
                occurred_at=dismissed.updated_at,
            )
        return dismissed

    async def _candidates(self, event: IngestionEvent) -> tuple[CorrelationCandidate, ...]:
        if self._inventory is None:
            return ()
        credentials = await self._inventory.credentials(event.organisation_id)
        services = {item.id: item for item in await self._inventory.services(event.organisation_id)}
        bindings = await self._inventory.bindings(event.organisation_id)
        by_credential: dict[str, list[ConsumerBinding]] = {}
        for binding in bindings:
            by_credential.setdefault(binding.credential_id, []).append(binding)
        scored: list[tuple[int, bool, ManagedCredential, tuple[str, ...]]] = []
        for credential in credentials:
            score = 0
            exact = False
            reason_values: list[str] = []
            if event.resource.connection_id is not None and event.resource.repository is not None:
                controls = await self._inventory.get_control_version(
                    event.organisation_id, credential.id, credential.control_version
                )
                if any(
                    source.connection_id == event.resource.connection_id
                    and source.resource == event.resource.repository
                    for source in controls.definition.exposure_sources
                ):
                    score += 200
                    exact = True
                    reason_values.append("configured exposure source matches exactly")
            if event.resource.credential_id == credential.id:
                score += 200
                exact = True
                reason_values.append("managed credential identifier matches exactly")
            if event.resource.provider == credential.provider:
                score += 20
                reason_values.append("provider matches inventory")
            if (
                event.resource.provider_id is not None
                and event.resource.provider_id == credential.provider_id
            ):
                score += 100
                exact = True
                reason_values.append("provider identifier matches exactly")
            consumers = [
                services[item.service_id]
                for item in by_credential.get(credential.id, [])
                if item.service_id in services
            ]
            if event.resource.provider_id is not None and any(
                _secret_resource(item.secret_reference) == event.resource.provider_id
                for item in by_credential.get(credential.id, [])
            ):
                score += 100
                exact = True
                reason_values.append("secret resource matches consumer binding exactly")
            if event.resource.repository and any(
                item.repository == event.resource.repository for item in consumers
            ):
                score += 50
                reason_values.append("repository consumes credential")
            resource_terms = tuple(
                value
                for value in (event.resource.project, event.resource.service)
                if value is not None
            )
            if resource_terms and any(
                any(term in item.runtime_resource for term in resource_terms) for item in consumers
            ):
                score += 30
                reason_values.append("runtime resource matches incident")
            if score:
                scored.append((score, exact, credential, tuple(reason_values)))
        exact_ids = {item[2].id for item in scored if item[1]}
        values = []
        for score, exact, credential, candidate_reasons in sorted(
            scored, key=lambda item: -item[0]
        ):
            confidence = (
                Confidence.VERIFIED
                if exact and len(exact_ids) == 1
                else Confidence.HIGH
                if score >= 50
                else Confidence.MEDIUM
            )
            values.append(
                CorrelationCandidate(
                    credential_id=credential.id,
                    confidence=confidence,
                    reasons=candidate_reasons,
                    consumer_ids=credential.consumer_ids,
                )
            )
        return tuple(values)

    async def _notify(self, incident: Incident, event: IngestionEvent) -> None:
        if self._notifier is None:
            return
        if event.source == "test-email":
            kind = NotificationKind.INCIDENT
            title = "Test incident received"
            body = (
                "A simulated incident was received. "
                "Its credential controls determine the next action."
            )
        elif event.source == "schedule":
            kind = NotificationKind.ROTATION_DUE
            title = "Scheduled credential rotation is due"
            body = f"Kinu incident {incident.id} is ready for its scheduled rotation."
        elif incident.status is IncidentStatus.CORRELATING:
            kind = NotificationKind.INCIDENT_CONFIRMATION
            title = "Credential incident needs confirmation"
            body = f"Kinu incident {incident.id} has ambiguous credential matches."
        elif incident.severity in {Severity.CRITICAL, Severity.HIGH} and incident.confidence in {
            Confidence.HIGH,
            Confidence.VERIFIED,
        }:
            kind = NotificationKind.INCIDENT
            title = "Credential incident detected"
            body = f"Kinu incident {incident.id} requires review and containment."
        else:
            return
        await self._notifier.emit(
            event.id,
            incident.organisation_id,
            kind,
            incident.severity,
            title,
            body,
            f"/organisations/{incident.organisation_id}/incidents/{incident.id}",
            incident.id,
            run_id=incident.run_id,
            incident_id=incident.id,
        )


def _secret_resource(reference: str) -> str:
    marker = "/versions/"
    return reference.partition(marker)[0] if marker in reference else reference


def _audit_id(*values: str) -> str:
    checksum = hashlib.sha256("\0".join(values).encode()).hexdigest()
    return f"audit_{checksum[:40]}"
