from datetime import UTC, datetime, timedelta

from connectors.base.errors import ConnectorError
from contracts import (
    CleanupRunCommand,
    CompleteStageCommand,
    FailRunCommand,
    Failure,
    PauseRunCommand,
    ResumeRunCommand,
    RotationRun,
    RunStatus,
    Stage,
    StageBindings,
    StageExecutionResult,
    StageExecutionStatus,
    StageProof,
    StartRunCommand,
)
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.errors import LeaseConflictError, ResourceConflictError, ResourceSetupRequiredError
from core.state import RotationMachine
from core.storage.database import Database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from policy import GatePolicy

from rotation.approval import RevocationApprovals
from rotation.generations import discard
from rotation.journal import Journal
from rotation.lease import Heartbeat
from rotation.ports import Effects, Loader
from rotation.stages import Planner, Stages, stable
from rotation.state import Snapshot

_CHECK_FAILURES = {
    "host-lease": "The rotation no longer holds the key lock.",
    "secret-current": "The active key changed. Check the credential before trying again.",
    "browser-probe": "The current key cannot be verified for browser rotation.",
    "provider-identity": "The provider key could not be identified.",
    "host-variable": "An app's key mapping changed. Review its connection.",
    "host-proof": "An app does not have a working verification path.",
    "provider-management": "Use a separate provider management credential.",
}


