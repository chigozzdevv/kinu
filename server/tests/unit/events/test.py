from datetime import UTC, datetime, timedelta

import pytest
from contracts import EventKind, OutboxEvent, RunEvent, RunStatus, Stage
from core.events import EventPublisher, OutboxClaim

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class MemoryOutbox:
    def __init__(self, claims: list[OutboxClaim]) -> None:
        self.claims = claims
        self.published: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str, datetime]] = []
        self.dead_lettered: list[tuple[str, str, datetime]] = []

    async def claim(
        self, owner_id: str, now: datetime, lease_duration: timedelta, limit: int
    ) -> tuple[OutboxClaim, ...]:
        claimed, self.claims = (self.claims[:limit], self.claims[limit:])
        return tuple(claimed)

    async def mark_published(
        self, claim: OutboxClaim, owner_id: str, message_id: str, published_at: datetime
    ) -> None:
        self.published.append((claim.outbox.event.id, message_id))

    async def mark_failed(
        self, claim: OutboxClaim, owner_id: str, error: str, available_at: datetime
    ) -> None:
        self.failed.append((claim.outbox.event.id, error, available_at))

    async def mark_dead_letter(
        self, claim: OutboxClaim, owner_id: str, error: str, dead_lettered_at: datetime
    ) -> None:
        self.dead_lettered.append((claim.outbox.event.id, error, dead_lettered_at))


class MemoryTransport:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.events: list[str] = []

    async def publish(self, event: RunEvent) -> str:
        self.events.append(event.id)
        if event.id in self.failing:
            raise TimeoutError("provider unavailable\nretry")
        return f"message-{event.id}"


async def test_publisher_drains_claimed_events() -> None:
    repository = MemoryOutbox([_claim("event_one"), _claim("event_two")])
    transport = MemoryTransport()
    publisher = EventPublisher(
        repository, transport, owner_id="publisher_one", clock=lambda: NOW, batch_size=1
    )
    summary = await publisher.drain()
    assert summary.claimed == 2
    assert summary.published == 2
    assert summary.failed == 0
    assert summary.dead_lettered == 0
    assert transport.events == ["event_one", "event_two"]
    assert repository.published == [
        ("event_one", "message-event_one"),
        ("event_two", "message-event_two"),
    ]


async def test_publisher_releases_failure_with_backoff() -> None:
    claim = _claim("event_one", attempts=3)
    repository = MemoryOutbox([claim])
    publisher = EventPublisher(
        repository, MemoryTransport({"event_one"}), owner_id="publisher_one", clock=lambda: NOW
    )
    summary = await publisher.drain()
    assert summary.failed == 1
    assert repository.failed == [
        ("event_one", "TimeoutError: provider unavailable retry", NOW + timedelta(seconds=20))
    ]


async def test_publisher_dead_letters_a_poison_event_after_the_attempt_limit() -> None:
    claim = _claim("event_one", attempts=10)
    repository = MemoryOutbox([claim])
    publisher = EventPublisher(
        repository, MemoryTransport({"event_one"}), owner_id="publisher_one", clock=lambda: NOW
    )
    summary = await publisher.drain()
    assert summary.dead_lettered == 1
    assert summary.failed == 0
    assert repository.failed == []
    assert repository.dead_lettered == [
        ("event_one", "TimeoutError: provider unavailable retry", NOW)
    ]


def _claim(event_id: str, attempts: int = 1) -> OutboxClaim:
    event = RunEvent(
        id=event_id,
        organisation_id="org_one",
        run_id=f"run_{event_id}",
        credential_id="cred_one",
        kind=EventKind.RUN_CREATED,
        revision=0,
        stage=Stage.TRIGGER,
        status=RunStatus.PENDING,
        actor_id="service_one",
        occurred_at=NOW,
    )
    outbox = OutboxEvent(
        event=event,
        available_at=NOW,
        attempts=attempts,
        lease_owner="publisher_one",
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    return OutboxClaim(path=f"organisations/org_one/outbox/{event_id}", outbox=outbox)
