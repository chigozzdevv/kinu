import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from contracts import (
    CancelRunCommand,
    CleanupRunCommand,
    CompleteRecoveryCommand,
    CompleteStageCommand,
    CreateRunCommand,
    EventKind,
    FailRunCommand,
    Failure,
    PauseRunCommand,
    RecoverRunCommand,
    RenewLeaseCommand,
    ResumeRunCommand,
    RotationRun,
    RunStatus,
    Stage,
    StartRunCommand,
)

from core.ids import new_id
from core.state import RotationMachine
from core.storage import MutationResult, RunRepository
from core.workflow.trigger import build_run

Clock = Callable[[], datetime]
IdFactory = Callable[[str], str]

_LIST_SCAN_LIMIT = 500
_RECOVERY_STAGES = frozenset({Stage.DEPLOY, Stage.VERIFY, Stage.ROLLOUT, Stage.OBSERVE})


@dataclass(frozen=True, slots=True)
class ReapResult:
    scanned: int
    restarted: int
    failed: int
    runs: tuple[RotationRun, ...]


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunWorkflow:
    def __init__(
        self,
        repository: RunRepository,
        machine: RotationMachine | None = None,
        clock: Clock = utcnow,
        id_factory: IdFactory = new_id,
    ) -> None:
        self._repository = repository
        self._machine = machine or RotationMachine()
        self._clock = clock
        self._id_factory = id_factory

    async def create(self, command: CreateRunCommand) -> MutationResult:
        now = self._clock()
        run = build_run(command, command.run_id or self._id_factory("run"), now)
        return await self._repository.create(run, command)

    async def get(self, organisation_id: str, run_id: str) -> RotationRun:
        return await self._repository.get(organisation_id, run_id)

    async def list_runs(
        self,
        organisation_id: str,
        statuses: frozenset[RunStatus] | None = None,
        limit: int = 100,
    ) -> tuple[RotationRun, ...]:
        runs = await self._repository.list_runs(organisation_id, _LIST_SCAN_LIMIT)
        if statuses is not None:
            runs = tuple(run for run in runs if run.status in statuses)
        ordered = sorted(runs, key=lambda run: (run.created_at, run.id), reverse=True)
        return tuple(ordered[:limit])

    async def start(self, command: StartRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_STARTED,
            lambda run: self._machine.start(
                run,
                command.owner_id,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def renew(self, command: RenewLeaseCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.LEASE_RENEWED,
            lambda run: self._machine.renew(
                run,
                command.owner_id,
                command.fencing_token,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def complete(self, command: CompleteStageCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.STAGE_COMPLETED,
            lambda run: self._machine.complete(
                run,
                command.proof,
                command.fencing_token,
                command.expected_revision,
                now,
                command.bindings,
            ),
            command.proof,
        )

    async def pause(self, command: PauseRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_PAUSED,
            lambda run: self._machine.pause(
                run,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )

    async def resume(self, command: ResumeRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_RESUMED,
            lambda run: self._machine.resume(
                run,
                command.owner_id,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def cleanup(self, command: CleanupRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.CLEANUP_REQUIRED,
            lambda run: self._machine.cleanup(
                run,
                command.failure,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )

    async def fail(self, command: FailRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_FAILED,
            lambda run: self._machine.fail(
                run,
                command.failure,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )

    async def cancel(self, command: CancelRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RUN_CANCELLED,
            lambda run: self._machine.cancel(run, command.expected_revision, now),
        )

    async def recover(self, command: RecoverRunCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RECOVERY_STARTED,
            lambda run: self._machine.recover(
                run,
                command.owner_id,
                command.expected_revision,
                command.expires_at,
                now,
            ),
        )

    async def complete_recovery(self, command: CompleteRecoveryCommand) -> MutationResult:
        now = self._clock()
        return await self._repository.mutate(
            command,
            EventKind.RECOVERY_COMPLETED,
            lambda run: self._machine.complete_recovery(
                run,
                command.recovery_id,
                command.mode,
                command.evidence_ids,
                command.fencing_token,
                command.expected_revision,
                now,
            ),
        )

    async def reap_expired(self, organisation_id: str, actor_id: str) -> ReapResult:
        now = self._clock()
        runs = await self._repository.list_runs(organisation_id, _LIST_SCAN_LIMIT)
        candidates = tuple(
            run
            for run in runs
            if run.status is RunStatus.CLEANUP
            or (
                run.status in {RunStatus.RUNNING, RunStatus.RECOVERING}
                and run.lease is not None
                and run.lease.expires_at <= now
            )
        )
        restarted = 0
        failed = 0
        changed: list[RotationRun] = []
        for run in candidates:
            if run.status is RunStatus.CLEANUP:
                result = await self._restart_recovery(run, actor_id, now)
                restarted += 1
            elif run.status is RunStatus.RECOVERING:
                result = await self.fail(
                    FailRunCommand(
                        id=_reap_id(run, "fail-recovery"),
                        organisation_id=organisation_id,
                        run_id=run.id,
                        actor_id=actor_id,
                        expected_revision=run.revision,
                        fencing_token=run.fencing_token,
                        failure=Failure(
                            code="recovery-lease-expired",
                            message="Recovery worker lease expired before recovery completed.",
                            retryable=False,
                        ),
                    )
                )
                failed += 1
            elif run.stage in _RECOVERY_STAGES and run.plan_id is not None:
                cleanup = await self.cleanup(
                    CleanupRunCommand(
                        id=_reap_id(run, "cleanup"),
                        organisation_id=organisation_id,
                        run_id=run.id,
                        actor_id=actor_id,
                        expected_revision=run.revision,
                        fencing_token=run.fencing_token,
                        failure=Failure(
                            code="stage-lease-expired",
                            message=(f"Worker lease expired while executing {run.stage.value}."),
                            retryable=True,
                        ),
                    )
                )
                result = await self._restart_recovery(cleanup.run, actor_id, now)
                restarted += 1
            else:
                result = await self.fail(
                    FailRunCommand(
                        id=_reap_id(run, "fail"),
                        organisation_id=organisation_id,
                        run_id=run.id,
                        actor_id=actor_id,
                        expected_revision=run.revision,
                        fencing_token=run.fencing_token,
                        failure=Failure(
                            code="stage-lease-expired",
                            message=f"Worker lease expired while executing {run.stage.value}.",
                            retryable=True,
                        ),
                    )
                )
                failed += 1
            changed.append(result.run)
        return ReapResult(len(candidates), restarted, failed, tuple(changed))

    async def _restart_recovery(
        self, run: RotationRun, actor_id: str, now: datetime
    ) -> MutationResult:
        return await self.recover(
            RecoverRunCommand(
                id=_reap_id(run, "recover"),
                organisation_id=run.organisation_id,
                run_id=run.id,
                actor_id=actor_id,
                expected_revision=run.revision,
                owner_id=actor_id,
                expires_at=now + timedelta(minutes=30),
            )
        )


def _reap_id(run: RotationRun, operation: str) -> str:
    checksum = hashlib.sha256(
        f"{run.organisation_id}\0{run.id}\0{run.revision}\0{operation}".encode()
    ).hexdigest()[:40]
    return f"reap_{checksum}"
