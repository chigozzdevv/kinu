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
from contracts.api_playbook import ApiPlaybook, ApiPlaybookVersion
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from core.connection.models import AwsLink
from core.errors import ResourceConflictError, ResourceNotFoundError, RevisionConflictError
from core.storage.database import Database

_KINDS: dict[type[Contract], str] = {
    AgentRegistration: "agent",
    AgentSession: "agent-session",
    AgentMemory: "agent-memory",
    Approval: "approval",
    ProtectedAction: "protected-action",
    Playbook: "playbook",
    ApiPlaybook: "api-playbook",
    ApiPlaybookVersion: "api-playbook-version",
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
        yield RecordTransaction(self._database)

    @asynccontextmanager
    async def write(self, organisation_id: str) -> AsyncIterator[RecordTransaction]:
        async with self._database.transaction(organisation_id) as session:
            yield RecordTransaction(self._database, session)


class RecordTransaction:
    def __init__(self, database: Database, session: AsyncClientSession | None = None) -> None:
        self.database, self.session = database, session
        self.records = database.collection("inventory")

    async def get[T: Contract](self, model: type[T], organisation_id: str, resource_id: str) -> T:
        row = await self.records.find_one(
            {"organisation_id": organisation_id, "kind": _KINDS[model], "id": resource_id},
            session=self.session,
        )
        if row is None:
            raise ResourceNotFoundError(f"{_KINDS[model]} is unavailable")
        return model.model_validate(row["data"])

    async def list[T: Contract](self, model: type[T], organisation_id: str) -> tuple[T, ...]:
        return await self.match(model, organisation_id, {})

    async def match[T: Contract](
        self, model: type[T], organisation_id: str, values: dict[str, object]
    ) -> tuple[T, ...]:
        if not values.keys() <= model.model_fields.keys():
            raise ValueError("record lookup requires known fields")
        query = {
            "organisation_id": organisation_id,
            "kind": _KINDS[model],
            **{f"data.{key}": value for key, value in values.items()},
        }
        return tuple(
            [
                model.model_validate(row["data"])
                async for row in self.records.find(query, session=self.session).sort("id")
            ]
        )

    async def add[T: Contract](self, value: T, organisation_id: str) -> T:
        data = value.model_dump(mode="json")
        if data["organisation_id"] != organisation_id or data.get("revision", 0) != 0:
            raise ResourceConflictError("new inventory must belong to this workspace at revision 0")
        try:
            await self.records.insert_one(
                {
                    "organisation_id": organisation_id,
                    "kind": _KINDS[type(value)],
                    "id": data["id"],
                    "revision": 0,
                    "data": data,
                },
                session=self.session,
            )
        except DuplicateKeyError as error:
            raise ResourceConflictError("inventory record already exists") from error
        return value

    async def replace[T: Contract](self, value: T, revision: int) -> T:
        data = value.model_dump(mode="json")
        if data.get("revision") != revision + 1:
            raise RevisionConflictError("inventory revision must advance once")
        result = await self.records.update_one(
            {
                "organisation_id": data["organisation_id"],
                "kind": _KINDS[type(value)],
                "id": data["id"],
                "revision": revision,
            },
            {"$set": {"data": data, "revision": revision + 1}},
            session=self.session,
        )
        if not result.matched_count:
            raise RevisionConflictError("inventory revision changed")
        return value

    async def remove(self, value: ConsumerBinding) -> None:
        result = await self.records.delete_one(
            {
                "organisation_id": value.organisation_id,
                "kind": "binding",
                "id": value.id,
                "revision": value.revision,
            },
            session=self.session,
        )
        if not result.deleted_count:
            raise RevisionConflictError("consumer binding changed")

    async def publish[T: (PlaybookVersion, ProbeVersion, ApiPlaybookVersion)](
        self, previous: T, changed: T
    ) -> T:
        mutable = {"state", "published_by", "published_at", "approved_by", "approved_at"}
        if previous.model_dump(exclude=mutable) != changed.model_dump(exclude=mutable):
            raise ResourceConflictError("publication cannot change an immutable definition")
        result = await self.records.update_one(
            {
                "organisation_id": previous.organisation_id,
                "kind": _KINDS[type(previous)],
                "id": previous.id,
                "data": previous.model_dump(mode="json"),
            },
            {"$set": {"data": changed.model_dump(mode="json")}},
            session=self.session,
        )
        if not result.matched_count:
            raise RevisionConflictError("version publication changed")
        return changed
