from contracts import Incident, IncidentStatus, RotationRun, RunStatus, Stage
from pymongo.asynchronous.client_session import AsyncClientSession

from core.storage.database import Database
from core.storage.records import RecordTransaction


async def sync_incidents(
    database: Database, run: RotationRun, session: AsyncClientSession
) -> tuple[Incident, ...]:
    if run.status is RunStatus.COMPLETED:
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
    incidents = await rows.match(Incident, run.organisation_id, {"run_id": run.id})
    changed = []
    for incident in incidents:
        if incident.status in {IncidentStatus.RESOLVED, IncidentStatus.DISMISSED, status}:
            continue
        updated = incident.model_copy(
            update={
                "status": status,
                "updated_at": run.updated_at,
                "revision": incident.revision + 1,
            }
        )
        changed.append(await rows.replace(updated, incident.revision))
    return tuple(changed)
