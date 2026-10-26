import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from contracts import (
    Notification,
    NotificationChannel,
    NotificationEndpoint,
    NotificationKind,
    NotificationProvider,
    Severity,
)

from core.errors import ResourceConflictError, ResourceNotFoundError


@dataclass(frozen=True, slots=True)
class EmailDeliveryConfiguration:
    auth_reference: str
    sender: str


class NotificationRepository(Protocol):
    async def emit(self, notification: Notification) -> tuple[Notification, bool]: ...

    async def list_notifications(
        self, organisation_id: str, limit: int
    ) -> tuple[Notification, ...]: ...

    async def mark_read(
        self,
        organisation_id: str,
        notification_id: str,
        expected_revision: int,
        read_at: datetime,
    ) -> Notification: ...

    async def register_endpoint(self, endpoint: NotificationEndpoint) -> NotificationEndpoint: ...

    async def list_endpoints(self, organisation_id: str) -> tuple[NotificationEndpoint, ...]: ...

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
        updated_at: datetime,
    ) -> NotificationEndpoint: ...


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        clock: Callable[[], datetime],
        email_delivery: EmailDeliveryConfiguration | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._email_delivery = email_delivery

    async def emit(
        self,
        event_id: str,
        organisation_id: str,
        kind: NotificationKind,
        severity: Severity,
        title: str,
        body: str,
        link_path: str,
        resource_id: str,
        run_id: str | None = None,
        incident_id: str | None = None,
        approval_id: str | None = None,
    ) -> tuple[Notification, bool]:
        identity = hashlib.sha256(
            f"{organisation_id}\0{event_id}\0{kind.value}".encode()
        ).hexdigest()
        notification = Notification(
            id=f"notification_{identity[:40]}",
            organisation_id=organisation_id,
            kind=kind,
            severity=severity,
            title=title,
            body=body,
            link_path=link_path,
            resource_id=resource_id,
            run_id=run_id,
            incident_id=incident_id,
            approval_id=approval_id,
            created_at=self._clock(),
        )
        return await self._repository.emit(notification)

    def ensure_email_delivery(self) -> None:
        if self._email_delivery is None:
            raise ResourceConflictError("email delivery is not configured")

    async def list_notifications(
        self, organisation_id: str, limit: int = 100
    ) -> tuple[Notification, ...]:
        return await self._repository.list_notifications(organisation_id, limit)

    async def mark_read(
        self, organisation_id: str, notification_id: str, expected_revision: int
    ) -> Notification:
        return await self._repository.mark_read(
            organisation_id,
            notification_id,
            expected_revision,
            self._clock(),
        )

    async def register_endpoint(
        self,
        endpoint_id: str,
        organisation_id: str,
        display_name: str,
        channel: NotificationChannel,
        provider: NotificationProvider,
        auth_reference: str,
        event_kinds: frozenset[NotificationKind],
        recipients: tuple[str, ...] = (),
        sender: str | None = None,
        principal_id: str | None = None,
    ) -> NotificationEndpoint:
        now = self._clock()
        endpoint = NotificationEndpoint(
            id=endpoint_id,
            organisation_id=organisation_id,
            principal_id=principal_id,
            display_name=display_name,
            channel=channel,
            provider=provider,
            auth_reference=auth_reference,
            event_kinds=event_kinds,
            recipients=recipients,
            sender=sender,
            created_at=now,
            updated_at=now,
        )
        return await self._repository.register_endpoint(endpoint)

    async def register_email_endpoint(
        self,
        endpoint_id: str,
        organisation_id: str,
        principal_id: str,
        email_address: str,
        event_kinds: frozenset[NotificationKind],
    ) -> NotificationEndpoint:
        if self._email_delivery is None:
            raise ResourceConflictError("email notifications are not configured")
        email = email_address.strip().lower()
        return await self.register_endpoint(
            endpoint_id,
            organisation_id,
            email,
            NotificationChannel.EMAIL,
            NotificationProvider.RESEND,
            self._email_delivery.auth_reference,
            event_kinds,
            (email,),
            self._email_delivery.sender,
            principal_id,
        )

    async def register_invitation_endpoint(
        self,
        organisation_id: str,
        email_address: str,
    ) -> NotificationEndpoint:
        self.ensure_email_delivery()
        assert self._email_delivery is not None
        email = email_address.strip().lower()
        identity = hashlib.sha256(
            f"{organisation_id}\0{email}\0team-invitation".encode()
        ).hexdigest()
        return await self.register_endpoint(
            f"endpoint_{identity[:40]}",
            organisation_id,
            email,
            NotificationChannel.EMAIL,
            NotificationProvider.RESEND,
            self._email_delivery.auth_reference,
            frozenset({NotificationKind.TEAM_INVITATION}),
            (email,),
            self._email_delivery.sender,
        )

    async def list_endpoints(self, organisation_id: str) -> tuple[NotificationEndpoint, ...]:
        return await self._repository.list_endpoints(organisation_id)

    async def list_email_endpoints(
        self,
        organisation_id: str,
        principal_id: str,
    ) -> tuple[NotificationEndpoint, ...]:
        endpoints = await self.list_endpoints(organisation_id)
        return tuple(
            endpoint
            for endpoint in endpoints
            if endpoint.provider is NotificationProvider.RESEND
            and endpoint.principal_id == principal_id
        )

    async def set_email_endpoint_enabled(
        self,
        organisation_id: str,
        principal_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
    ) -> NotificationEndpoint:
        endpoints = await self.list_email_endpoints(organisation_id, principal_id)
        if not any(endpoint.id == endpoint_id for endpoint in endpoints):
            raise ResourceNotFoundError(f"email notification endpoint {endpoint_id} was not found")
        return await self.set_endpoint_enabled(
            organisation_id,
            endpoint_id,
            expected_revision,
            enabled,
        )

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
    ) -> NotificationEndpoint:
        return await self._repository.set_endpoint_enabled(
            organisation_id,
            endpoint_id,
            expected_revision,
            enabled,
            self._clock(),
        )
