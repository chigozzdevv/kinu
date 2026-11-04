from datetime import datetime
from typing import Any

from contracts import CorrelationCandidate, Incident, IncidentStatus, IngestionEvent

from core.errors import (
    IdempotencyConflictError,
    ResourceConflictError,
    RevisionConflictError,
    StorageIntegrityError,
)
from core.storage.database import Database
from core.storage.records import Records, RecordTransaction


class IncidentStore:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._rows = Records(database)

    async def ingest(self, incident: Incident, event: IngestionEvent) -> tuple[Incident, bool]:
        org = incident.organisation_id
        if event.organisation_id != org or incident.event_id != event.id:
            raise StorageIntegrityError("incident does not match its source event")
        async with self._rows.write(org) as rows:
            existing = await rows.match(
                IngestionEvent,
                org,
                {"source": event.source, "source_event_id": event.source_event_id},
            )
            if existing:
                stored = existing[0]
                if stored.stable_payload() != event.stable_payload():
                    raise IdempotencyConflictError(
                        "source event was already used for another signal"
                    )
                incidents = await rows.match(Incident, org, {"event_id": stored.id})
                if len(incidents) != 1:
                    raise StorageIntegrityError("source event has no unique incident")
                return incidents[0], False
            await rows.add(event, org)
            await rows.add(incident, org)
            return incident, True

    async def get(self, organisation_id: str, incident_id: str) -> Incident:
        async with self._rows.read() as rows:
            return await rows.get(Incident, organisation_id, incident_id)

    async def list_incidents(
        self,
        organisation_id: str,
        limit: int,
        *,
        statuses: frozenset[IncidentStatus] | None = None,
        before: str | None = None,
    ) -> tuple[Incident, ...]:
        query: dict[str, Any] = {"organisation_id": organisation_id, "kind": "incident"}
        if statuses is not None:
            query["data.status"] = {"$in": sorted(statuses)}
        if before:
            anchor = await self.get(organisation_id, before)
            created = anchor.model_dump(mode="json")["created_at"]
            query["$or"] = [
                {"data.created_at": {"$lt": created}},
                {"data.created_at": created, "id": {"$lt": anchor.id}},
            ]
        cursor = self._database.collection("inventory").find(query)
        return tuple(
            [
                Incident.model_validate(row["data"])
                async for row in cursor.sort([("data.created_at", -1), ("id", -1)]).limit(
                    min(max(limit, 1), 500)
                )
            ]
        )

    async def count_incidents(
        self, organisation_id: str, statuses: frozenset[IncidentStatus]
    ) -> int:
        async with self._rows.read() as rows:
            return sum(
                item.status in statuses for item in await rows.list(Incident, organisation_id)
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
        async with self._rows.write(organisation_id) as rows:
            current = await _current(rows, organisation_id, incident_id, expected_revision)
            if current.status in {
                IncidentStatus.ROTATING,
                IncidentStatus.CONTAINED,
                IncidentStatus.RESOLVED,
                IncidentStatus.DISMISSED,
            }:
                raise ResourceConflictError("incident is no longer awaiting correlation")
            return await _replace(
                rows,
                current,
                updated_at,
                candidates=candidates,
                credential_id=credential_id,
                status=IncidentStatus.ACTION if credential_id else IncidentStatus.CORRELATING,
            )

    async def link_run(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        credential_id: str,
        run_id: str,
        updated_at: datetime,
    ) -> Incident:
        async with self._rows.write(organisation_id) as rows:
            current = await _current(rows, organisation_id, incident_id, expected_revision)
            if (
                current.status is not IncidentStatus.ACTION
                or current.credential_id != credential_id
            ):
                raise ResourceConflictError("incident must confirm this credential before rotation")
            return await _replace(
                rows, current, updated_at, run_id=run_id, status=IncidentStatus.ROTATING
            )

    async def advance_run(
        self, organisation_id: str, run_id: str, status: IncidentStatus, updated_at: datetime
    ) -> tuple[Incident, ...]:
        if status not in {
            IncidentStatus.ROTATING,
            IncidentStatus.CONTAINED,
            IncidentStatus.RESOLVED,
            IncidentStatus.ACTION,
        }:
            raise ResourceConflictError("unsupported rotation incident status")
        async with self._rows.write(organisation_id) as rows:
            values = await rows.match(Incident, organisation_id, {"run_id": run_id})
            return tuple(
                [
                    await _replace(rows, item, updated_at, status=status)
                    for item in values
                    if item.status not in {IncidentStatus.DISMISSED, IncidentStatus.RESOLVED}
                ]
            )

    async def dismiss(
        self,
        organisation_id: str,
        incident_id: str,
        expected_revision: int,
        reason: str,
        updated_at: datetime,
    ) -> Incident:
        async with self._rows.write(organisation_id) as rows:
            current = await _current(rows, organisation_id, incident_id, expected_revision)
            if current.status in {
                IncidentStatus.ROTATING,
                IncidentStatus.CONTAINED,
                IncidentStatus.RESOLVED,
            }:
                raise ResourceConflictError("an active or completed rotation cannot be dismissed")
            return await _replace(
                rows, current, updated_at, status=IncidentStatus.DISMISSED, dismissal_reason=reason
            )


async def _current(rows: RecordTransaction, org: str, identity: str, revision: int) -> Incident:
    current = await rows.get(Incident, org, identity)
    if current.revision != revision:
        raise RevisionConflictError("incident revision changed")
    return current


async def _replace(
    rows: RecordTransaction, current: Incident, now: datetime, **values: Any
) -> Incident:
    updated = Incident.model_validate(
        current.model_dump() | values | {"updated_at": now, "revision": current.revision + 1}
    )
    return await rows.replace(updated, current.revision)
