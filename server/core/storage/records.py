from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from contracts import (
    AgentMemory,
    AgentRegistration,
    AgentSession,
    Application,
    Approval,
    Connection,
    ConsumerBinding,
    ConsumerService,
    Contract,
    ControlVersion,
    CredentialGeneration,
    Environment,
    Incident,
    IngestionEvent,
    ManagedCredential,
    Notification,
    NotificationEndpoint,
    Playbook,
    PlaybookVersion,
    Probe,
    ProbeVersion,
    ProtectedAction,
)
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from core.connection.models import AwsLink
from core.errors import ResourceConflictError, ResourceNotFoundError, RevisionConflictError
from core.storage.database import Database
from core.storage.sql import SqlConnection

_KINDS: dict[type[Contract], str] = {
    AgentRegistration: "agent",
    AgentSession: "agent-session",
    AgentMemory: "agent-memory",
    Approval: "approval",
    ProtectedAction: "protected-action",
    Playbook: "playbook",
    Probe: "probe",
    Incident: "incident",
    Notification: "notification",
    NotificationEndpoint: "notification-endpoint",
    IngestionEvent: "ingestion-event",
    AwsLink: "aws-link",
    Connection: "connection",
    Application: "application",
    Environment: "environment",
    ConsumerService: "service",
    ManagedCredential: "credential",
    CredentialGeneration: "generation",
    ConsumerBinding: "binding",
    ControlVersion: "controls",
    ProbeVersion: "probe-version",
    PlaybookVersion: "playbook-version",
}


class Records:
    def __init__(self, database: Database) -> None:
        self._database = database

    @asynccontextmanager
    async def read(self) -> AsyncIterator[RecordTransaction]:
        async with self._database.connection() as connection:
            yield RecordTransaction(connection)

    @asynccontextmanager
    async def write(self, organisation_id: str) -> AsyncIterator[RecordTransaction]:
        async with self._database.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                "SELECT id FROM kinu.organisations WHERE id = %s FOR UPDATE", (organisation_id,)
            )
            if await cursor.fetchone() is None:
                raise ResourceNotFoundError("workspace is unavailable")
            yield RecordTransaction(connection)


class RecordTransaction:
    def __init__(self, connection: SqlConnection) -> None:
        self._connection = connection

    @property
    def sql(self) -> SqlConnection:
        return self._connection

    async def get[T: Contract](self, model: type[T], organisation_id: str, resource_id: str) -> T:
        cursor = await self._connection.execute(
            "SELECT data FROM kinu.inventory WHERE organisation_id = %s AND kind = %s AND id = %s",
            (organisation_id, _KINDS[model], resource_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError(f"{_KINDS[model]} is unavailable")
        return model.model_validate(row["data"])

    async def list[T: Contract](self, model: type[T], organisation_id: str) -> tuple[T, ...]:
        cursor = await self._connection.execute(
            "SELECT data FROM kinu.inventory WHERE organisation_id = %s AND kind = %s ORDER BY id",
            (organisation_id, _KINDS[model]),
        )
        return tuple(model.model_validate(row["data"]) for row in await cursor.fetchall())

    async def match[T: Contract](
        self, model: type[T], organisation_id: str, values: dict[str, object]
    ) -> tuple[T, ...]:
        if not values or not values.keys() <= model.model_fields.keys():
            raise ValueError("record lookup requires known fields")
        cursor = await self._connection.execute(
            "SELECT data FROM kinu.inventory WHERE organisation_id = %s AND kind = %s "
            "AND data @> %s ORDER BY id",
            (organisation_id, _KINDS[model], Jsonb(values)),
        )
        return tuple(model.model_validate(row["data"]) for row in await cursor.fetchall())

    async def add[T: Contract](self, value: T, organisation_id: str) -> T:
        data = value.model_dump(mode="json")
        if data["organisation_id"] != organisation_id or data.get("revision", 0) != 0:
            raise ResourceConflictError("new inventory must belong to this workspace at revision 0")
        try:
            await self._connection.execute(
                "INSERT INTO kinu.inventory (organisation_id, kind, id, data) VALUES (%s,%s,%s,%s)",
                (organisation_id, _KINDS[type(value)], data["id"], Jsonb(data)),
            )
        except UniqueViolation as error:
            raise ResourceConflictError("inventory record already exists") from error
        return value

    async def replace[T: Contract](self, value: T, revision: int) -> T:
        data = value.model_dump(mode="json")
        if data.get("revision") != revision + 1:
            raise RevisionConflictError("inventory revision must advance once")
        cursor = await self._connection.execute(
            "UPDATE kinu.inventory SET data = %s, revision = %s "
            "WHERE organisation_id = %s AND kind = %s AND id = %s AND revision = %s RETURNING id",
            (
                Jsonb(data),
                revision + 1,
                data["organisation_id"],
                _KINDS[type(value)],
                data["id"],
                revision,
            ),
        )
        if await cursor.fetchone() is None:
            raise RevisionConflictError("inventory revision changed")
        return value

    async def remove(self, value: ConsumerBinding) -> None:
        cursor = await self._connection.execute(
            "DELETE FROM kinu.inventory WHERE organisation_id = %s AND kind = 'binding' "
            "AND id = %s AND revision = %s RETURNING id",
            (value.organisation_id, value.id, value.revision),
        )
        if await cursor.fetchone() is None:
            raise RevisionConflictError("consumer binding changed")

    async def publish[T: (PlaybookVersion, ProbeVersion)](self, previous: T, changed: T) -> T:
        mutable = {"state", "published_by", "published_at", "approved_by", "approved_at"}
        if previous.model_dump(exclude=mutable) != changed.model_dump(exclude=mutable):
            raise ResourceConflictError("publication cannot change an immutable definition")
        cursor = await self._connection.execute(
            "UPDATE kinu.inventory SET data=%s WHERE organisation_id=%s AND kind=%s "
            "AND id=%s AND data=%s RETURNING id",
            (
                Jsonb(changed.model_dump(mode="json")),
                previous.organisation_id,
                _KINDS[type(previous)],
                previous.id,
                Jsonb(previous.model_dump(mode="json")),
            ),
        )
        if await cursor.fetchone() is None:
            raise RevisionConflictError("version publication changed")
        return changed
