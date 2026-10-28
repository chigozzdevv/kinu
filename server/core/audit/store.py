from datetime import datetime

from contracts import AuditEvent

from core.audit.chain import GENESIS, event_hash
from core.errors import IdempotencyConflictError
from core.storage.database import Database


class AuditStore:
    def __init__(self, database: Database) -> None:
        self._database = database

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
        values = {
            "kind": kind,
            "actor_id": actor_id,
            "resource": resource,
            "run_id": run_id,
            "payload": payload,
            "evidence_ids": evidence_ids,
            "occurred_at": occurred_at,
            "region": region,
        }
        async with self._database.transaction(organisation_id) as session:
            duplicate = await self._database.collection("audit").find_one(
                {"organisation_id": organisation_id, "id": event_id},
                session=session,
            )
            if duplicate:
                stored = AuditEvent.model_validate(duplicate["data"])
                if any(getattr(stored, key) != value for key, value in values.items()):
                    raise IdempotencyConflictError(
                        "audit identity was already used for another event"
                    )
                return stored
            previous = await self._database.collection("audit").find_one(
                {"organisation_id": organisation_id},
                sort=[("sequence", -1)],
                session=session,
            )
            last = AuditEvent.model_validate(previous["data"]) if previous else None
            sequence, previous_hash = (last.sequence + 1, last.event_hash) if last else (0, GENESIS)
            hashed = event_hash(
                organisation_id,
                sequence,
                kind,
                actor_id,
                resource,
                run_id,
                payload,
                evidence_ids,
                previous_hash,
                occurred_at,
                region,
            )
            event = AuditEvent(
                id=event_id,
                organisation_id=organisation_id,
                sequence=sequence,
                previous_hash=previous_hash,
                event_hash=hashed,
                kind=kind,
                actor_id=actor_id,
                resource=resource,
                run_id=run_id,
                payload=payload,
                evidence_ids=evidence_ids,
                occurred_at=occurred_at,
                region=region,
            )
            await self._database.collection("audit").insert_one(
                {
                    "organisation_id": organisation_id,
                    "id": event_id,
                    "sequence": sequence,
                    "data": event.model_dump(mode="json"),
                },
                session=session,
            )
            return event

    async def list_events(self, organisation_id: str, limit: int) -> tuple[AuditEvent, ...]:
        return tuple(
            [
                AuditEvent.model_validate(row["data"])
                async for row in self._database.collection("audit")
                .find({"organisation_id": organisation_id})
                .sort("sequence", -1)
                .limit(min(max(limit, 1), 500))
            ]
        )
