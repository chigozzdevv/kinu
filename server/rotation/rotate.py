from datetime import UTC, datetime, timedelta

from connectors.base.errors import ConnectorError
from contracts import (
    CleanupRunCommand,
    CompleteStageCommand,
    FailRunCommand,
    Failure,
    RotationRun,
    RunStatus,
    Stage,
    StageExecutionResult,
    StageExecutionStatus,
    StageProof,
    StartRunCommand,
)
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.errors import LeaseConflictError, ResourceConflictError
from core.state import RotationMachine
from core.storage.database import Database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from policy import GatePolicy

from rotation.generations import discard
from rotation.journal import Journal
from rotation.ports import Effects, Loader
from rotation.stages import Stages, stable
from rotation.state import Snapshot


class Rotation:
    def __init__(self, database: Database, effects: Effects, loader: Loader, region: str) -> None:
        self.database, self.effects, self.loader = database, effects, loader
        self.journal = Journal(database)
        self.audit = AuditWriter(AuditStore(database), region, lambda: datetime.now(UTC))

    async def execute(self, org: str, run_id: str, owner: str) -> RotationRun:
        repository = RunStore(self.database)
        run = await repository.get(org, run_id)
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.CLEANUP,
            RunStatus.COMPENSATED,
            RunStatus.PAUSED,
        }:
            return run
        if (
            run.status is RunStatus.RUNNING
            and run.lease
            and run.lease.expires_at <= datetime.now(UTC)
        ):
            failure = Failure(
                code="worker-lease-expired",
                message="Worker stopped without a final outcome; inspect the saved evidence.",
                retryable=False,
            )
            command = {
                "id": stable("expired", run.id),
                "organisation_id": org,
                "run_id": run.id,
                "actor_id": owner,
                "expected_revision": run.revision,
                "fencing_token": run.fencing_token,
                "failure": failure,
            }
            if run.plan_id is None:
                return (
                    await RunWorkflow(repository).fail(FailRunCommand.model_validate(command))
                ).run
            return (
                await RunWorkflow(repository).cleanup(CleanupRunCommand.model_validate(command))
            ).run
        try:
            state = await self.loader(org, run.credential_id, run.control_version)
        except ResourceConflictError:
            return await self._reject(repository, run, owner)
        workflow = RunWorkflow(
            repository, RotationMachine(GatePolicy(state.controls.definition.required_checks))
        )
        if run.status is RunStatus.PENDING:
            run = (
                await workflow.start(
                    StartRunCommand(
                        id=stable("start", run.id),
                        organisation_id=org,
                        run_id=run.id,
                        actor_id=owner,
                        owner_id=owner,
                        expected_revision=run.revision,
                        expires_at=datetime.now(UTC) + timedelta(minutes=14),
                    )
                )
            ).run
        elif not run.lease or run.lease.owner_id != owner:
            raise LeaseConflictError("another rotation worker owns this run")
        stages = Stages(self.database, self.effects, state, run)
        try:
            while run.status is RunStatus.RUNNING:
                await self._guard(run, state)
                result = await self.journal.get(org, run_id, run.stage)
                if result is None:
                    started = datetime.now(UTC)
                    checks, output, bindings = await stages.execute(run.stage)
                    identity = stable("execution", run.id + ":" + run.stage.value)
                    result = await self.journal.save(
                        StageExecutionResult(
                            id=identity,
                            organisation_id=org,
                            run_id=run_id,
                            stage=run.stage,
                            status=StageExecutionStatus.SUCCEEDED,
                            checks=checks,
                            output=output,
                            evidence_ids=(identity,),
                            bindings=bindings,
                            started_at=started,
                            completed_at=datetime.now(UTC),
                        )
                    )
                await self.audit.append(
                    result.id,
                    org,
                    "rotation.stage-completed",
                    owner,
                    f"runs/{run_id}",
                    {
                        "stage": run.stage.value,
                        "checks": ",".join(sorted(result.checks)),
                        "method": "api",
                    },
                    run_id=run_id,
                    occurred_at=result.completed_at,
                )
                run = (
                    await workflow.complete(
                        CompleteStageCommand(
                            id=stable("complete", run.id + ":" + run.stage.value),
                            organisation_id=org,
                            run_id=run.id,
                            actor_id=owner,
                            expected_revision=run.revision,
                            fencing_token=run.fencing_token,
                            bindings=result.bindings,
                            proof=StageProof(
                                run_id=run.id,
                                organisation_id=org,
                                stage=run.stage,
                                checks=result.checks,
                                evidence_ids=result.evidence_ids,
                                actor_id=owner,
                                recorded_at=result.completed_at,
                            ),
                        )
                    )
                ).run
            return run
        except Exception as error:
            current = await repository.get(org, run_id)
            if (
                not current.lease
                or current.lease.owner_id != owner
                or current.fencing_token != run.fencing_token
            ):
                return current
            created = await self.journal.get(org, run_id, Stage.CREATE)
            deployment = await self.journal.get(org, run_id, Stage.DEPLOY)
            restored = True
            try:
                if created:
                    restored = await self.effects.rollback(
                        state, created.output, deployment.output if deployment else None
                    )
                elif current.stage is Stage.CREATE:
                    restored = await self.effects.cleanup_unrecorded(state, run_id)
            except Exception:
                restored = False
            if restored and created:
                await discard(self.database, org, stable("generation", run.id))
            failure = Failure(
                code="api-rotation-failed" if restored else "api-cleanup-required",
                message=f"Stopped during {current.stage.value} before retiring the old key."
                if restored
                else "Rotation stopped; inspect the recorded evidence before recovery.",
                retryable=False,
            )
            if await self.journal.get(org, run_id, current.stage) is None:
                now = datetime.now(UTC)
                await self.journal.save(
                    StageExecutionResult(
                        id=stable("execution", run_id + ":" + current.stage.value),
                        organisation_id=org,
                        run_id=run_id,
                        stage=current.stage,
                        status=StageExecutionStatus.FAILED,
                        reason=failure.message,
                        output={
                            "error_code": error.code
                            if isinstance(error, ConnectorError)
                            else "rotation-check-failed"
                        },
                        started_at=now,
                        completed_at=now,
                    )
                )
            command = {
                "id": stable("failed", run.id),
                "organisation_id": org,
                "run_id": run_id,
                "actor_id": owner,
                "expected_revision": current.revision,
                "fencing_token": current.fencing_token,
                "failure": failure,
            }
            if current.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
                if restored:
                    return (await workflow.fail(FailRunCommand.model_validate(command))).run
                return (await workflow.cleanup(CleanupRunCommand.model_validate(command))).run
            return current

    async def _reject(self, repository: RunStore, run: RotationRun, owner: str) -> RotationRun:
        if run.status is not RunStatus.PENDING:
            raise ResourceConflictError("the running rotation mapping changed")
        workflow = RunWorkflow(repository)
        started = (
            await workflow.start(
                StartRunCommand(
                    id=stable("start", run.id),
                    organisation_id=run.organisation_id,
                    run_id=run.id,
                    actor_id=owner,
                    owner_id=owner,
                    expected_revision=run.revision,
                    expires_at=datetime.now(UTC) + timedelta(minutes=14),
                )
            )
        ).run
        return (
            await workflow.fail(
                FailRunCommand(
                    id=stable("failed", run.id),
                    organisation_id=run.organisation_id,
                    run_id=run.id,
                    actor_id=owner,
                    expected_revision=started.revision,
                    fencing_token=started.fencing_token,
                    failure=Failure(
                        code="rotation-mapping-changed",
                        message="Credential mappings or controls changed before execution.",
                        retryable=False,
                    ),
                )
            )
        ).run

    async def _guard(self, run: RotationRun, state: Snapshot) -> None:
        current = await RunStore(self.database).get(run.organisation_id, run.id)
        if (
            current.revision != run.revision
            or current.status is not RunStatus.RUNNING
            or not current.lease
            or current.lease.expires_at <= datetime.now(UTC)
        ):
            raise LeaseConflictError("rotation lease or revision changed")
        if await self.loader(run.organisation_id, run.credential_id, run.control_version) != state:
            raise ResourceConflictError("Credential mapping or connections changed during the run")
