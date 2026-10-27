from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from contracts import (
    CreateRunCommand,
    EventKind,
    RotationRun,
    RunCommand,
    RunStatus,
    RunStep,
    StageProof,
)
from psycopg.types.json import Jsonb

from core.errors import (
    ActiveRunConflictError,
    IdempotencyConflictError,
    ResourceNotFoundError,
    RevisionConflictError,
    RunNotFoundError,
    StorageIntegrityError,
)
from core.storage.database import Database
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
from core.storage.sql import SqlConnection

_TERMINAL = {RunStatus.CANCELLED, RunStatus.COMPLETED, RunStatus.COMPENSATED, RunStatus.FAILED}


class RunStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    @asynccontextmanager
    async def _write(self, organisation_id: str) -> AsyncIterator[SqlConnection]:
        async with self._database.connection() as connection, connection.transaction():
            row = await (
                await connection.execute(
                    "SELECT id FROM kinu.organisations WHERE id = %s FOR UPDATE", (organisation_id,)
                )
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("workspace is unavailable")
            yield connection

    async def create(self, run: RotationRun, command: CreateRunCommand) -> MutationResult:
        validate_create(run, command)
        org, request_hash = run.organisation_id, creation_hash(command)
        async with self._write(org) as connection:
            row = await (
                await connection.execute(
                    "SELECT c.data FROM kinu.triggers t JOIN kinu.commands c "
                    "ON c.organisation_id = t.organisation_id AND c.id = t.command_id "
                    "WHERE t.organisation_id = %s AND t.source = %s AND t.event_id = %s",
                    (org, run.trigger.source, run.trigger.event_id),
                )
            ).fetchone()
            if row:
                return await _duplicate(connection, org, row["data"], request_hash)
            locked = await (
                await connection.execute(
                    "SELECT run_id FROM kinu.run_locks "
                    "WHERE organisation_id = %s AND credential_id = %s",
                    (org, run.credential_id),
                )
            ).fetchone()
            if locked:
                raise ActiveRunConflictError("credential already has an active rotation")
            existing = await (
                await connection.execute(
                    "SELECT id FROM kinu.runs WHERE organisation_id = %s AND id = %s "
                    "UNION ALL SELECT id FROM kinu.commands "
                    "WHERE organisation_id = %s AND id = %s",
                    (org, run.id, org, command.id),
                )
            ).fetchone()
            if existing:
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
            await connection.execute(
                "INSERT INTO kinu.runs (organisation_id,id,credential_id,revision,data) "
                "VALUES (%s,%s,%s,%s,%s)",
                (org, run.id, run.credential_id, run.revision, Jsonb(run.model_dump(mode="json"))),
            )
            await connection.execute(
                "INSERT INTO kinu.run_locks VALUES (%s,%s,%s)", (org, run.credential_id, run.id)
            )
            await _record(
                connection,
                run,
                step,
                EventKind.RUN_CREATED,
                {"source": run.trigger.source, "urgency": run.trigger.urgency},
            )
            await connection.execute(
                "INSERT INTO kinu.triggers VALUES (%s,%s,%s,%s)",
                (org, run.trigger.source, run.trigger.event_id, command.id),
            )
            return MutationResult(run, step, True)

    async def get(self, organisation_id: str, run_id: str) -> RotationRun:
        async with self._database.connection() as connection:
            return await _get(connection, organisation_id, run_id)

    async def list_runs(self, organisation_id: str, limit: int) -> tuple[RotationRun, ...]:
        async with self._database.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT data FROM kinu.runs WHERE organisation_id = %s "
                    "ORDER BY data->>'created_at' DESC, id LIMIT %s",
                    (organisation_id, min(max(limit, 1), 500)),
                )
            ).fetchall()
        return tuple(RotationRun.model_validate(row["data"]) for row in rows)

    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int:
        if not statuses:
            return 0
        placeholders = ",".join("%s" for _ in statuses)
        async with self._database.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT count(*) AS total FROM kinu.runs WHERE organisation_id = %s "
                    f"AND data->>'status' IN ({placeholders})",
                    (organisation_id, *sorted(statuses)),
                )
            ).fetchone()
        return int(row["total"]) if row else 0

    async def mutate(
        self,
        command: RunCommand,
        kind: EventKind,
        transition: Transition,
        proof: StageProof | None = None,
    ) -> MutationResult:
        org, request_hash = command.organisation_id, mutation_hash(command)
        async with self._write(org) as connection:
            previous = await (
                await connection.execute(
                    "SELECT data FROM kinu.commands WHERE organisation_id = %s AND id = %s",
                    (org, command.id),
                )
            ).fetchone()
            if previous:
                return await _duplicate(connection, org, previous["data"], request_hash)
            run = await _get(connection, org, command.run_id)
            if run.revision != command.expected_revision:
                raise RevisionConflictError("run revision changed")
            lock = await (
                await connection.execute(
                    "SELECT run_id FROM kinu.run_locks "
                    "WHERE organisation_id = %s AND credential_id = %s",
                    (org, run.credential_id),
                )
            ).fetchone()
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
            await connection.execute(
                "UPDATE kinu.runs SET revision = %s, data = %s "
                "WHERE organisation_id = %s AND id = %s",
                (updated.revision, Jsonb(updated.model_dump(mode="json")), org, run.id),
            )
            await _record(
                connection,
                updated,
                step,
                resolved_event(kind, updated),
                {"operation": command.operation, "previous_revision": run.revision},
            )
            if holds_lock and updated.status in _TERMINAL:
                await connection.execute(
                    "DELETE FROM kinu.run_locks "
                    "WHERE organisation_id = %s AND credential_id = %s AND run_id = %s",
                    (org, run.credential_id, run.id),
                )
            return MutationResult(updated, step, True)

    async def steps(self, organisation_id: str, run_id: str) -> tuple[RunStep, ...]:
        async with self._database.connection() as connection:
            await _get(connection, organisation_id, run_id)
            rows = await (
                await connection.execute(
                    "SELECT data FROM kinu.commands WHERE organisation_id = %s AND run_id = %s "
                    "ORDER BY (data->>'revision')::integer",
                    (organisation_id, run_id),
                )
            ).fetchall()
        return tuple(RunStep.model_validate(row["data"]) for row in rows)


async def _get(connection: SqlConnection, org: str, run_id: str) -> RotationRun:
    row = await (
        await connection.execute(
            "SELECT data FROM kinu.runs WHERE organisation_id = %s AND id = %s", (org, run_id)
        )
    ).fetchone()
    if row is None:
        raise RunNotFoundError("run is unavailable")
    return RotationRun.model_validate(row["data"])


async def _duplicate(
    connection: SqlConnection, org: str, data: object, request_hash: str
) -> MutationResult:
    step = RunStep.model_validate(data)
    if step.command_hash != request_hash:
        raise IdempotencyConflictError(
            "command or source event was already used for another request"
        )
    return MutationResult(await _get(connection, org, step.run_id), step, False)


async def _record(
    connection: SqlConnection,
    run: RotationRun,
    step: RunStep,
    kind: EventKind,
    payload: dict[str, str | int | float | bool | None],
) -> None:
    event = build_outbox(step.id, run, kind, step.actor_id, step.recorded_at, payload)
    await connection.execute(
        "INSERT INTO kinu.commands VALUES (%s,%s,%s,%s)",
        (run.organisation_id, step.id, run.id, Jsonb(step.model_dump(mode="json"))),
    )
    await connection.execute(
        "INSERT INTO kinu.outbox VALUES (%s,%s,%s)",
        (run.organisation_id, step.id, Jsonb(event.model_dump(mode="json"))),
    )
