import hashlib
from typing import Protocol

from contracts import (
    Confidence,
    ControlVersion,
    Incident,
    IncidentStatus,
    IngestionEvent,
    ManagedCredential,
)
from core.errors import ActiveRunConflictError, ResourceConflictError
from core.incident import IncidentService
from core.workflow.dispatch import RunDispatcher


class CredentialReader(Protocol):
    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...


class ControlReader(Protocol):
    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion: ...


class IncidentAutomation:
    def __init__(
        self,
        incidents: IncidentService,
        inventory: CredentialReader,
        controls: ControlReader,
        dispatcher: RunDispatcher | None = None,
    ) -> None:
        self._incidents = incidents
        self._inventory = inventory
        self._controls = controls
        self._dispatcher = dispatcher

    async def ingest(
        self,
        event: IngestionEvent,
        *,
        actor_id: str = "kinu_ingestion",
        allow_automatic: bool = True,
        reason: str | None = None,
    ) -> tuple[Incident, bool]:
        incident_id = _identifier("incident", event.id)
        incident, applied = await self._incidents.ingest(incident_id, event)
        if not allow_automatic:
            return incident, applied
        if incident.status is IncidentStatus.ROTATING:
            if self._dispatcher and incident.run_id:
                await self._dispatcher.submit(event.organisation_id, incident.run_id)
            return incident, applied
        if incident.status is IncidentStatus.CORRELATING and len(incident.candidates) == 1:
            candidate = incident.candidates[0]
            credential = await self._credential(event.organisation_id, candidate.credential_id)
            controls = await self._controls.get_control_version(
                event.organisation_id, credential.id, credential.control_version
            )
            if self._automatic(controls, event, candidate.confidence):
                incident = await self._incidents.confirm(
                    event.organisation_id,
                    incident.id,
                    incident.revision,
                    credential.id,
                    actor_id,
                )
        if incident.status is not IncidentStatus.ACTION or incident.credential_id is None:
            return incident, applied
        credential = await self._credential(event.organisation_id, incident.credential_id)
        controls = await self._controls.get_control_version(
            event.organisation_id, credential.id, credential.control_version
        )
        if not self._automatic(controls, event, Confidence.VERIFIED):
            return incident, applied
        urgency = "emergency" if event.kind in controls.definition.emergency_triggers else "routine"
        command_id = _identifier("command", event.id)
        try:
            if self._dispatcher:
                await self._dispatcher.require_scope(event.organisation_id, credential.id)
            linked, _, _ = await self._incidents.start_rotation(
                event.organisation_id,
                incident.id,
                command_id,
                actor_id,
                controls.id,
                reason or _rotation_reason(event.kind),
                urgency,
                event.observed_at,
            )
        except (ActiveRunConflictError, ResourceConflictError):
            current = await self._incidents.get(event.organisation_id, incident.id)
            return current, applied
        if self._dispatcher and linked.run_id:
            await self._dispatcher.submit(event.organisation_id, linked.run_id)
        return linked, applied

    def _automatic(
        self,
        controls: ControlVersion,
        event: IngestionEvent,
        confidence: Confidence,
    ) -> bool:
        levels = {
            Confidence.LOW: 0,
            Confidence.MEDIUM: 1,
            Confidence.HIGH: 2,
            Confidence.VERIFIED: 3,
        }
        return (
            event.kind in controls.definition.automatic_triggers
            and levels[confidence] >= levels[controls.definition.minimum_automatic_confidence]
        )

    async def _credential(self, organisation_id: str, credential_id: str) -> ManagedCredential:
        matches = tuple(
            credential
            for credential in await self._inventory.credentials(organisation_id)
            if credential.id == credential_id
        )
        if len(matches) != 1:
            raise ResourceConflictError("correlated credential disappeared from inventory")
        return matches[0]


def _identifier(prefix: str, event_id: str) -> str:
    return f"{prefix}_{hashlib.sha256(event_id.encode()).hexdigest()[:40]}"


def _rotation_reason(kind: str) -> str:
    reasons = {
        "credential-rotation-due": "The configured rotation time was reached.",
        "credential-expiring": "The credential reached its configured rotation window.",
        "credential-exposure-detected": "A verified credential exposure was detected.",
        "credential-provider-drift": "The provider credential no longer matches inventory.",
        "credential-inventory-drift": "The credential inventory mapping changed.",
        "credential-runtime-drift": "A runtime credential binding changed.",
    }
    return reasons.get(kind, "A configured credential control started the rotation.")
