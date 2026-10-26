import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from connectors.base.errors import ConnectorError
from contracts import Notification, NotificationEndpoint

from core.storage.notification import NotificationClaim


class DeliveryRepository(Protocol):
    async def claim(
        self,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[NotificationClaim, ...]: ...

    async def mark_sent(
        self,
        claim: NotificationClaim,
        owner_id: str,
        receipt: str,
        sent_at: datetime,
    ) -> None: ...

    async def mark_failed(
        self,
        claim: NotificationClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
        terminal: bool,
    ) -> None: ...


class DeliveryConnector(Protocol):
    async def send(
        self,
        notification: Notification,
        endpoint: NotificationEndpoint,
        delivery_id: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    claimed: int
    sent: int
    failed: int


class NotificationDispatcher:
    def __init__(
        self,
        repository: DeliveryRepository,
        connector: DeliveryConnector,
        owner_id: str,
        clock: Callable[[], datetime],
        lease_duration: timedelta = timedelta(seconds=60),
        batch_size: int = 20,
        maximum_attempts: int = 8,
    ) -> None:
        self._repository = repository
        self._connector = connector
        self._owner_id = owner_id
        self._clock = clock
        self._lease_duration = lease_duration
        self._batch_size = batch_size
        self._maximum_attempts = maximum_attempts

    async def drain(self, maximum: int = 100) -> DeliverySummary:
        claimed = sent = failed = 0
        while claimed < maximum:
            batch = await self._repository.claim(
                self._owner_id,
                self._clock(),
                self._lease_duration,
                min(self._batch_size, maximum - claimed),
            )
            if not batch:
                break
            results = await asyncio.gather(*(self._send(item) for item in batch))
            claimed += len(batch)
            sent += sum(results)
            failed += len(results) - sum(results)
        return DeliverySummary(claimed, sent, failed)

    async def _send(self, claim: NotificationClaim) -> bool:
        if not claim.endpoint.enabled:
            await self._repository.mark_failed(
                claim,
                self._owner_id,
                "endpoint-disabled: notification endpoint was disabled before delivery",
                self._clock(),
                True,
            )
            return False
        if claim.endpoint.revision != claim.delivery.endpoint_revision:
            await self._repository.mark_failed(
                claim,
                self._owner_id,
                "endpoint-changed: notification endpoint changed before delivery",
                self._clock(),
                True,
            )
            return False
        try:
            receipt = await self._connector.send(
                claim.notification,
                claim.endpoint,
                claim.delivery.id,
            )
        except Exception as error:
            retryable = isinstance(error, ConnectorError) and error.retryable
            terminal = not retryable or claim.delivery.attempts >= self._maximum_attempts
            now = self._clock()
            await self._repository.mark_failed(
                claim,
                self._owner_id,
                _safe_error(error),
                now if terminal else now + _backoff(claim.delivery.attempts),
                terminal,
            )
            return False
        await self._repository.mark_sent(claim, self._owner_id, receipt, self._clock())
        return True


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(5 * (2 ** min(max(attempts - 1, 0), 10)), 900))


def _safe_error(error: Exception) -> str:
    if isinstance(error, ConnectorError):
        return f"{error.code}: {error}"[:1024]
    return type(error).__name__[:1024]
