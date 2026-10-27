import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from contracts import CancelRunCommand, CreateRunCommand, RunStatus, StartRunCommand, Trigger
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.errors import ActiveRunConflictError, IdempotencyConflictError, RunNotFoundError
from core.storage.database import Database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 27, tzinfo=UTC)


async def setup(
    database: Database, identity: AuthenticatedIdentity
) -> tuple[RunWorkflow, CreateRunCommand]:
    workspace = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "Rotation")
    command = CreateRunCommand(
        id="create_one",
        organisation_id=workspace.organisation.id,
        credential_id="credential_one",
        control_version="controls_one",
        trigger=Trigger(
            source="email",
            event_id="message_one",
            actor_id="operator_one",
            reason="Credential exposure alert",
            urgency="high",
            received_at=NOW,
        ),
    )
    return RunWorkflow(RunStore(database), clock=lambda: NOW), command


async def test_concurrent_delivery_deduplicates_and_run_survives_repository_restart(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workflow, command = await setup(database, identity)
    results = await asyncio.gather(*(workflow.create(command) for _ in range(4)))
    assert sum(result.applied for result in results) == 1
    assert len({result.run.id for result in results}) == 1
    run = results[0].run
    store = RunStore(database)
    assert await store.get(run.organisation_id, run.id) == run
    assert len(await store.steps(run.organisation_id, run.id)) == 1
    with pytest.raises(RunNotFoundError):
        await store.get("org_other", run.id)
    with pytest.raises(IdempotencyConflictError):
        await workflow.create(command.model_copy(update={"credential_id": "credential_other"}))


async def test_credential_lock_blocks_overlap_and_terminal_command_releases_it(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workflow, command = await setup(database, identity)
    result = await workflow.create(command)
    next_command = command.model_copy(
        update={
            "id": "create_two",
            "trigger": command.trigger.model_copy(update={"event_id": "message_two"}),
        }
    )
    with pytest.raises(ActiveRunConflictError):
        await workflow.create(next_command)
    run = result.run
    started = await workflow.start(
        StartRunCommand(
            id="start_one",
            organisation_id=run.organisation_id,
            run_id=run.id,
            actor_id="operator_one",
            expected_revision=0,
            owner_id="worker_one",
            expires_at=NOW + timedelta(minutes=5),
        )
    )
    cancelled = await workflow.cancel(
        CancelRunCommand(
            id="cancel_one",
            organisation_id=run.organisation_id,
            run_id=run.id,
            actor_id="operator_one",
            expected_revision=started.run.revision,
        )
    )
    assert cancelled.run.status is RunStatus.CANCELLED
    assert (await workflow.create(next_command)).applied
    assert len(await RunStore(database).steps(run.organisation_id, run.id)) == 3