class Rotation:
    def __init__(
        self,
        database: Database,
        effects: Effects,
        loader: Loader,
        region: str,
        planner: Planner | None = None,
    ) -> None:
        self.database, self.effects, self.loader = database, effects, loader
        self.planner = planner
        self.journal = Journal(database)
        self.audit = AuditWriter(AuditStore(database), region, lambda: datetime.now(UTC))
        self.approvals: RevocationApprovals | None = None

    async def execute(self, org: str, run_id: str, owner: str) -> RotationRun:
        repository = RunStore(self.database)
        run = await repository.get(org, run_id)
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.CLEANUP,
            RunStatus.COMPENSATED,
        }:
            return run
        if run.status is RunStatus.PAUSED:
            if run.stage is not Stage.APPROVAL or self.approvals is None:
                return run
            if not await self.approvals.ready(run):
                return run
            run = (
                await RunWorkflow(repository).resume(
                    ResumeRunCommand(
                        id=stable("resume", run.id + ":" + str(run.revision)),
                        organisation_id=org,
                        run_id=run.id,
                        actor_id=owner,
                        owner_id=owner,
                        expected_revision=run.revision,
                        expires_at=datetime.now(UTC) + timedelta(minutes=14),
                    )
                )
            ).run
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

        async def preflight_progress(check: str) -> None:
            await self.database.collection("run_progress").update_one(
                {"_id": f"{org}:{run_id}:{run.fencing_token}"},
                {"$addToSet": {"checks": check}},
                upsert=True,
            )

        stages = Stages(self.database, self.effects, state, run, self.planner, preflight_progress)
        heartbeat = Heartbeat(repository, workflow, run)
        heartbeat.start()
        try:
            while run.status is RunStatus.RUNNING:
                heartbeat.check()
                stages.run = run
                await self._guard(run, state)
                result = await self.journal.get(org, run_id, run.stage)
                if result is None:
                    started = datetime.now(UTC)
                    if (
                        run.stage is Stage.PREFLIGHT
                        and state.controls.definition.require_revoke_approval
                    ):
                        if self.approvals is None:
                            raise ResourceConflictError("Revocation approval is not configured")
                        await self.approvals.ensure_approver(run)
                        await preflight_progress("approver-checked")
                    if (
                        run.stage is Stage.APPROVAL
                        and state.controls.definition.require_revoke_approval
                    ):
                        if self.approvals is None:
                            raise ResourceConflictError("Revocation approval is not configured")
                        output = await self.approvals.authorize(state, run)
                        if output is None:
                            async with heartbeat.lock:
                                current = await repository.get(org, run_id)
                                return (
                                    await workflow.pause(
                                        PauseRunCommand(
                                            id=stable(
                                                "pause", run.id + ":" + str(current.revision)
                                            ),
                                            organisation_id=org,
                                            run_id=run.id,
                                            actor_id=owner,
                                            expected_revision=current.revision,
                                            fencing_token=run.fencing_token,
                                        )
                                    )
                                ).run
                        checks = frozenset(
                            {"approval-valid", "action-digest-valid", "evidence-current"}
                        )
                        bindings = StageBindings()
                    else:
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
                        "method": "browser"
                        if state.playbook is not None and run.stage in {Stage.CREATE, Stage.REVOKE}
                        else "api",
                    },
                    run_id=run_id,
                    occurred_at=result.completed_at,
                )
                async with heartbeat.lock:
                    heartbeat.check()
                    current = await repository.get(org, run_id)
                    run = (
                        await workflow.complete(
                            CompleteStageCommand(
                                id=stable("complete", run.id + ":" + run.stage.value),
                                organisation_id=org,
                                run_id=run.id,
                                actor_id=owner,
                                expected_revision=current.revision,
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
            rollback_error: Exception | None = None
            try:
                if created:
                    restored = await self.effects.rollback(
                        state,
                        created.output | {"run_id": run_id, "fencing_token": current.fencing_token},
                        deployment.output if deployment else None,
                    )
                elif current.stage is Stage.CREATE:
                    restored = await self.effects.cleanup_unrecorded(state, run_id)
            except Exception as rollback_failure:
                rollback_error = rollback_failure
                restored = False
            if restored and created:
                await discard(self.database, org, stable("generation", run.id))
            failure = Failure(
                code="builder-setup-required"
                if current.stage is Stage.PLAN and isinstance(error, ResourceSetupRequiredError)
                else "api-rotation-failed"
                if restored
                else "api-cleanup-required",
                message="Connect Builder AI in Settings and start a new rotation."
                if current.stage is Stage.PLAN and isinstance(error, ResourceSetupRequiredError)
                else "Builder AI could not produce a valid plan. Check AI settings and try again."
                if current.stage is Stage.PLAN
                else _CHECK_FAILURES[error.code]
                if restored
                and current.stage is Stage.PREFLIGHT
                and isinstance(error, ConnectorError)
                and error.code in _CHECK_FAILURES
                else f"Stopped during {current.stage.value} before retiring the old key."
                if restored
                else f"Stopped at {current.stage.value}. Review the saved checks before recovery.",
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
                            else "rotation-check-failed",
                            "error_type": type(error).__name__,
                            **(
                                {
                                    "rollback_error_type": type(rollback_error).__name__,
                                    "rollback_error_code": rollback_error.code
                                    if isinstance(rollback_error, ConnectorError)
                                    else "rollback-check-failed",
                                }
                                if rollback_error is not None
                                else {}
                            ),
                        },
                        started_at=now,
                        completed_at=now,
                    )
                )
            async with heartbeat.lock:
                current = await repository.get(org, run_id)
                if not current.lease or current.lease.owner_id != owner:
                    return current
                command = {
                    "id": stable("failed", run.id),
                    "organisation_id": org,
                    "run_id": run_id,
                    "actor_id": owner,
                    "expected_revision": current.revision,
                    "fencing_token": run.fencing_token,
                    "failure": failure,
                }
                if current.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
                    if restored:
                        return (await workflow.fail(FailRunCommand.model_validate(command))).run
                    return (await workflow.cleanup(CleanupRunCommand.model_validate(command))).run
                return current
        finally:
            await heartbeat.close()

    async def _reject(self, repository: RunStore, run: RotationRun, owner: str) -> RotationRun:
        if run.status is not RunStatus.PENDING:
            return (
                await RunWorkflow(repository).cleanup(
                    CleanupRunCommand(
                        id=stable("mapping", run.id + ":" + str(run.revision)),
                        organisation_id=run.organisation_id,
                        run_id=run.id,
                        actor_id=owner,
                        expected_revision=run.revision,
                        fencing_token=run.fencing_token,
                        failure=Failure(
                            code="rotation-mapping-changed",
                            message="Restore the pinned credential mappings before recovery.",
                            retryable=False,
                        ),
                    )
                )
            ).run
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
        if not run.current_generation_id:
            event = await self.database.collection("inventory").find_one(
                {
                    "organisation_id": run.organisation_id,
                    "kind": "ingestion-event",
                    "id": run.trigger.event_id,
                }
            )
            if event and event["data"]["resource"].get("generation_id") not in {
                None,
                state.current.id,
            }:
                raise ResourceConflictError("The incident refers to a previous key generation")
        current = await RunStore(self.database).get(run.organisation_id, run.id)
        if (
            current.fencing_token != run.fencing_token
            or current.stage is not run.stage
            or current.status is not RunStatus.RUNNING
            or not current.lease
            or current.lease.expires_at <= datetime.now(UTC)
        ):
            raise LeaseConflictError("rotation lease or revision changed")
        if await self.loader(run.organisation_id, run.credential_id, run.control_version) != state:
            raise ResourceConflictError("Credential mapping or connections changed during the run")
