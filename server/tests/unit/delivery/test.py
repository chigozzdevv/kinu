from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationEndpoint,
    NotificationKind,
    NotificationProvider,
    NotificationState,
    Severity,
)
from core.notification import NotificationDispatcher
from core.storage import NotificationClaim

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Deliveries:
    def __init__(self, claims: list[NotificationClaim]) -> None:
        self.claims = claims
        self.sent: list[str] = []
        self.failed: list[tuple[str, bool]] = []

    async def claim(
        self, owner_id: str, now: datetime, lease_duration: timedelta, limit: int
    ) -> tuple[NotificationClaim, ...]:
        values, self.claims = (self.claims[:limit], self.claims[limit:])
        return tuple(values)

    async def mark_sent(
        self, claim: NotificationClaim, owner_id: str, receipt: str, sent_at: datetime
    ) -> None:
        self.sent.append(receipt)

    async def mark_failed(
        self,
        claim: NotificationClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
        terminal: bool,
    ) -> None:
        self.failed.append((error, terminal))


async def test_dispatcher_retries_only_retryable_failures() -> None:
    claim = _claim()
    repository = Deliveries([claim])

    class Failing:
        async def send(
            self, notification: Notification, endpoint: NotificationEndpoint, delivery_id: str
        ) -> str:
            from connectors.base.errors import ConnectorError

            raise ConnectorError("temporary", "unavailable", retryable=True)

    summary = await NotificationDispatcher(repository, Failing(), "worker_one", lambda: NOW).drain()
    assert summary.failed == 1
    assert repository.failed == [("temporary: unavailable", False)]


def _notification() -> Notification:
    return Notification(
        id="notification_one",
        organisation_id="org_one",
        kind=NotificationKind.ROTATION_FAILED,
        severity=Severity.CRITICAL,
        title="Rotation failed",
        body="Run run_one requires review.",
        link_path="/organisations/org_one/runs/run_one",
        resource_id="run_one",
        run_id="run_one",
        created_at=NOW,
    )


def _endpoint() -> NotificationEndpoint:
    return NotificationEndpoint(
        id="endpoint_one",
        organisation_id="org_one",
        display_name="Operations",
        channel=NotificationChannel.CHAT,
        provider=NotificationProvider.SLACK,
        auth_reference="secret://project-one/notification/versions/1",
        event_kinds=frozenset({NotificationKind.ROTATION_FAILED}),
        created_at=NOW,
        updated_at=NOW,
    )


def _claim() -> NotificationClaim:
    notification = _notification()
    endpoint = _endpoint()
    delivery = NotificationDelivery(
        id="delivery_one",
        organisation_id="org_one",
        notification_id=notification.id,
        endpoint_id=endpoint.id,
        endpoint_revision=endpoint.revision,
        provider=endpoint.provider,
        state=NotificationState.SENDING,
        available_at=NOW,
        attempts=1,
        lease_owner="worker_one",
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    return NotificationClaim("deliveries/one", delivery, notification, endpoint)
