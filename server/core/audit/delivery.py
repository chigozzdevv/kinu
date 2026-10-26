import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from contracts import AuditEvent, AuditOutbox


@dataclass(frozen=True, slots=True)
class AuditClaim:
    path: str
    outbox: AuditOutbox


@dataclass(frozen=True, slots=True)
class AuditDeliverySummary:
    claimed: int
    logged: int
    failed: int
    dead_lettered: int


class AuditDeliveryRepository(Protocol):
    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[AuditClaim, ...]: ...

    async def mark_logged(
        self,
        claim: AuditClaim,
        owner_id: str,
        receipt: str,
        logged_at: datetime,
    ) -> None: ...

    async def mark_failed(
        self,
        claim: AuditClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
    ) -> None: ...

    async def mark_dead_letter(
        self,
        claim: AuditClaim,
        owner_id: str,
        error: str,
        dead_lettered_at: datetime,
    ) -> None: ...


class AuditTransport(Protocol):
    async def write(self, event: AuditEvent) -> str: ...


class AuditPublisher:
    def __init__(
        self,
        repository: AuditDeliveryRepository,
        transport: AuditTransport,
        owner_id: str,
        clock: Callable[[], datetime],
        lease_duration: timedelta = timedelta(seconds=60),
        batch_size: int = 20,
        maximum_attempts: int = 10,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._owner_id = owner_id
        self._clock = clock
        self._lease_duration = lease_duration
        self._batch_size = batch_size
        self._maximum_attempts = maximum_attempts

    async def drain(self, maximum: int = 100) -> AuditDeliverySummary:
        claimed = logged = failed = dead_lettered = 0
        while claimed < maximum:
            batch = await self._repository.claim(
                self._owner_id,
                self._clock(),
                self._lease_duration,
                min(self._batch_size, maximum - claimed),
            )
            if not batch:
                break
            results = await asyncio.gather(*(self._write(item) for item in batch))
            claimed += len(batch)
            logged += sum(result == "logged" for result in results)
            failed += sum(result == "failed" for result in results)
            dead_lettered += sum(result == "dead-lettered" for result in results)
        return AuditDeliverySummary(claimed, logged, failed, dead_lettered)

    async def _write(self, claim: AuditClaim) -> str:
        try:
            receipt = await self._transport.write(claim.outbox.event)
        except Exception as error:
            now = self._clock()
            safe_error = _safe_error(error)
            if claim.outbox.attempts >= self._maximum_attempts:
                await self._repository.mark_dead_letter(
                    claim,
                    self._owner_id,
                    safe_error,
                    now,
                )
                return "dead-lettered"
            await self._repository.mark_failed(
                claim,
                self._owner_id,
                safe_error,
                now + _backoff(claim.outbox.attempts),
            )
            return "failed"
        await self._repository.mark_logged(claim, self._owner_id, receipt, self._clock())
        return "logged"


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(5 * (2 ** min(max(attempts - 1, 0), 10)), 900))


def _safe_error(error: Exception) -> str:
    return type(error).__name__[:1024]
