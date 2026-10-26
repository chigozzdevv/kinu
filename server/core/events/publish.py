import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from contracts import OutboxEvent, RunEvent


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    path: str
    outbox: OutboxEvent


@dataclass(frozen=True, slots=True)
class PublishSummary:
    claimed: int
    published: int
    failed: int
    dead_lettered: int


class OutboxRepository(Protocol):
    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[OutboxClaim, ...]: ...

    async def mark_published(
        self,
        claim: OutboxClaim,
        owner_id: str,
        message_id: str,
        published_at: datetime,
    ) -> None: ...

    async def mark_failed(
        self,
        claim: OutboxClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
    ) -> None: ...

    async def mark_dead_letter(
        self,
        claim: OutboxClaim,
        owner_id: str,
        error: str,
        dead_lettered_at: datetime,
    ) -> None: ...


class EventTransport(Protocol):
    async def publish(self, event: RunEvent) -> str: ...


class EventPublisher:
    def __init__(
        self,
        repository: OutboxRepository,
        transport: EventTransport,
        owner_id: str,
        clock: Callable[[], datetime],
        lease_duration: timedelta = timedelta(seconds=60),
        batch_size: int = 20,
        retry_base: timedelta = timedelta(seconds=5),
        retry_max: timedelta = timedelta(minutes=15),
        maximum_attempts: int = 10,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if retry_base <= timedelta(0) or retry_max < retry_base:
            raise ValueError("retry delays are invalid")
        if maximum_attempts < 1:
            raise ValueError("maximum attempts must be positive")
        self._repository = repository
        self._transport = transport
        self._owner_id = owner_id
        self._clock = clock
        self._lease_duration = lease_duration
        self._batch_size = batch_size
        self._retry_base = retry_base
        self._retry_max = retry_max
        self._maximum_attempts = maximum_attempts

    async def drain(self, max_events: int = 100) -> PublishSummary:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        claimed = 0
        published = 0
        failed = 0
        dead_lettered = 0

        while claimed < max_events:
            batch = await self._repository.claim(
                self._owner_id,
                self._clock(),
                self._lease_duration,
                min(self._batch_size, max_events - claimed),
            )
            if not batch:
                break
            results = await asyncio.gather(*(self._deliver(item) for item in batch))
            claimed += len(batch)
            published += sum(result == "published" for result in results)
            failed += sum(result == "failed" for result in results)
            dead_lettered += sum(result == "dead-lettered" for result in results)

        return PublishSummary(
            claimed=claimed,
            published=published,
            failed=failed,
            dead_lettered=dead_lettered,
        )

    async def _deliver(self, claim: OutboxClaim) -> str:
        try:
            message_id = await self._transport.publish(claim.outbox.event)
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
                now + self._backoff(claim.outbox.attempts),
            )
            return "failed"

        await self._repository.mark_published(
            claim,
            self._owner_id,
            message_id,
            self._clock(),
        )
        return "published"

    def _backoff(self, attempts: int) -> timedelta:
        multiplier = 2 ** min(max(attempts - 1, 0), 16)
        seconds = min(
            self._retry_base.total_seconds() * multiplier,
            self._retry_max.total_seconds(),
        )
        return timedelta(seconds=seconds)


def _safe_error(error: Exception) -> str:
    message = f"{type(error).__name__}: {error}".replace("\n", " ").strip()
    return (message or type(error).__name__)[:1024]
