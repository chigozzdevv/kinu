import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

from bullmq import Job, Worker
from core.errors import ResourceConflictError
from executor.config import ExecutorSettings
from executor.runtime import Runtime, runtime
from rotation.queue import RotationTask

log = logging.getLogger("kinu.jobs")


async def recover(service: Runtime) -> None:
    async for item in (
        service.database.collection("runs")
        .find(
            {
                "$or": [
                    {"data.status": {"$in": ["pending", "recovering"]}},
                    {"data.status": "paused", "data.stage": "approval"},
                    {
                        "data.status": "running",
                        "data.lease.expires_at": {"$lte": datetime.now(UTC).isoformat()},
                    },
                ]
            }
        )
        .sort("data.created_at", 1)
        .limit(100)
    ):
        if item["data"]["status"] == "recovering":
            from contracts import RotationRun

            await service.recovery.enqueue(RotationRun.model_validate(item["data"]))
        else:
            await service.queue.rotation(item["organisation_id"], item["id"], recover=True)


async def main() -> None:
    config = ExecutorSettings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    async with runtime(config) as service:

        async def process(job: Job, token: str) -> None:
            if job.name not in {"rotation", "recovery", "provision"}:
                raise ResourceConflictError("Unknown background job")
            task = RotationTask.model_validate(job.data)
            if job.name == "provision":
                await service.provisioning.execute(task.organisation_id, task.run_id)
                return
            if job.name == "recovery":
                await service.recovery.execute(task.organisation_id, task.run_id)
                return
            await service.rotation.execute(
                task.organisation_id, task.run_id, "worker_" + uuid4().hex
            )

        worker = Worker(
            "kinu", process, {"connection": config.redis_url.get_secret_value(), "concurrency": 2}
        )

        async def supervise(name: str, check: Callable[[], Awaitable[object]]) -> None:
            while not stop.is_set():
                try:
                    await check()
                except Exception as error:
                    log.warning("Background %s check failed: %s", name, type(error).__name__)
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), 15)

        async def heartbeat() -> None:
            await service.queue.queue.client.set(
                "kinu:worker:heartbeat", datetime.now(UTC).isoformat(), ex=45
            )

        checks = [
            asyncio.create_task(supervise(name, check))
            for name, check in (
                ("recovery", partial(recover, service)),
                ("monitoring", service.monitor.poll),
                ("key setup", service.provisioning.poll),
                ("gmail", service.poll),
                ("incident sources", service.sources.drain),
                ("email", service.email.drain),
                ("heartbeat", heartbeat),
            )
        ]
        try:
            await stop.wait()
        finally:
            for task in checks:
                task.cancel()
            await asyncio.gather(*checks, return_exceptions=True)
            await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
