from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    Failure,
    RecoveryMode,
    RotationRun,
    RunStatus,
    RuntimeDeployment,
    Stage,
    StageBindings,
    StageProof,
)
from core.errors import LeaseConflictError, RevisionConflictError, TransitionRejectedError
from core.state import RotationMachine
from policy import GatePolicy, PolicyViolationError
from testkit import make_proof, make_run

NOW = datetime.now(UTC)
LEASE_END = NOW + timedelta(minutes=5)


def start() -> tuple[RotationMachine, RotationRun]:
    machine = RotationMachine()
    run = machine.start(make_run(NOW), "worker_one", 0, LEASE_END, NOW)
    return (machine, run)


def test_start_assigns_first_fencing_token() -> None:
    machine = RotationMachine()
    run = machine.start(make_run(NOW), "worker_one", 0, LEASE_END, NOW)
    assert run.status is RunStatus.RUNNING
    assert run.lease is not None
    assert run.lease.fencing_token == 1
    assert run.revision == 1


def test_start_rejects_stale_revision() -> None:
    with pytest.raises(RevisionConflictError):
        RotationMachine().start(make_run(NOW), "worker_one", 3, LEASE_END, NOW)


def test_stage_rejects_stale_fencing_token() -> None:
    machine, run = start()
    with pytest.raises(LeaseConflictError, match="stale"):
        machine.complete(run, make_proof(Stage.TRIGGER, NOW), 9, run.revision, NOW)


def test_stage_rejects_proof_for_another_stage() -> None:
    machine, run = start()
    with pytest.raises(TransitionRejectedError, match="current stage"):
        machine.complete(run, make_proof(Stage.PREFLIGHT, NOW), 1, run.revision, NOW)


def test_stage_rejects_incomplete_evidence() -> None:
    machine, run = start()
    proof = StageProof(
        run_id=run.id,
        organisation_id=run.organisation_id,
        stage=Stage.TRIGGER,
        checks=frozenset({"request-authenticated"}),
        evidence_ids=("evidence_one",),
        actor_id="service_one",
        recorded_at=NOW,
    )
    with pytest.raises(PolicyViolationError):
        machine.complete(run, proof, 1, run.revision, NOW)


def test_stage_can_delegate_policy_validation_to_repository() -> None:
    machine = RotationMachine(validate_policy=False)
    run = machine.start(make_run(NOW), "worker_one", 0, LEASE_END, NOW)
    proof = StageProof(
        run_id=run.id,
        organisation_id=run.organisation_id,
        stage=Stage.TRIGGER,
        checks=frozenset({"request-authenticated"}),
        evidence_ids=("evidence_one",),
        actor_id="service_one",
        recorded_at=NOW,
    )
    advanced = machine.complete(run, proof, 1, run.revision, NOW)
    assert advanced.stage is Stage.PREFLIGHT


def test_run_requires_every_stage_before_completion() -> None:
    machine, run = start()
    for stage in Stage:
        assert run.stage is stage
        assert run.status is RunStatus.RUNNING
        assert run.lease is not None
        run = machine.complete(
            run, make_proof(stage, NOW), run.lease.fencing_token, run.revision, NOW, bindings(stage)
        )
    assert run.stage is Stage.COMPLETE
    assert run.status is RunStatus.COMPLETED
    assert run.lease is None
    assert run.revision == 13


def test_resume_invalidates_queued_work() -> None:
    machine, run = start()
    run = machine.pause(run, 1, run.revision, NOW)
    run = machine.resume(run, "worker_one", run.revision, LEASE_END, NOW)
    assert run.lease is not None
    assert run.lease.fencing_token == 2
    with pytest.raises(LeaseConflictError, match="stale"):
        machine.complete(run, make_proof(Stage.TRIGGER, NOW), 1, run.revision, NOW)


def test_cancel_releases_the_lease_and_invalidates_queued_work() -> None:
    machine, run = start()
    cancelled = machine.cancel(run, run.revision, NOW)
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.lease is None
    assert cancelled.fencing_token == 2
    with pytest.raises(TransitionRejectedError, match="terminal"):
        machine.cancel(cancelled, cancelled.revision, NOW)


def test_cancel_can_abandon_a_failed_run() -> None:
    machine, run = start()
    failed = machine.fail(
        run,
        Failure(
            code="provisioning-failed", message="Browser provisioning failed.", retryable=False
        ),
        run.fencing_token,
        run.revision,
        NOW,
    )
    cancelled = machine.cancel(failed, failed.revision, NOW)
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.failure is None
    assert cancelled.lease is None


