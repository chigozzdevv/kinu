from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    CancelRunCommand,
    CompleteStageCommand,
    CreateRunCommand,
    EventKind,
    FailRunCommand,
    Failure,
    RunStatus,
    RuntimeDeployment,
    Stage,
    StageBindings,
    StartRunCommand,
    Trigger,
)
from core.errors import ActiveRunConflictError, IdempotencyConflictError
from core.workflow import RunWorkflow
from testkit import MemoryRunRepository, make_proof

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class IdSequence:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}_{self._value}"


def create_command(
    command_id: str = "command_create", event_id: str = "event-one", credential_id: str = "cred_one"
) -> CreateRunCommand:
    return CreateRunCommand(
        id=command_id,
        organisation_id="org_one",
        credential_id=credential_id,
        control_version="policy_one",
        trigger=Trigger(
            source="schedule",
            event_id=event_id,
            actor_id="service_one",
            reason="routine rotation",
            urgency="routine",
            received_at=NOW,
        ),
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_is_deduplicated_by_source_event() -> None:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: NOW, id_factory=IdSequence())
    command = create_command()
    first = await workflow.create(command)
    duplicate = await workflow.create(command.model_copy(update={"id": "command_retry"}))
    assert first.applied is True
    assert duplicate.applied is False
    assert duplicate.run.id == first.run.id
    assert len(repository.steps) == 1
    assert len(repository.events) == 1


@pytest.mark.anyio
async def test_credential_allows_only_one_active_run() -> None:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: NOW, id_factory=IdSequence())
    await workflow.create(create_command())
    with pytest.raises(ActiveRunConflictError, match="already owned"):
        await workflow.create(create_command(command_id="command_other", event_id="event-two"))


@pytest.mark.anyio
async def test_mutation_retry_cannot_change_command_content() -> None:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: NOW, id_factory=IdSequence())
    created = await workflow.create(create_command())
    command = StartRunCommand(
        id="command_start",
        organisation_id="org_one",
        run_id=created.run.id,
        actor_id="service_one",
        expected_revision=0,
        owner_id="worker_one",
        expires_at=NOW + timedelta(hours=1),
    )
    first = await workflow.start(command)
    duplicate = await workflow.start(command)
    assert first.applied is True
    assert duplicate.applied is False
    assert duplicate.run.revision == 1
    changed = command.model_copy(update={"owner_id": "worker_two"})
    with pytest.raises(IdempotencyConflictError, match="another mutation"):
        await workflow.start(changed)


@pytest.mark.anyio
async def test_complete_flow_releases_credential_lock() -> None:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: NOW, id_factory=IdSequence())
    result = await workflow.create(create_command())
    result = await workflow.start(
        StartRunCommand(
            id="command_start",
            organisation_id="org_one",
            run_id=result.run.id,
            actor_id="service_one",
            expected_revision=result.run.revision,
            owner_id="worker_one",
            expires_at=NOW + timedelta(hours=1),
        )
    )
    for index, stage in enumerate(Stage):
        result = await workflow.complete(
            CompleteStageCommand(
                id=f"command_stage_{index}",
                organisation_id="org_one",
                run_id=result.run.id,
                actor_id="service_one",
                expected_revision=result.run.revision,
                fencing_token=result.run.fencing_token,
                proof=make_proof(stage, NOW).model_copy(update={"run_id": result.run.id}),
                bindings=bindings(stage),
            )
        )
    assert result.run.status is RunStatus.COMPLETED
    assert result.run.revision == 13
    assert repository.events[-1].event.kind is EventKind.RUN_COMPLETED
    next_run = await workflow.create(
        create_command(command_id="command_next", event_id="event-two")
    )
    assert next_run.applied is True
    assert next_run.run.id != result.run.id


@pytest.mark.anyio
async def test_cancel_releases_credential_lock() -> None:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: NOW, id_factory=IdSequence())
    created = await workflow.create(create_command())
    cancelled = await workflow.cancel(
        CancelRunCommand(
            id="command_cancel",
            organisation_id="org_one",
            run_id=created.run.id,
            actor_id="service_one",
            expected_revision=created.run.revision,
        )
    )
    next_run = await workflow.create(
        create_command(command_id="command_next", event_id="event-two")
    )
    assert cancelled.run.status is RunStatus.CANCELLED
    assert repository.events[-2].event.kind is EventKind.RUN_CANCELLED
    assert next_run.applied is True


