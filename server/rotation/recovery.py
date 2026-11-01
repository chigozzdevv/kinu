from datetime import UTC, datetime, timedelta

from connectors.http import HttpProviderConnector
from contracts import (
    CleanupRunCommand,
    CompleteRecoveryCommand,
    CredentialGeneration,
    Failure,
    RecoverRunCommand,
    RecoveryMode,
    RotationRun,
    RunStatus,
    Stage,
    StageExecutionResult,
    StageExecutionStatus,
)
from core.errors import IdempotencyConflictError, ResourceConflictError
from core.ids import new_id
from core.storage.database import Database
from core.storage.records import Records
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from jobs.queue import Jobs
from pymongo.errors import DuplicateKeyError

from rotation.generations import discard, promote
from rotation.guard import validate
from rotation.lease import Heartbeat
from rotation.rotate import Rotation
from rotation.stages import stable


class Recovery:
    def __init__(
        self, database: Database, rotation: Rotation, queue: Jobs, provider: HttpProviderConnector
    ) -> None:
        self.database, self.rotation, self.queue, self.provider = (
            database,
            rotation,
            queue,
            provider,
        )
        self.requests = database.collection("recovery_requests")

    async def request(
        self, org: str, run_id: str, revision: int, actor: str, command: str
    ) -> RotationRun:
        repository = RunStore(self.database)
        existing = await self.requests.find_one({"organisation_id": org, "id": command})
        run = await repository.get(org, run_id)
        if not existing:
            if run.status is not RunStatus.CLEANUP or run.revision != revision:
                raise ResourceConflictError("Refresh the cleanup-required rotation before recovery")
            expires = datetime.now(UTC) + timedelta(minutes=14)
            existing = {
                "organisation_id": org,
                "run_id": run_id,
                "id": command,
                "actor_id": actor,
                "revision": revision,
                "owner_id": stable("recovery", command),
                "fencing_token": run.fencing_token + 1,
                "expires_at": expires.isoformat(),
            }
            try:
                await self.requests.insert_one(existing)
            except DuplicateKeyError:
                return await self.request(org, run_id, revision, actor, command)
        if (existing["run_id"], existing["actor_id"], existing["revision"]) != (
            run_id,
            actor,
            revision,
        ):
            raise IdempotencyConflictError("Recovery request already identifies another action")
        await self.requested(existing)
        if run.status is not RunStatus.CLEANUP or run.revision != revision:
            if (
                run.status is RunStatus.RECOVERING
                and run.lease
                and (run.lease.owner_id == existing["owner_id"])
            ):
                await self.enqueue(run)
            return run
        recovered = (
            await RunWorkflow(repository).recover(
                RecoverRunCommand(
                    id=command,
                    organisation_id=org,
                    run_id=run_id,
                    actor_id=actor,
                    owner_id=existing["owner_id"],
                    expected_revision=revision,
                    expires_at=datetime.fromisoformat(existing["expires_at"]),
                )
            )
        ).run
        await self.enqueue(recovered)
        return recovered

    async def requested(self, request: dict[str, object]) -> None:
        await self.rotation.audit.append(
            stable("recovery-requested", str(request["id"])),
            str(request["organisation_id"]),
            "rotation.recovery-requested",
            str(request["actor_id"]),
            "runs/" + str(request["run_id"]),
            {"request_id": str(request["id"])},
            run_id=str(request["run_id"]),
            occurred_at=datetime.fromisoformat(str(request["expires_at"])) - timedelta(minutes=14),
        )

    async def enqueue(self, run: RotationRun) -> None:
        await self.queue.add(
            "recovery",
            {"organisation_id": run.organisation_id, "run_id": run.id},
            f"recovery-{run.organisation_id}-{run.id}-{run.fencing_token}",
            recover=True,
        )

    async def execute(self, org: str, run_id: str) -> RotationRun:
        repository = RunStore(self.database)
        workflow = RunWorkflow(repository)
        run = await repository.get(org, run_id)
        if run.status is not RunStatus.RECOVERING or not run.lease:
            return run
        request = await self.requests.find_one(
            {
                "organisation_id": org,
                "run_id": run_id,
                "owner_id": run.lease.owner_id,
                "fencing_token": run.fencing_token,
            }
        )
        if not request:
            raise ResourceConflictError("Recovery has no matching authorized request")
        await self.requested(request)
        if run.lease.expires_at <= datetime.now(UTC):
            return (
                await workflow.cleanup(
                    CleanupRunCommand(
                        id=stable("recovery-expired", f"{run.id}:{run.fencing_token}"),
                        organisation_id=org,
                        run_id=run_id,
                        actor_id=request["actor_id"],
                        expected_revision=run.revision,
                        fencing_token=run.fencing_token,
                        failure=Failure(
                            code="recovery-lease-expired",
                            message=(
                                "Recovery stopped without a final outcome. Check and recover again."
                            ),
                            retryable=False,
                        ),
                    )
                )
            ).run
        heartbeat = Heartbeat(repository, workflow, run)
        heartbeat.start()
        started = datetime.now(UTC)
        try:
            state = await self.rotation.loader(org, run.credential_id, run.control_version)
            fence = {"run_id": run.id, "fencing_token": run.fencing_token}
            await validate(self.database, state, fence)
            journal = self.rotation.journal
            created = await journal.get(org, run_id, Stage.CREATE)
            retired = await journal.get(org, run_id, Stage.REVOKE)
            deployed = await journal.get(org, run_id, Stage.DEPLOY)
            completed = bool(
                retired
                and retired.status is StageExecutionStatus.SUCCEEDED
                and all(
                    retired.output.get(check) is True
                    for check in ("old_rejected", "replacement_valid", "old_secret_retired")
                )
            )
            if completed:
                if (
                    not created
                    or not deployed
                    or not run.target_generation_id
                    or not run.current_generation_id
                ):
                    raise ResourceConflictError(
                        "Retirement evidence has incomplete generation bindings"
                    )
                async with Records(self.database).read() as rows:
                    predecessor = await rows.get(
                        CredentialGeneration, org, run.current_generation_id
                    )
                matches = [
                    item
                    for item in await self.provider.metadata(state.provider)
                    if item["provider_id"] == predecessor.provider_id
                ]
                if matches and not (
                    len(matches) == 1
                    and (
                        matches[0].get("disabled") is True
                        or matches[0].get("status")
                        in {"disabled", "revoked", "inactive", "deleted"}
                    )
                ):
                    raise ResourceConflictError(
                        "The provider no longer confirms predecessor retirement"
                    )
                await self.rotation.effects.verify_current(state, created.output, deployed.output)
                verified = await journal.get(org, run_id, Stage.VERIFY)
                if not verified or verified.status is not StageExecutionStatus.SUCCEEDED:
                    raise ResourceConflictError("Recovery requires saved replacement verification")
                if state.credential.active_generation_id != run.target_generation_id:
                    await validate(self.database, state, fence)
                    await promote(self.database, state, run.target_generation_id, verified.id)
                mode = RecoveryMode.ROLLFORWARD
                checks = frozenset({"replacement-valid", "old-rejected", "generation-recorded"})
            else:
                if RecoveryMode.ROLLBACK not in state.controls.definition.allowed_recovery_modes:
                    raise ResourceConflictError("The pinned controls do not permit rollback")
                if created and created.status is StageExecutionStatus.SUCCEEDED:
                    restored = await self.rotation.effects.rollback(
                        state,
                        created.output | {"run_id": run.id, "fencing_token": run.fencing_token},
                        deployed.output if deployed else None,
                    )
                    if restored:
                        await discard(self.database, org, stable("generation", run.id))
                elif run.stage in {Stage.TRIGGER, Stage.PREFLIGHT, Stage.PLAN}:
                    await self.rotation.effects.initial(state)
                    restored = True
                else:
                    restored = await self.rotation.effects.cleanup_unrecorded(state, run_id)
                if not restored:
                    raise ResourceConflictError(
                        "A provider action or deployment is still unresolved; "
                        "review it before retrying"
                    )
                mode = RecoveryMode.ROLLBACK
                checks = frozenset({"predecessor-restored", "replacement-retired"})
            heartbeat.check()
            evidence = StageExecutionResult(
                id=new_id("recoveryreview"),
                organisation_id=org,
                run_id=run_id,
                stage=run.stage,
                status=StageExecutionStatus.RECOVERED,
                recovery_mode=mode,
                checks=checks,
                output={"mode": mode.value},
                evidence_ids=(request["id"],),
                started_at=started,
                completed_at=datetime.now(UTC),
            )
            await journal.review(evidence)
            await self.rotation.audit.append(
                evidence.id,
                org,
                "rotation.recovery-verified",
                request["actor_id"],
                "runs/" + run_id,
                {"mode": mode.value, "request_id": request["id"]},
                run_id=run_id,
                evidence_ids=(evidence.id,),
                occurred_at=evidence.completed_at,
            )
            async with heartbeat.lock:
                current = await repository.get(org, run_id)
                return (
                    await workflow.complete_recovery(
                        CompleteRecoveryCommand(
                            id=stable("recovered", request["id"]),
                            organisation_id=org,
                            run_id=run_id,
                            actor_id=request["actor_id"],
                            expected_revision=current.revision,
                            fencing_token=run.fencing_token,
                            recovery_id=request["id"],
                            mode=mode,
                            evidence_ids=(evidence.id,),
                        )
                    )
                ).run
        except Exception as error:
            async with heartbeat.lock:
                current = await repository.get(org, run_id)
                if (
                    current.status is not RunStatus.RECOVERING
                    or current.fencing_token != run.fencing_token
                ):
                    return current
                return (
                    await workflow.cleanup(
                        CleanupRunCommand(
                            id=stable("recovery-held", request["id"]),
                            organisation_id=org,
                            run_id=run_id,
                            actor_id=request["actor_id"],
                            expected_revision=current.revision,
                            fencing_token=run.fencing_token,
                            failure=Failure(
                                code="recovery-review-required",
                                message=str(error)
                                if isinstance(error, ResourceConflictError)
                                else (
                                    "Recovery could not verify a safe outcome. Review the saved "
                                    "checks and reconnect expired accounts before retrying."
                                ),
                                retryable=False,
                            ),
                        )
                    )
                ).run
        finally:
            await heartbeat.close()
