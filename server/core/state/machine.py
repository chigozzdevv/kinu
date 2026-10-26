from datetime import datetime

from contracts import (
    Failure,
    Lease,
    RecoveryMode,
    RotationRun,
    RunStatus,
    Stage,
    StageBindings,
    StageProof,
)
from contracts.state import STAGES
from policy import GatePolicy

from core.errors import LeaseConflictError, RevisionConflictError, TransitionRejectedError


class RotationMachine:
    def __init__(
        self,
        policy: GatePolicy | None = None,
        *,
        validate_policy: bool = True,
    ) -> None:
        self._policy = policy or GatePolicy()
        self._validate_policy = validate_policy

    def start(
        self,
        run: RotationRun,
        owner_id: str,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._revision(run, expected_revision)
        if run.status is not RunStatus.PENDING:
            raise TransitionRejectedError("only a pending run can start")
        self._future(expires_at, now)
        token = run.fencing_token + 1
        lease = Lease(owner_id=owner_id, fencing_token=token, expires_at=expires_at)
        return self._update(
            run,
            now,
            status=RunStatus.RUNNING,
            lease=lease,
            fencing_token=token,
        )

    def renew(
        self,
        run: RotationRun,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.lease is None or run.lease.owner_id != owner_id:
            raise LeaseConflictError("only the lease owner can renew it")
        self._future(expires_at, now)
        lease = run.lease.model_copy(update={"expires_at": expires_at})
        return self._update(run, now, lease=lease)

    def complete(
        self,
        run: RotationRun,
        proof: StageProof,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
        bindings: StageBindings | None = None,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.status is not RunStatus.RUNNING:
            raise TransitionRejectedError("only normally running work can advance")
        if proof.run_id != run.id or proof.organisation_id != run.organisation_id:
            raise TransitionRejectedError("proof belongs to a different run")
        if proof.stage is not run.stage:
            raise TransitionRejectedError("proof does not match the current stage")
        if self._validate_policy:
            self._policy.validate(proof)
        changes = self._bindings(run, bindings or StageBindings())

        if run.stage is Stage.COMPLETE:
            return self._update(
                run,
                now,
                status=RunStatus.COMPLETED,
                lease=None,
                **changes,
            )

        index = STAGES.index(run.stage)
        return self._update(
            run,
            now,
            stage=STAGES[index + 1],
            status=RunStatus.RUNNING,
            **changes,
        )

    @staticmethod
    def _bindings(run: RotationRun, bindings: StageBindings) -> dict[str, object]:
        values = bindings.model_dump(exclude_none=True, exclude_defaults=True)
        allowed: dict[Stage, frozenset[str]] = {
            Stage.PREFLIGHT: frozenset({"browser_playbook_version", "current_generation_id"}),
            Stage.PLAN: frozenset({"plan_id", "plan_hash"}),
            Stage.CREATE: frozenset({"target_generation_id"}),
            Stage.DEPLOY: frozenset({"deployments"}),
        }
        unexpected = set(values).difference(allowed.get(run.stage, frozenset()))
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise TransitionRejectedError(
                f"stage {run.stage.value} cannot bind run fields: {names}"
            )
        if run.stage is Stage.PREFLIGHT and "current_generation_id" not in values:
            raise TransitionRejectedError("preflight must bind the current generation")
        if run.stage is Stage.PLAN and set(values) != allowed[Stage.PLAN]:
            raise TransitionRejectedError("plan stage must bind plan identity and digest")
        if run.stage is Stage.CREATE and set(values) != allowed[Stage.CREATE]:
            raise TransitionRejectedError("create stage must bind the target generation")
        if run.stage is Stage.DEPLOY and set(values) != allowed[Stage.DEPLOY]:
            raise TransitionRejectedError("deploy stage must bind runtime deployment identities")
        immutable = {
            name: value
            for name, value in values.items()
            if getattr(run, name) not in {None, ()} and getattr(run, name) != value
        }
        if immutable:
            raise TransitionRejectedError("run bindings cannot change after they are set")
        return values

    def pause(
        self,
        run: RotationRun,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.status not in {RunStatus.RUNNING, RunStatus.RECOVERING}:
            raise TransitionRejectedError("only active work can pause")
        return self._update(run, now, status=RunStatus.PAUSED)

    def resume(
        self,
        run: RotationRun,
        owner_id: str,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._revision(run, expected_revision)
        if run.status is not RunStatus.PAUSED:
            raise TransitionRejectedError("only a paused run can resume")
        if run.lease and run.lease.expires_at > now and run.lease.owner_id != owner_id:
            raise LeaseConflictError("an active lease belongs to another worker")
        self._future(expires_at, now)
        token = run.fencing_token + 1
        lease = Lease(owner_id=owner_id, fencing_token=token, expires_at=expires_at)
        return self._update(
            run,
            now,
            status=RunStatus.RUNNING,
            lease=lease,
            fencing_token=token,
        )

    def cleanup(
        self,
        run: RotationRun,
        failure: Failure,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._interrupt_control(run, fencing_token, expected_revision, now)
        if run.status not in {RunStatus.RUNNING, RunStatus.RECOVERING, RunStatus.PAUSED}:
            raise TransitionRejectedError("terminal work cannot enter cleanup")
        return self._update(run, now, status=RunStatus.CLEANUP, failure=failure)

    def fail(
        self,
        run: RotationRun,
        failure: Failure,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.COMPENSATED}:
            raise TransitionRejectedError("terminal work cannot fail again")
        self._interrupt_control(run, fencing_token, expected_revision, now)
        changes: dict[str, object] = {
            "status": RunStatus.FAILED,
            "failure": failure,
            "lease": None,
        }
        if run.status is RunStatus.RECOVERING:
            changes.update(
                {
                    "recovery_id": None,
                    "recovery_stage": None,
                    "recovery_mode": None,
                    "recovery_failure": None,
                    "recovery_evidence_ids": (),
                }
            )
        return self._update(run, now, **changes)

    def cancel(
        self,
        run: RotationRun,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._revision(run, expected_revision)
        if run.status in {
            RunStatus.CANCELLED,
            RunStatus.COMPLETED,
            RunStatus.COMPENSATED,
        }:
            raise TransitionRejectedError("terminal work cannot be cancelled")
        return self._update(
            run,
            now,
            status=RunStatus.CANCELLED,
            lease=None,
            fencing_token=run.fencing_token + 1,
            failure=None,
            recovery_id=None,
            recovery_stage=None,
            recovery_mode=None,
            recovery_failure=None,
            recovery_evidence_ids=(),
        )

    def recover(
        self,
        run: RotationRun,
        owner_id: str,
        expected_revision: int,
        expires_at: datetime,
        now: datetime,
    ) -> RotationRun:
        self._revision(run, expected_revision)
        if run.status not in {RunStatus.FAILED, RunStatus.CLEANUP}:
            raise TransitionRejectedError("only interrupted work can recover")
        if run.lease and run.lease.expires_at > now and run.lease.owner_id != owner_id:
            raise LeaseConflictError("an active lease belongs to another worker")
        self._future(expires_at, now)
        token = run.fencing_token + 1
        lease = Lease(owner_id=owner_id, fencing_token=token, expires_at=expires_at)
        return self._update(
            run,
            now,
            status=RunStatus.RECOVERING,
            failure=None,
            recovery_stage=run.stage,
            recovery_failure=run.failure,
            lease=lease,
            fencing_token=token,
        )

    def complete_recovery(
        self,
        run: RotationRun,
        recovery_id: str,
        mode: RecoveryMode,
        evidence_ids: tuple[str, ...],
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> RotationRun:
        self._control(run, fencing_token, expected_revision, now)
        if run.status is not RunStatus.RECOVERING or run.recovery_stage is not run.stage:
            raise TransitionRejectedError("only an active bound recovery can complete")
        if not evidence_ids:
            raise TransitionRejectedError("recovery completion requires evidence")
        if mode is RecoveryMode.RETRY:
            if run.recovery_failure is None or not run.recovery_failure.retryable:
                raise TransitionRejectedError("a non-retryable failure cannot re-enter its stage")
            return self._update(
                run,
                now,
                status=RunStatus.RUNNING,
                recovery_id=None,
                recovery_stage=None,
                recovery_mode=None,
                recovery_failure=None,
                recovery_evidence_ids=(),
            )
        return self._update(
            run,
            now,
            status=RunStatus.COMPENSATED,
            lease=None,
            recovery_id=recovery_id,
            recovery_mode=mode,
            recovery_evidence_ids=evidence_ids,
        )

    @staticmethod
    def _revision(run: RotationRun, expected: int) -> None:
        if run.revision != expected:
            raise RevisionConflictError(f"expected revision {expected}, found {run.revision}")

    def _control(
        self,
        run: RotationRun,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> None:
        self._revision(run, expected_revision)
        if run.lease is None:
            raise LeaseConflictError("run has no active lease")
        if run.lease.fencing_token != fencing_token:
            raise LeaseConflictError("stale fencing token")
        if run.lease.expires_at <= now:
            raise LeaseConflictError("lease has expired")

    def _interrupt_control(
        self,
        run: RotationRun,
        fencing_token: int,
        expected_revision: int,
        now: datetime,
    ) -> None:
        self._revision(run, expected_revision)
        if run.lease is None:
            raise LeaseConflictError("run has no active lease")
        if run.lease.expires_at <= now:
            return
        if run.lease.fencing_token != fencing_token:
            raise LeaseConflictError("stale fencing token")

    @staticmethod
    def _future(expires_at: datetime, now: datetime) -> None:
        if expires_at <= now:
            raise LeaseConflictError("lease expiry must be in the future")

    @staticmethod
    def _update(run: RotationRun, now: datetime, **changes: object) -> RotationRun:
        payload = run.model_dump()
        payload.update(changes)
        payload["updated_at"] = now
        payload["revision"] = run.revision + 1
        return RotationRun.model_validate(payload)