@pytest.mark.anyio
async def test_fail_releases_credential_lock() -> None:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: NOW, id_factory=IdSequence())
    created = await workflow.create(create_command())
    started = await workflow.start(
        StartRunCommand(
            id="command_start",
            organisation_id="org_one",
            run_id=created.run.id,
            actor_id="service_one",
            expected_revision=created.run.revision,
            owner_id="worker_one",
            expires_at=NOW + timedelta(hours=1),
        )
    )
    failed = await workflow.fail(
        FailRunCommand(
            id="command_fail",
            organisation_id="org_one",
            run_id=started.run.id,
            actor_id="service_one",
            expected_revision=started.run.revision,
            fencing_token=started.run.fencing_token,
            failure=Failure(code="test-failure", message="Test failure.", retryable=False),
        )
    )
    next_run = await workflow.create(
        create_command(command_id="command_next", event_id="event-two")
    )
    assert failed.run.status is RunStatus.FAILED
    assert next_run.applied is True


@pytest.mark.anyio
async def test_reaper_fails_an_expired_run_before_a_recovery_plan_exists() -> None:
    current = [NOW]
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: current[0], id_factory=IdSequence())
    result = await workflow.create(create_command())
    await workflow.start(
        StartRunCommand(
            id="command_start",
            organisation_id="org_one",
            run_id=result.run.id,
            actor_id="service_one",
            expected_revision=result.run.revision,
            owner_id="worker_one",
            expires_at=NOW + timedelta(minutes=1),
        )
    )
    current[0] = NOW + timedelta(minutes=2)
    reaped = await workflow.reap_expired("org_one", "automation_one")
    assert reaped.scanned == 1
    assert reaped.failed == 1
    assert reaped.restarted == 0
    assert reaped.runs[0].status is RunStatus.FAILED
    assert reaped.runs[0].failure is not None
    assert reaped.runs[0].failure.code == "stage-lease-expired"


@pytest.mark.anyio
async def test_reaper_restarts_recoverable_work_and_terminalises_stale_recovery() -> None:
    current = [NOW]
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository, clock=lambda: current[0], id_factory=IdSequence())
    result = await workflow.create(create_command())
    result = await workflow.start(
        StartRunCommand(
            id="command_start",
            organisation_id="org_one",
            run_id=result.run.id,
            actor_id="service_one",
            expected_revision=result.run.revision,
            owner_id="worker_one",
            expires_at=NOW + timedelta(minutes=1),
        )
    )
    for index, stage in enumerate(
        (Stage.TRIGGER, Stage.PREFLIGHT, Stage.PLAN, Stage.CREATE, Stage.STORE)
    ):
        result = await workflow.complete(
            CompleteStageCommand(
                id=f"command_prepare_{index}",
                organisation_id="org_one",
                run_id=result.run.id,
                actor_id="service_one",
                expected_revision=result.run.revision,
                fencing_token=result.run.fencing_token,
                proof=make_proof(stage, NOW).model_copy(update={"run_id": result.run.id}),
                bindings=bindings(stage),
            )
        )
    assert result.run.stage is Stage.DEPLOY
    current[0] = NOW + timedelta(minutes=2)
    restarted = await workflow.reap_expired("org_one", "automation_one")
    assert restarted.scanned == 1
    assert restarted.restarted == 1
    assert restarted.runs[0].status is RunStatus.RECOVERING
    assert restarted.runs[0].recovery_stage is Stage.DEPLOY
    assert restarted.runs[0].recovery_failure is not None
    assert restarted.runs[0].recovery_failure.code == "stage-lease-expired"
    current[0] = NOW + timedelta(minutes=33)
    failed = await workflow.reap_expired("org_one", "automation_one")
    assert failed.failed == 1
    assert failed.runs[0].status is RunStatus.FAILED
    assert failed.runs[0].failure is not None
    assert failed.runs[0].failure.code == "recovery-lease-expired"


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
