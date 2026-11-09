from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from contracts import (
    Notification,
    NotificationDelivery,
    NotificationEndpoint,
    NotificationKind,
    Severity,
)
from core.auth import AccessControl, AuthenticatedIdentity, Permission
from core.errors import (
    AuthorizationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from core.notification import EmailDeliveryConfiguration, NotificationService
from core.workflow import RunWorkflow
from fastapi import FastAPI
from pydantic import ValidationError
from testkit import MemoryRunRepository
from tests.browser import HEADERS, make_browser

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Notifications:
    def __init__(self) -> None:
        self.values: dict[str, Notification] = {}
        self.endpoints: dict[str, NotificationEndpoint] = {}

    async def emit(self, notification: Notification) -> tuple[Notification, bool]:
        existing = self.values.get(notification.id)
        if existing is not None:
            return (existing, False)
        self.values[notification.id] = notification
        return (notification, True)

    async def list_notifications(
        self, organisation_id: str, limit: int
    ) -> tuple[Notification, ...]:
        return tuple(self.values.values())[:limit]

    async def mark_read(
        self, organisation_id: str, notification_id: str, expected_revision: int, read_at: datetime
    ) -> Notification:
        current = self.values[notification_id]
        changed = current.model_copy(update={"read_at": read_at, "revision": 1})
        self.values[notification_id] = changed
        return changed

    async def register_endpoint(self, endpoint: NotificationEndpoint) -> NotificationEndpoint:
        self.endpoints[endpoint.id] = endpoint
        return endpoint

    async def list_endpoints(self, organisation_id: str) -> tuple[NotificationEndpoint, ...]:
        return tuple(self.endpoints.values())

    async def set_endpoint_enabled(
        self,
        organisation_id: str,
        endpoint_id: str,
        expected_revision: int,
        enabled: bool,
        updated_at: datetime,
    ) -> NotificationEndpoint:
        current = self.endpoints.get(endpoint_id)
        if current is None:
            raise ResourceNotFoundError("endpoint missing")
        changed = current.model_copy(
            update={"enabled": enabled, "updated_at": updated_at, "revision": current.revision + 1}
        )
        self.endpoints[endpoint_id] = changed
        return changed


async def test_emission_is_deterministic_and_links_cannot_carry_capabilities() -> None:
    repository = Notifications()
    service = NotificationService(repository, lambda: NOW)
    first, applied = await service.emit(
        "event_one",
        "org_one",
        NotificationKind.INCIDENT,
        Severity.CRITICAL,
        "Credential exposed",
        "Incident incident_one requires containment.",
        "/organisations/org_one/incidents/incident_one",
        "incident_one",
        incident_id="incident_one",
    )
    duplicate, duplicate_applied = await service.emit(
        "event_one",
        "org_one",
        NotificationKind.INCIDENT,
        Severity.CRITICAL,
        "Credential exposed",
        "Incident incident_one requires containment.",
        "/organisations/org_one/incidents/incident_one",
        "incident_one",
        incident_id="incident_one",
    )
    assert applied and (not duplicate_applied)
    assert first.id == duplicate.id
    with pytest.raises(ValidationError, match="capabilities"):
        Notification.model_validate(
            {**first.model_dump(), "link_path": "/approvals/one?token=secret"}
        )


async def test_email_endpoint_uses_platform_delivery_configuration() -> None:
    repository = Notifications()
    service = NotificationService(
        repository,
        lambda: NOW,
        EmailDeliveryConfiguration(
            "secret://kinu-project/email-delivery/versions/2", "alerts@kinu.example"
        ),
    )
    endpoint = await service.register_email_endpoint(
        "endpoint_one",
        "org_one",
        "actor_owner",
        "OWNER@ACME.EXAMPLE ",
        frozenset({NotificationKind.APPROVAL_REQUIRED}),
    )
    assert endpoint.display_name == "owner@acme.example"
    assert endpoint.principal_id == "actor_owner"
    assert endpoint.recipients == ("owner@acme.example",)
    assert endpoint.sender == "alerts@kinu.example"
    assert endpoint.auth_reference.endswith("/email-delivery/versions/2")


async def test_email_endpoint_requires_platform_delivery_configuration() -> None:
    service = NotificationService(Notifications(), lambda: NOW)
    with pytest.raises(ResourceConflictError, match="not configured"):
        await service.register_email_endpoint(
            "endpoint_one",
            "org_one",
            "actor_owner",
            "owner@acme.example",
            frozenset({NotificationKind.APPROVAL_REQUIRED}),
        )


async def test_invitation_endpoint_is_deterministic_and_system_owned() -> None:
    repository = Notifications()
    service = NotificationService(
        repository,
        lambda: NOW,
        EmailDeliveryConfiguration(
            "secret://kinu-project/email-delivery/versions/2", "invite@kinu.example"
        ),
    )
    first = await service.register_invitation_endpoint("org_one", "NEW.MEMBER@ACME.EXAMPLE ")
    second = await service.register_invitation_endpoint("org_one", "new.member@acme.example")
    assert first.id == second.id
    assert first.principal_id is None
    assert first.recipients == ("new.member@acme.example",)
    assert first.event_kinds == frozenset({NotificationKind.TEAM_INVITATION})


IDENTITY = AuthenticatedIdentity(
    subject="user-one", issuer="https://identity.example.com/kinu", email="owner@acme.example"
)


class TopicAccess:
    allowed = frozenset({Permission.NOTIFICATION_READ, Permission.APPROVAL_DECIDE})

    async def require(
        self, identity: AuthenticatedIdentity, organisation_id: str, permission: Permission
    ) -> None:
        assert identity == IDENTITY
        assert organisation_id == "org_one"
        if permission not in self.allowed:
            raise AuthorizationError(f"principal lacks {permission.value}")

    async def permissions(
        self, identity: AuthenticatedIdentity, organisation_id: str
    ) -> frozenset[Permission]:
        assert identity == IDENTITY
        assert organisation_id == "org_one"
        return self.allowed


def notification_app() -> FastAPI:
    notifications = NotificationService(
        Notifications(),
        lambda: NOW,
        EmailDeliveryConfiguration(
            "secret://kinu-project/email-delivery/versions/2", "alerts@kinu.example"
        ),
    )
    return create_app(
        ApiServices(
            workflow=RunWorkflow(MemoryRunRepository(), clock=lambda: NOW),
            access=cast(AccessControl, TopicAccess()),
            notifications=notifications,
        ),
        browser=make_browser(IDENTITY),
    )


async def test_topics_and_email_endpoint_are_permission_scoped() -> None:
    transport = httpx.ASGITransport(app=notification_app(), raise_app_exceptions=False)
    headers = HEADERS
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        settings = await client.get(
            "/v1/organisations/org_one/notifications/settings", headers=headers
        )
        topics = await client.get("/v1/organisations/org_one/notifications/topics", headers=headers)
        forbidden = await client.post(
            "/v1/organisations/org_one/notifications/endpoints",
            headers=headers,
            json={
                "id": "endpoint_forbidden",
                "email_address": "owner@acme.example",
                "topics": ["incidents"],
            },
        )
        created = await client.post(
            "/v1/organisations/org_one/notifications/endpoints",
            headers=headers,
            json={
                "id": "endpoint_one",
                "email_address": "owner@acme.example",
                "topics": ["approvals"],
            },
        )
        foreign = await client.post(
            "/v1/organisations/org_one/notifications/endpoints",
            headers=headers,
            json={
                "id": "endpoint_foreign",
                "email_address": "updated@acme.example",
                "topics": ["approvals"],
            },
        )
    assert settings.status_code == 200
    assert settings.json() == {"email_enabled": True}
    assert topics.status_code == 200
    assert topics.json() == [
        {"id": "approvals", "label": "Approval requests", "event_kinds": ["approval-required"]}
    ]
    assert forbidden.status_code == 403
    assert created.status_code == 201
    assert created.json()["email_address"] == "owner@acme.example"
    assert foreign.status_code == 403
    assert "auth_reference" not in created.json()
    assert "sender" not in created.json()


def test_stored_delivery_rows_with_receipt_status_fields_validate() -> None:
    occurred = datetime(2026, 8, 14, 12, tzinfo=UTC)
    row = {
        "id": "delivery_one",
        "organisation_id": "org_one",
        "notification_id": "notification_one",
        "endpoint_id": "endpoint_one",
        "endpoint_revision": 3,
        "provider": "resend",
        "state": "sent",
        "available_at": occurred,
        "attempts": 2,
        "sent_at": occurred,
        "provider_receipt": "email_receipt_one",
        "last_error": None,
        "status_due": occurred,
        "provider_status": "accepted",
        "status_checked_at": occurred,
        "status_error": "Provider receipt lookup is unavailable",
    }
    delivery = NotificationDelivery.model_validate(row)
    assert delivery.status_error == "Provider receipt lookup is unavailable"
    assert delivery.provider_status == "accepted"
    assert delivery.state is not None
