from collections.abc import Collection
from typing import Any

from contracts import (
    CreateRunCommand,
    EventKind,
    Incident,
    RotationRun,
    RunCommand,
    RunStatus,
    RunStep,
    StageProof,
)
from pymongo.asynchronous.client_session import AsyncClientSession

from core.errors import (
    ActiveRunConflictError,
    IdempotencyConflictError,
    RevisionConflictError,
    RunNotFoundError,
    StorageIntegrityError,
)
from core.storage.database import Database
from core.storage.incidents import sync_incidents
from core.storage.repository import (
    MutationResult,
    Transition,
    build_outbox,
    creation_hash,
    mutation_hash,
    resolved_event,
    validate_create,
    validate_transition,
)

_TERMINAL = {RunStatus.CANCELLED, RunStatus.COMPLETED, RunStatus.COMPENSATED, RunStatus.FAILED}


def current_runs_pipeline(organisation_id: str) -> list[dict[str, Any]]:
    """Keep each key's latest attempt and every run still holding work open."""
    return [
        {"$match": {"organisation_id": organisation_id}},
        {
            "$setWindowFields": {
                "partitionBy": "$credential_id",
                "sortBy": {"data.created_at": -1, "id": -1},
                "output": {
                    "current_id": {
                        "$first": "$id",
                        "window": {"documents": ["unbounded", "current"]},
                    }
                },
            }
        },
        {
            "$match": {
                "$or": [
                    {"$expr": {"$eq": ["$id", "$current_id"]}},
                    {"data.status": {"$nin": sorted(_TERMINAL)}},
                ]
            }
        },
        {"$unset": "current_id"},
    ]


async def execution_locked(
    database: Database,
    org: str,
    credentials: Collection[str],
    session: AsyncClientSession,
) -> bool:
    async for lock in database.collection("run_locks").find(
        {"organisation_id": org, "credential_id": {"$in": sorted(credentials)}},
        session=session,
    ):
        run = await database.collection("runs").find_one(
            {"organisation_id": org, "id": lock["run_id"]}, session=session
        )
        if not run or run["data"]["status"] != RunStatus.CLEANUP:
            return True
    return False


class RunStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, run: RotationRun, command: CreateRunCommand) -> MutationResult:
        validate_create(run, command)
        org, request_hash = run.organisation_id, creation_hash(command)
        async with self._database.transaction(org) as session:
            trigger = await self._database.collection("triggers").find_one(
                {
                    "organisation_id": org,
                    "source": run.trigger.source,
                    "event_id": run.trigger.event_id,
                },
                session=session,
            )
            if trigger:
                row = await self._database.collection("commands").find_one(
                    {"organisation_id": org, "id": trigger["command_id"]},
                    session=session,
                )
                if not row:
                    raise StorageIntegrityError("source event has no command")
                return await self._duplicate(org, row["data"], request_hash, session)
            if await self._database.collection("run_locks").find_one(
                {"organisation_id": org, "credential_id": run.credential_id},
                session=session,
            ):
                raise ActiveRunConflictError("credential already has an active rotation")
            for name, identity in (("runs", run.id), ("commands", command.id)):
                if await self._database.collection(name).find_one(
                    {"organisation_id": org, "id": identity},
                    session=session,
                ):
                    raise IdempotencyConflictError("run or command identity already exists")
            step = RunStep(
                id=command.id,
                organisation_id=org,
                run_id=run.id,
                operation=command.operation,
                command_hash=request_hash,
                actor_id=command.trigger.actor_id,
                after_stage=run.stage,
                after_status=run.status,
                revision=run.revision,
                recorded_at=run.created_at,
            )
            await self._database.collection("runs").insert_one(
                {
                    "organisation_id": org,
                    "id": run.id,
                    "credential_id": run.credential_id,
                    "revision": run.revision,
                    "data": run.model_dump(mode="json"),
                },
                session=session,
            )
            await self._database.collection("run_locks").insert_one(
                {"organisation_id": org, "credential_id": run.credential_id, "run_id": run.id},
                session=session,
            )
            await self._record(
                run,
                step,
                EventKind.RUN_CREATED,
                {"source": run.trigger.source, "urgency": run.trigger.urgency},
                session,
            )
            await self._database.collection("triggers").insert_one(
                {
                    "organisation_id": org,
                    "source": run.trigger.source,
                    "event_id": run.trigger.event_id,
                    "command_id": command.id,
                },
                session=session,
            )
            return MutationResult(run, step, True)

    async def get(self, organisation_id: str, run_id: str) -> RotationRun:
        return await self._get(organisation_id, run_id)

    async def _get(
        self, org: str, run_id: str, session: AsyncClientSession | None = None
    ) -> RotationRun:
        row = await self._database.collection("runs").find_one(
            {"organisation_id": org, "id": run_id},
            session=session,
        )
        if row is None:
            raise RunNotFoundError("run is unavailable")
        return RotationRun.model_validate(row["data"])

    async def list_runs(
        self,
        organisation_id: str,
        limit: int,
        *,
        before: str | None = None,
        statuses: frozenset[RunStatus] | None = None,
        current: bool = False,
    ) -> tuple[RotationRun, ...]:
        query: dict[str, Any] = {"organisation_id": organisation_id}
        if statuses is not None:
            query["data.status"] = {"$in": sorted(statuses)}
        if before:
            anchor = await self.get(organisation_id, before)
            created = anchor.model_dump(mode="json")["created_at"]
            query["$or"] = [
                {"data.created_at": {"$lt": created}},
                {"data.created_at": created, "id": {"$lt": anchor.id}},
            ]
        if current:
            pipeline = [
                *current_runs_pipeline(organisation_id),
                {"$match": query},
                {"$sort": {"data.created_at": -1, "id": -1}},
                {"$limit": min(max(limit, 1), 500)},
            ]
            current_cursor = await self._database.collection("runs").aggregate(pipeline)
            return tuple([RotationRun.model_validate(row["data"]) async for row in current_cursor])
        cursor = self._database.collection("runs").find(query)
        return tuple(
            [
                RotationRun.model_validate(row["data"])
                async for row in cursor.sort([("data.created_at", -1), ("id", -1)]).limit(
                    min(max(limit, 1), 500)
                )
            ]
        )

    async def reconcile_incidents(self, organisation_id: str, run_id: str) -> tuple[Incident, ...]:
        async with self._database.transaction(organisation_id) as session:
            run = await self._get(organisation_id, run_id, session)
            return await sync_incidents(self._database, run, session)

    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int:
        return await self._database.collection("runs").count_documents(
            {"organisation_id": organisation_id, "data.status": {"$in": sorted(statuses)}}
        )

    async def mutate(
        self,
        command: RunCommand,
        kind: EventKind,
        transition: Transition,
        proof: StageProof | None = None,
    ) -> MutationResult:
        org, request_hash = command.organisation_id, mutation_hash(command)
        async with self._database.transaction(org) as session:
            previous = await self._database.collection("commands").find_one(
                {"organisation_id": org, "id": command.id},
                session=session,
            )
            if previous:
                return await self._duplicate(org, previous["data"], request_hash, session)
            run = await self._get(org, command.run_id, session)
            if run.revision != command.expected_revision:
                raise RevisionConflictError("run revision changed")
            lock = await self._database.collection("run_locks").find_one(
                {"organisation_id": org, "credential_id": run.credential_id},
                session=session,
            )
            holds_lock = bool(lock and lock["run_id"] == run.id)
            if not holds_lock and not (
                command.operation == "cancel" and run.status is RunStatus.FAILED
            ):
                raise StorageIntegrityError("run does not own its credential lock")
            updated = transition(run)
            validate_transition(run, updated, org)
            if updated.credential_id != run.credential_id:
                raise StorageIntegrityError("a transition cannot change credential identity")
            step = RunStep(
                id=command.id,
                organisation_id=org,
                run_id=run.id,
                operation=command.operation,
                command_hash=request_hash,
                actor_id=command.actor_id,
                before_stage=run.stage,
                after_stage=updated.stage,
                before_status=run.status,
                after_status=updated.status,
                revision=updated.revision,
                proof=proof,
                recorded_at=updated.updated_at,
            )
            await self._database.collection("runs").update_one(
                {"organisation_id": org, "id": run.id, "revision": run.revision},
                {"$set": {"revision": updated.revision, "data": updated.model_dump(mode="json")}},
                session=session,
            )
            await sync_incidents(self._database, updated, session)
            await self._record(
                updated,
                step,
                resolved_event(kind, updated),
                {"operation": command.operation, "previous_revision": run.revision},
                session,
            )
            if holds_lock and updated.status in _TERMINAL:
                await self._database.collection("run_locks").delete_one(
                    {"organisation_id": org, "credential_id": run.credential_id, "run_id": run.id},
                    session=session,
                )
            return MutationResult(updated, step, True)

    async def steps(self, organisation_id: str, run_id: str) -> tuple[RunStep, ...]:
        await self._get(organisation_id, run_id)
        return tuple(
            [
                RunStep.model_validate(row["data"])
                async for row in self._database.collection("commands")
                .find({"organisation_id": organisation_id, "run_id": run_id})
                .sort("data.revision")
            ]
        )

    async def _duplicate(
        self, org: str, data: object, request_hash: str, session: AsyncClientSession
    ) -> MutationResult:
        step = RunStep.model_validate(data)
        if step.command_hash != request_hash:
            raise IdempotencyConflictError(
                "command or source event was already used for another request"
            )
        return MutationResult(await self._get(org, step.run_id, session), step, False)

    async def _record(
        self,
        run: RotationRun,
        step: RunStep,
        kind: EventKind,
        payload: dict[str, str | int | float | bool | None],
        session: AsyncClientSession,
    ) -> None:
        event = build_outbox(step.id, run, kind, step.actor_id, step.recorded_at, payload)
        await self._database.collection("commands").insert_one(
            {
                "organisation_id": run.organisation_id,
                "id": step.id,
                "run_id": run.id,
                "data": step.model_dump(mode="json"),
            },
            session=session,
        )
        await self._database.collection("outbox").insert_one(
            {
                "organisation_id": run.organisation_id,
                "id": step.id,
                "data": event.model_dump(mode="json"),
                "delivered": False,
            },
            session=session,
        )
