from dataclasses import dataclass
from datetime import datetime

from contracts import Notification, NotificationDelivery, NotificationEndpoint
from notification.fanout import enqueue

from core.errors import ResourceConflictError, RevisionConflictError
from core.storage.database import Database
from core.storage.records import Records


@dataclass(frozen=True, slots=True)
class NotificationClaim:
    path: str
    delivery: NotificationDelivery
    notification: Notification
    endpoint: NotificationEndpoint


class NotificationStore:
    def __init__(self, database: Database) -> None:
        self._records = Records(database)

    async def emit(self, notification: Notification) -> tuple[Notification, bool]:
        org = notification.organisation_id
        async with self._records.write(org) as rows:
            existing = await rows.match(Notification, org, {"id": notification.id})
            if existing:
                return existing[0], False
            await rows.add(notification, org)
            await enqueue(rows, notification)
            return notification, True

    async def list_notifications(
        self, organisation_id: str, limit: int
    ) -> tuple[Notification, ...]:
        async with self._records.read() as rows:
            values = await rows.list(Notification, organisation_id)
        return tuple(
            sorted(values, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]
        )

    async def mark_read(
        self,
        organisation_id: str,
        notification_id: str,
        expected_revision: int,
        read_at: datetime,
    ) -> Notification:
        async with self._records.write(organisation_id) as rows:
            current = await rows.get(Notification, organisation_id, notification_id)
            if current.revision != expected_revision:
                raise RevisionConflictError("notification revision changed")
            if current.read_at is not None:
                return current
            changed = current.model_copy(
                update={"read_at": read_at, "revision": current.revision + 1}
            )
            return await rows.replace(changed, expected_revision)

    async def register_endpoint(self, endpoint: NotificationEndpoint) -> NotificationEndpoint:
        org = endpoint.organisation_id
        async with self._records.write(org) as rows:
            existing = await rows.match(NotificationEndpoint, org, {"id": endpoint.id})
            if existing:
                current = existing[0]
                excluded = {"created_at", "updated_at", "revision", "enabled"}
                if current.model_dump(exclude=excluded) != endpoint.model_dump(exclude=excluded):
                    raise ResourceConflictError("notification endpoint already exists")
                return current
            return await rows.add(endpoint, org)

    async def list_endpoints(self, organisation_id: str) -> tuple[NotificationEndpoint, ...]:
        async with self._records.read() as rows:
            return await rows.list(NotificationEndpoint, organisation_id)

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
        updated_at: datetime,
    ) -> NotificationEndpoint:
        async with self._records.write(organisation_id) as rows:
            current = await rows.get(NotificationEndpoint, organisation_id, endpoint_id)
            if current.revision != expected_revision:
                raise RevisionConflictError("notification endpoint revision changed")
            changed = current.model_copy(
                update={
                    "enabled": enabled,
                    "updated_at": updated_at,
                    "revision": current.revision + 1,
                }
            )
            return await rows.replace(changed, expected_revision)
