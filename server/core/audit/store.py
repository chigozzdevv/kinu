from datetime import datetime

from contracts import AuditEvent
from psycopg.types.json import Jsonb

from core.audit.chain import GENESIS, event_hash
from core.errors import IdempotencyConflictError, ResourceNotFoundError
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
        async with self._database.connection() as connection, connection.transaction():
            exists = await (
                await connection.execute(
                    "SELECT id FROM kinu.organisations WHERE id = %s FOR UPDATE", (organisation_id,)
                )
            ).fetchone()
            if exists is None:
                raise ResourceNotFoundError("workspace is unavailable")
            duplicate = await (
                await connection.execute(
                    "SELECT data FROM kinu.audit WHERE organisation_id = %s AND id = %s",
                    (organisation_id, event_id),
                )
            ).fetchone()
            if duplicate:
                stored = AuditEvent.model_validate(duplicate["data"])
                if any(getattr(stored, key) != value for key, value in values.items()):
                    raise IdempotencyConflictError(
                        "audit identity was already used for another event"
                    )
                return stored
            previous = await (
                await connection.execute(
                    "SELECT data FROM kinu.audit WHERE organisation_id = %s "
                    "ORDER BY sequence DESC LIMIT 1",
                    (organisation_id,),
                )
            ).fetchone()
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
            await connection.execute(
                "INSERT INTO kinu.audit VALUES (%s,%s,%s,%s)",
                (organisation_id, event_id, sequence, Jsonb(event.model_dump(mode="json"))),
            )
            return event

    async def list_events(self, organisation_id: str, limit: int) -> tuple[AuditEvent, ...]:
        async with self._database.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT data FROM kinu.audit WHERE organisation_id = %s "
                    "ORDER BY sequence DESC LIMIT %s",
                    (organisation_id, min(max(limit, 1), 500)),
                )
            ).fetchall()
        return tuple(AuditEvent.model_validate(row["data"]) for row in rows)
