import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from contracts import RenewLeaseCommand, RotationRun, RunStatus
from core.errors import LeaseConflictError
from core.storage import RunRepository
from core.workflow import RunWorkflow

from rotation.stages import stable


class Heartbeat:
    def __init__(
        self,
        repository: RunRepository,
        workflow: RunWorkflow,
        run: RotationRun,
        *,
        interval: float = 60,
        duration: timedelta = timedelta(minutes=14),
    ) -> None:
        self.repository, self.workflow, self.run = repository, workflow, run
        self.interval, self.duration = interval, duration
        self.lock = asyncio.Lock()
        self.error: Exception | None = None
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._renew())

    def check(self) -> None:
        if self.error:
            raise self.error

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def _renew(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval)
                async with self.lock:
                    current = await self.repository.get(self.run.organisation_id, self.run.id)
                    if current.status not in {RunStatus.RUNNING, RunStatus.RECOVERING}:
                        return
                    if (
                        not current.lease
                        or not self.run.lease
                        or current.lease.owner_id != self.run.lease.owner_id
                        or current.fencing_token != self.run.fencing_token
                    ):
                        raise LeaseConflictError("Rotation execution ownership changed")
                    await self.workflow.renew(
                        RenewLeaseCommand(
                            id=stable("renew", current.id + ":" + str(current.revision)),
                            organisation_id=current.organisation_id,
                            run_id=current.id,
                            actor_id=current.lease.owner_id,
                            owner_id=current.lease.owner_id,
                            expected_revision=current.revision,
                            fencing_token=current.fencing_token,
                            expires_at=datetime.now(UTC) + self.duration,
                        )
                    )
        except Exception as error:
            self.error = error
