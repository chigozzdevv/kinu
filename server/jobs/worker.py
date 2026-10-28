import asyncio
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime
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
                    {"data.status": "pending"},
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
        await service.queue.rotation(item["organisation_id"], item["id"], recover=True)


async def main() -> None:
    config = ExecutorSettings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    async with runtime(config) as service:

        async def process(job: Job, token: str) -> None:
            if job.name != "rotation":
                raise ResourceConflictError("Unknown background job")
            task = RotationTask.model_validate(job.data)
            await service.rotation.execute(
                task.organisation_id, task.run_id, "worker_" + uuid4().hex
            )

        worker = Worker(
            "kinu", process, {"connection": config.redis_url.get_secret_value(), "concurrency": 2}
        )
        try:
            while not stop.is_set():
                try:
                    await recover(service)
                    await service.poll()
                except Exception as error:
                    log.warning("Background check failed: %s", type(error).__name__)
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), 15)
        finally:
            await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
