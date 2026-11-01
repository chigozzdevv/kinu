from datetime import datetime
from typing import Literal

from contracts import (
    CredentialGeneration,
    GenerationState,
    Incident,
    IncidentStatus,
    ManagedCredential,
    RecoveryMode,
    RotationRun,
    RunStatus,
    Stage,
)
from pymongo.asynchronous.client_session import AsyncClientSession

from core.errors import ResourceNotFoundError
from core.storage.database import Database
from core.storage.records import RecordTransaction

_ROTATION_INCIDENTS = {
    "credential-exposure-detected",
    "credential-expiring",
    "credential-rotation-due",
}


async def resolve_observed_incidents(
    rows: RecordTransaction,
    credential: ManagedCredential,
    observed_at: datetime,
    *,
    kind: Literal["credential-metadata-stale", "credential-runtime-drift"],
) -> None:
    for incident in await rows.match(
        Incident, credential.organisation_id, {"credential_id": credential.id}
    ):
        if (
            incident.kind == kind
            and incident.status is IncidentStatus.ACTION
            and incident.resource.generation_id == credential.active_generation_id
            and incident.created_at <= observed_at
        ):
            await rows.replace(
                Incident.model_validate(
                    incident.model_dump()
                    | {
                        "status": IncidentStatus.RESOLVED,
                        "resolution_observed_at": observed_at,
                        "updated_at": max(incident.updated_at, observed_at),
                        "revision": incident.revision + 1,
                    }
                ),
                incident.revision,
            )


async def sync_incidents(
    database: Database, run: RotationRun, session: AsyncClientSession
) -> tuple[Incident, ...]:
    if run.status is RunStatus.COMPLETED or (
        run.status is RunStatus.COMPENSATED and run.recovery_mode is RecoveryMode.ROLLFORWARD
    ):
        status = IncidentStatus.RESOLVED
    elif run.status in {
        RunStatus.FAILED,
        RunStatus.CLEANUP,
        RunStatus.CANCELLED,
        RunStatus.COMPENSATED,
    }:
        status = IncidentStatus.ACTION
    elif run.stage is Stage.COMPLETE:
        status = IncidentStatus.CONTAINED
    else:
        status = IncidentStatus.ROTATING
    rows = RecordTransaction(database, session)
    linked = await rows.match(Incident, run.organisation_id, {"run_id": run.id})
    incidents = {item.id: item for item in linked}
    if status is IncidentStatus.RESOLVED and await _retired(rows, run):
        for incident in await rows.match(
            Incident, run.organisation_id, {"credential_id": run.credential_id}
        ):
            if (
                incident.status is IncidentStatus.ACTION
                and incident.kind in _ROTATION_INCIDENTS
                and incident.resource.generation_id == run.current_generation_id
                and incident.created_at <= run.updated_at
            ):
                incidents[incident.id] = incident
    changed = []
    for incident in incidents.values():
        if incident.status in {IncidentStatus.RESOLVED, IncidentStatus.DISMISSED, status}:
            continue
        if (
            status is IncidentStatus.RESOLVED
            and incident.resource.generation_id
            and incident.resource.generation_id != run.current_generation_id
        ):
            continue
        updated = incident.model_copy(
            update={
                "status": status,
                "resolution_run_id": run.id if status is IncidentStatus.RESOLVED else None,
                "updated_at": max(incident.updated_at, run.updated_at),
                "revision": incident.revision + 1,
            }
        )
        changed.append(await rows.replace(updated, incident.revision))
    return tuple(changed)


async def _retired(rows: RecordTransaction, run: RotationRun) -> bool:
    if not run.current_generation_id or not run.target_generation_id:
        return False
    try:
        old = await rows.get(CredentialGeneration, run.organisation_id, run.current_generation_id)
        target = await rows.get(CredentialGeneration, run.organisation_id, run.target_generation_id)
    except ResourceNotFoundError:
        return False
    if (
        old.credential_id != run.credential_id
        or target.credential_id != run.credential_id
        or old.state is not GenerationState.REVOKED
        or old.successor_id != target.id
        or target.predecessor_id != old.id
        or target.attempt_id != run.id
        or target.state not in {GenerationState.ACTIVE, GenerationState.REVOKED}
    ):
        return False
    result = await rows.database.collection("executions").find_one(
        {
            "organisation_id": run.organisation_id,
            "run_id": run.id,
            "stage": Stage.REVOKE.value,
            "data.status": "succeeded",
        },
        session=rows.session,
    )
    if not result:
        return False
    output = result["data"].get("output", {})
    return all(
        output.get(check) is True
        for check in ("old_rejected", "replacement_valid", "old_secret_retired")
    )
