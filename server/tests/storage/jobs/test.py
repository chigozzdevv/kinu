import asyncio
import os
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import pytest
from bullmq import Job, Worker
from jobs.queue import Jobs

pytestmark = pytest.mark.anyio


@pytest.fixture
async def jobs() -> AsyncIterator[Jobs]:
    url = os.environ.get("KINU_TEST_REDIS_URL")
    if not url:
        pytest.skip("set KINU_TEST_REDIS_URL to Redis database 15 for queue checks")
    if urlsplit(url).path != "/15":
        pytest.fail("Queue tests require isolated Redis database 15")
    queue = Jobs(url)
    await queue.queue.obliterate({"force": True})
    try:
        yield queue
    finally:
        await queue.queue.obliterate({"force": True})
        await queue.close()


async def state(job: Job, expected: str) -> None:
    async with asyncio.timeout(40):
        while await job.getState() != expected:
            await asyncio.sleep(0.05)


async def test_delivery_deduplication_retry_and_recovery_survive_client_restart(jobs: Jobs) -> None:
    url = os.environ["KINU_TEST_REDIS_URL"]
    await asyncio.gather(*(jobs.rotation("org_one", "run_one") for _ in range(4)))
    identity = "rotation-org_one-run_one"
    job = await Job.fromId(jobs.queue, identity)
    assert job and job.data == {"organisation_id": "org_one", "run_id": "run_one"}
    assert await jobs.queue.getJobCountByTypes("wait") == 1
    calls = []

    async def unavailable(current: Job, token: str) -> None:
        calls.append(current.id)
        raise RuntimeError("Storage temporarily unavailable")

    worker = Worker("kinu", unavailable, {"connection": url})
    try:
        await state(job, "failed")
    finally:
        await worker.close()
    assert calls == [identity] * 3
    restarted = Jobs(url)
    try:
        await restarted.rotation("org_one", "run_one", recover=True)
        assert await job.getState() == "waiting"
        completed = asyncio.Event()

        async def finish(current: Job, token: str) -> None:
            assert current.data == job.data
            completed.set()

        worker = Worker("kinu", finish, {"connection": url})
        try:
            await asyncio.wait_for(completed.wait(), 10)
            await state(job, "completed")
        finally:
            await worker.close()
        await restarted.rotation("org_one", "run_one")
        assert await job.getState() == "completed"
        await restarted.rotation("org_one", "run_one", recover=True)
        assert await job.getState() == "waiting"
    finally:
        await restarted.close()
