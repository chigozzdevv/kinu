import asyncio

from contracts import (
    CreateRunCommand,
    EventKind,
    OutboxEvent,
    RotationRun,
    RunCommand,
    RunStatus,
    RunStep,
    StageProof,
)
from core.errors import (
    ActiveRunConflictError,
    IdempotencyConflictError,
    RevisionConflictError,
    RunNotFoundError,
    StorageIntegrityError,
)
from core.storage import MutationResult
from core.storage.repository import (
    Transition,
    build_outbox,
    creation_hash,
    mutation_hash,
    resolved_event,
    validate_create,
    validate_transition,
)


class MemoryRunRepository:
    def __init__(self) -> None:
        self._mutex = asyncio.Lock()
        self._runs: dict[tuple[str, str], RotationRun] = {}
        self._steps: dict[tuple[str, str, str], RunStep] = {}
        self._events: dict[tuple[str, str], OutboxEvent] = {}
        self._locks: dict[tuple[str, str], str] = {}
        self._dedupe: dict[tuple[str, str, str], tuple[str, str, str]] = {}

    @property
    def events(self) -> tuple[OutboxEvent, ...]:
        return tuple(self._events.values())

    @property
    def steps(self) -> tuple[RunStep, ...]:
        return tuple(self._steps.values())

    async def create(
        self,
        run: RotationRun,
        command: CreateRunCommand,
    ) -> MutationResult:
        validate_create(run, command)
        request_hash = creation_hash(command)
        dedupe_key = (run.organisation_id, run.trigger.source, run.trigger.event_id)
        run_key = (run.organisation_id, run.id)
        step_key = (run.organisation_id, run.id, command.id)
        event_key = (run.organisation_id, command.id)
        lock_key = (run.organisation_id, run.credential_id)

        async with self._mutex:
            duplicate = self._dedupe.get(dedupe_key)
            if duplicate is not None:
                run_id, step_id, stored_hash = duplicate
                if stored_hash != request_hash:
                    raise IdempotencyConflictError(
                        "source event was already bound to another request"
                    )
                stored_run = self._runs.get((run.organisation_id, run_id))
                stored_step = self._steps.get((run.organisation_id, run_id, step_id))
                if stored_run is None or stored_step is None:
                    raise StorageIntegrityError("deduplicated run is incomplete")
                return MutationResult(run=stored_run, step=stored_step, applied=False)

            locked_run = self._locks.get(lock_key)
            if locked_run is not None:
                raise ActiveRunConflictError(
                    f"credential {run.credential_id} is already owned by run {locked_run}"
                )
            if run_key in self._runs:
                raise IdempotencyConflictError(f"run id {run.id} already exists")
            if event_key in self._events:
                raise IdempotencyConflictError(
                    f"command {command.id} already belongs to another run"
                )

            step = RunStep(
                id=command.id,
                organisation_id=run.organisation_id,
                run_id=run.id,
                operation=command.operation,
                command_hash=request_hash,
                actor_id=command.trigger.actor_id,
                after_stage=run.stage,
                after_status=run.status,
                revision=run.revision,
                recorded_at=run.created_at,
            )
            event = build_outbox(
                command.id,
                run,
                EventKind.RUN_CREATED,
                command.trigger.actor_id,
                run.created_at,
                {"source": run.trigger.source, "urgency": run.trigger.urgency},
            )
            self._runs[run_key] = run
            self._steps[step_key] = step
            self._events[event_key] = event
            self._locks[lock_key] = run.id
            self._dedupe[dedupe_key] = (run.id, command.id, request_hash)
            return MutationResult(run=run, step=step, applied=True)

    async def get(self, organisation_id: str, run_id: str) -> RotationRun:
        async with self._mutex:
            run = self._runs.get((organisation_id, run_id))
            if run is None:
                raise RunNotFoundError(f"run {run_id} was not found")
            return run

    async def list_runs(self, organisation_id: str, limit: int) -> tuple[RotationRun, ...]:
        async with self._mutex:
            runs = [run for (org_id, _), run in self._runs.items() if org_id == organisation_id]
            return tuple(runs[:limit])

    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int:
        async with self._mutex:
            return sum(
                1
                for (org_id, _), run in self._runs.items()
                if org_id == organisation_id and run.status in statuses
            )

    async def mutate(
        self,
        command: RunCommand,
        kind: EventKind,
        transition: Transition,
        proof: StageProof | None = None,
    ) -> MutationResult:
        run_key = (command.organisation_id, command.run_id)
        step_key = (command.organisation_id, command.run_id, command.id)
        event_key = (command.organisation_id, command.id)
        request_hash = mutation_hash(command)

        async with self._mutex:
            stored_step = self._steps.get(step_key)
            if stored_step is not None:
                if stored_step.command_hash != request_hash:
                    raise IdempotencyConflictError(
                        f"command {command.id} was already used for another mutation"
                    )
                stored_run = self._runs.get(run_key)
                if stored_run is None:
                    raise StorageIntegrityError(
                        f"run {command.run_id} disappeared after command execution"
                    )
                return MutationResult(run=stored_run, step=stored_step, applied=False)

            if event_key in self._events:
                raise IdempotencyConflictError(
                    f"command {command.id} already belongs to another run"
                )
            run = self._runs.get(run_key)
            if run is None:
                raise RunNotFoundError(f"run {command.run_id} was not found")
            if run.revision != command.expected_revision:
                raise RevisionConflictError(
                    f"expected revision {command.expected_revision}, found {run.revision}"
                )
            lock_key = (command.organisation_id, run.credential_id)
            holds_lock = self._locks.get(lock_key) == run.id
            if not holds_lock and not (
                command.operation == "cancel" and run.status is RunStatus.FAILED
            ):
                raise StorageIntegrityError(f"run {run.id} does not hold its credential lock")

            updated = transition(run)
            validate_transition(run, updated, command.organisation_id)
            step = RunStep(
                id=command.id,
                organisation_id=command.organisation_id,
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
            event = build_outbox(
                command.id,
                updated,
                resolved_event(kind, updated),
                command.actor_id,
                updated.updated_at,
                {"operation": command.operation, "previous_revision": run.revision},
            )
            self._runs[run_key] = updated
            self._steps[step_key] = step
            self._events[event_key] = event
            if holds_lock and updated.status in {
                RunStatus.CANCELLED,
                RunStatus.COMPLETED,
                RunStatus.COMPENSATED,
                RunStatus.FAILED,
            }:
                del self._locks[lock_key]
            return MutationResult(run=updated, step=step, applied=True)
