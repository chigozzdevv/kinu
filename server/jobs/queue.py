from typing import Any

from bullmq import Job, Queue
from core.errors import StorageUnavailableError


class Jobs:
    def __init__(self, url: str) -> None:
        self.queue = Queue("kinu", {"connection": url})

    async def close(self) -> None:
        await self.queue.close()

    async def rotation(self, organisation_id: str, run_id: str, *, recover: bool = False) -> None:
        await self.add(
            "rotation",
            {"organisation_id": organisation_id, "run_id": run_id},
            f"rotation-{organisation_id}-{run_id}",
            recover=recover,
        )

    async def add(
        self, name: str, data: dict[str, Any], identity: str, *, recover: bool = False
    ) -> None:
        try:
            if recover:
                existing = await Job.fromId(self.queue, identity)
                if existing:
                    state = await existing.getState()
                    if state in {"failed", "completed"}:
                        try:
                            await existing.retry(state)
                        except Exception:
                            if await existing.getState() in {"failed", "completed"}:
                                raise
                    return
            await self.queue.add(
                name,
                data,
                {
                    "jobId": identity,
                    "attempts": 3,
                    "backoff": {"type": "exponential", "delay": 5000},
                    "removeOnComplete": {"age": 86400, "count": 1000},
                    "removeOnFail": {"age": 604800, "count": 1000},
                },
            )
        except Exception:
            raise StorageUnavailableError(
                "Job queue is unavailable; saved work will be retried"
            ) from None