def test_cleanup_can_recover_under_a_new_fence() -> None:
    machine, run = start()
    failure = Failure(
        code="provider-response-lost", message="creation result is ambiguous", retryable=False
    )
    run = machine.cleanup(run, failure, 1, run.revision, NOW)
    run = machine.recover(
        run, "recovery_one", run.revision, LEASE_END + timedelta(minutes=5), LEASE_END
    )
    assert run.status is RunStatus.RECOVERING
    assert run.failure is None
    assert run.lease is not None
    assert run.lease.fencing_token == 2


def test_failed_run_recovery_preserves_fence_history() -> None:
    machine, run = start()
    failure = Failure(
        code="candidate-failed", message="candidate verification failed", retryable=True
    )
    run = machine.fail(run, failure, 1, run.revision, NOW)
    assert run.lease is None
    assert run.fencing_token == 1
    run = machine.recover(run, "recovery_one", run.revision, LEASE_END, NOW)
    assert run.lease is not None
    assert run.lease.fencing_token == 2
    assert run.fencing_token == 2


def test_retryable_recovery_reenters_the_same_stage() -> None:
    machine, run = start()
    failure = Failure(code="temporary-error", message="provider was unavailable", retryable=True)
    run = machine.fail(run, failure, 1, run.revision, NOW)
    run = machine.recover(run, "worker_one", run.revision, LEASE_END, NOW)
    assert run.lease is not None
    run = machine.complete_recovery(
        run,
        "recovery_one",
        RecoveryMode.RETRY,
        ("evidence_one",),
        run.lease.fencing_token,
        run.revision,
        NOW,
    )
    assert run.status is RunStatus.RUNNING
    assert run.stage is Stage.TRIGGER
    assert run.recovery_stage is None
    assert run.recovery_evidence_ids == ()


def test_recovering_run_cannot_advance_without_completing_recovery() -> None:
    machine, run = start()
    failure = Failure(code="temporary-error", message="provider was unavailable", retryable=True)
    run = machine.fail(run, failure, 1, run.revision, NOW)
    run = machine.recover(run, "worker_one", run.revision, LEASE_END, NOW)
    assert run.lease is not None
    with pytest.raises(TransitionRejectedError, match="normally running"):
        machine.complete(
            run, make_proof(Stage.TRIGGER, NOW), run.lease.fencing_token, run.revision, NOW
        )


def test_expired_active_run_can_enter_cleanup_under_a_new_worker() -> None:
    machine, run = start()
    failure = Failure(code="lease-expired", message="worker stopped", retryable=True)
    interrupted = machine.cleanup(
        run, failure, fencing_token=999, expected_revision=run.revision, now=LEASE_END
    )
    assert interrupted.status is RunStatus.CLEANUP
    assert interrupted.failure == failure


def test_expired_active_run_can_fail_and_release_its_lease() -> None:
    machine, run = start()
    failure = Failure(code="lease-expired", message="worker stopped", retryable=True)
    interrupted = machine.fail(
        run, failure, fencing_token=999, expected_revision=run.revision, now=LEASE_END
    )
    assert interrupted.status is RunStatus.FAILED
    assert interrupted.lease is None


def test_rollback_recovery_finishes_as_compensated() -> None:
    machine, run = start()
    failure = Failure(
        code="candidate-failed", message="candidate verification failed", retryable=False
    )
    run = machine.cleanup(run, failure, 1, run.revision, NOW)
    run = machine.recover(run, "worker_one", run.revision, LEASE_END, NOW)
    assert run.lease is not None
    run = machine.complete_recovery(
        run,
        "recovery_one",
        RecoveryMode.ROLLBACK,
        ("evidence_one",),
        run.lease.fencing_token,
        run.revision,
        NOW,
    )
    assert run.status is RunStatus.COMPENSATED
    assert run.lease is None
    assert run.recovery_stage is Stage.TRIGGER
    assert run.recovery_mode is RecoveryMode.ROLLBACK
    assert run.recovery_evidence_ids == ("evidence_one",)
    with pytest.raises(TransitionRejectedError, match="terminal"):
        machine.fail(run, failure, fencing_token=1, expected_revision=run.revision, now=NOW)


def test_gate_policy_covers_all_twelve_stages() -> None:
    policy = GatePolicy()
    assert {stage for stage in Stage if policy.checks(stage)} == set(Stage)


def bindings(stage: Stage) -> StageBindings:
    if stage is Stage.PREFLIGHT:
        return StageBindings(current_generation_id="generation_old")
    if stage is Stage.PLAN:
        return StageBindings(plan_id="plan_one", plan_hash="a" * 64)
    if stage is Stage.CREATE:
        return StageBindings(target_generation_id="generation_new")
    if stage is Stage.DEPLOY:
        return StageBindings(
            deployments=(
                RuntimeDeployment(
                    binding_id="binding_one",
                    connection_id="runtime_one",
                    service="service_one",
                    candidate_revision="revision_new",
                    rollback_revision="revision_old",
                ),
            )
        )
    return StageBindings()
