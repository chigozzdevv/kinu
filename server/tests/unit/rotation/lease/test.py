import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from contracts import CancelRunCommand, CreateRunCommand, StartRunCommand
from core.workflow import RunWorkflow
from rotation.lease import Heartbeat
from testkit import MemoryRunRepository, make_run

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_heartbeat_keeps_long_work_alive_and_stops_after_ownership_is_released() -> None:
    repository = MemoryRunRepository()
    workflow = RunWorkflow(repository)
    original = make_run()
    run = (
        await workflow.create(
            CreateRunCommand(
                id="create_one",
                run_id=original.id,
                organisation_id=original.organisation_id,
                credential_id=original.credential_id,
                control_version=original.control_version,
                trigger=original.trigger,
            )
        )
    ).run
    run = (
        await workflow.start(
            StartRunCommand(
                id="start_one",
                organisation_id=run.organisation_id,
                run_id=run.id,
                actor_id="worker_one",
                owner_id="worker_one",
                expected_revision=run.revision,
                expires_at=datetime.now(UTC) + timedelta(milliseconds=150),
            )
        )
    ).run
    heartbeat = Heartbeat(repository, workflow, run, interval=0.01, duration=timedelta(seconds=5))
    heartbeat.start()
    try:
        await asyncio.sleep(0.2)
        heartbeat.check()
        async with heartbeat.lock:
            current = await repository.get(run.organisation_id, run.id)
            assert current.lease and current.lease.expires_at > datetime.now(UTC)
            assert current.revision > run.revision
            await workflow.cancel(
                CancelRunCommand(
                    id="cancel_one",
                    organisation_id=run.organisation_id,
                    run_id=run.id,
                    actor_id="operator_one",
                    expected_revision=current.revision,
                )
            )
        await asyncio.sleep(0.03)
        assert heartbeat.task and heartbeat.task.done()
        assert (await repository.get(run.organisation_id, run.id)).lease is None
    finally:
        await heartbeat.close()
