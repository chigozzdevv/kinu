import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from contracts import (
    CreateRunCommand,
    EventKind,
    OutboxEvent,
    RotationRun,
    RunCommand,
    RunEvent,
    RunStatus,
    RunStep,
    StageProof,
)
from policy import digest

from core.errors import StorageIntegrityError

Transition = Callable[[RotationRun], RotationRun]


@dataclass(frozen=True, slots=True)
class MutationResult:
    run: RotationRun
    step: RunStep
    applied: bool


class RunRepository(Protocol):
    async def create(
        self,
        run: RotationRun,
        command: CreateRunCommand,
    ) -> MutationResult: ...

    async def get(self, organisation_id: str, run_id: str) -> RotationRun: ...

    async def list_runs(self, organisation_id: str, limit: int) -> tuple[RotationRun, ...]: ...

    async def mutate(
        self,
        command: RunCommand,
        kind: EventKind,
        transition: Transition,
        proof: StageProof | None = None,
    ) -> MutationResult: ...


def creation_hash(command: CreateRunCommand) -> str:
    payload = command.model_dump(mode="json", exclude={"id"})
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def mutation_hash(command: RunCommand) -> str:
    return digest(command)


def validate_create(run: RotationRun, command: CreateRunCommand) -> None:
    if (
        run.organisation_id != command.organisation_id
        or run.credential_id != command.credential_id
        or run.control_version != command.control_version
        or run.trigger != command.trigger
        or run.id != (command.run_id or run.id)
    ):
        raise StorageIntegrityError("created run does not match its command")


def validate_transition(
    previous: RotationRun,
    updated: RotationRun,
    organisation_id: str,
) -> None:
    if updated.organisation_id != organisation_id or updated.id != previous.id:
        raise StorageIntegrityError("a transition cannot change run identity")
    if updated.revision != previous.revision + 1:
        raise StorageIntegrityError("a transition must increment the revision exactly once")


def build_outbox(
    event_id: str,
    run: RotationRun,
    kind: EventKind,
    actor_id: str,
    occurred_at: datetime,
    payload: dict[str, str | int | float | bool | None],
) -> OutboxEvent:
    event = RunEvent(
        id=event_id,
        organisation_id=run.organisation_id,
        run_id=run.id,
        credential_id=run.credential_id,
        kind=kind,
        revision=run.revision,
        stage=run.stage,
        status=run.status,
        actor_id=actor_id,
        occurred_at=occurred_at,
        payload=payload,
    )
    return OutboxEvent(event=event, available_at=occurred_at)


def resolved_event(kind: EventKind, run: RotationRun) -> EventKind:
    if run.status is RunStatus.COMPLETED:
        return EventKind.RUN_COMPLETED
    return kind
