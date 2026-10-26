from contracts import (
    Contract,
    EmailNotificationEndpoint,
    Identifier,
    Notification,
    NotificationEndpoint,
    NotificationKind,
    NotificationTopic,
)
from core.auth import Permission
from core.errors import AuthorizationError, StorageIntegrityError
from fastapi import APIRouter, Query, Request, status
from pydantic import Field, field_validator

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/notifications",
    tags=["notifications"],
)


class ReadRequest(Contract):
    expected_revision: int = Field(ge=0)


class EmailEndpointRequest(Contract):
    id: Identifier
    email_address: str = Field(min_length=3, max_length=320)
    topics: frozenset[Identifier] = Field(min_length=1)

    @field_validator("email_address")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        normalised = value.strip().lower()
        local, separator, domain = normalised.rpartition("@")
        if not separator or not local or "." not in domain or domain.startswith("."):
            raise ValueError("email address is invalid")
        return normalised


class EndpointStateRequest(Contract):
    expected_revision: int = Field(ge=0)
    enabled: bool


TOPICS: tuple[tuple[NotificationTopic, Permission], ...] = (
    (
        NotificationTopic(
            id="incidents",
            label="Incidents",
            event_kinds=frozenset(
                {NotificationKind.INCIDENT, NotificationKind.INCIDENT_CONFIRMATION}
            ),
        ),
        Permission.INCIDENT_WRITE,
    ),
    (
        NotificationTopic(
            id="rotation-failures",
            label="Rotation failures",
            event_kinds=frozenset(
                {
                    NotificationKind.ROTATION_FAILED,
                    NotificationKind.RECOVERY_STARTED,
                    NotificationKind.CLEANUP_REQUIRED,
                }
            ),
        ),
        Permission.RUN_WRITE,
    ),
    (
        NotificationTopic(
            id="approvals",
            label="Approval requests",
            event_kinds=frozenset({NotificationKind.APPROVAL_REQUIRED}),
        ),
        Permission.APPROVAL_DECIDE,
    ),
    (
        NotificationTopic(
            id="credential-use",
            label="Previous credential use",
            event_kinds=frozenset({NotificationKind.OLD_KEY_USED}),
        ),
        Permission.RUN_READ,
    ),
    (
        NotificationTopic(
            id="connection-health",
            label="Connection issues",
            event_kinds=frozenset({NotificationKind.CONNECTION_UNHEALTHY}),
        ),
        Permission.INVENTORY_WRITE,
    ),
    (
        NotificationTopic(
            id="playbook-review",
            label="Playbook reviews",
            event_kinds=frozenset({NotificationKind.PLAYBOOK_REVIEW}),
        ),
        Permission.PLAYBOOK_APPROVE,
    ),
    (
        NotificationTopic(
            id="rotation-due",
            label="Upcoming rotations",
            event_kinds=frozenset({NotificationKind.ROTATION_DUE}),
        ),
        Permission.RUN_WRITE,
    ),
    (
        NotificationTopic(
            id="rotation-completed",
            label="Completed rotations",
            event_kinds=frozenset(
                {
                    NotificationKind.REVOCATION_SUCCEEDED,
                    NotificationKind.ROTATION_COMPLETED,
                }
            ),
        ),
        Permission.RUN_READ,
    ),
)


@router.get("", response_model=tuple[Notification, ...])
async def list_notifications(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> tuple[Notification, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_READ)
    return await required(api.notifications, "notifications").list_notifications(
        organisation_id, limit
    )


@router.post("/{notification_id}/read", response_model=Notification)
async def mark_read(
    organisation_id: Identifier,
    notification_id: Identifier,
    body: ReadRequest,
    identity: Identity,
    request: Request,
) -> Notification:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_READ)
    return await required(api.notifications, "notifications").mark_read(
        organisation_id, notification_id, body.expected_revision
    )


@router.get("/topics", response_model=tuple[NotificationTopic, ...])
async def list_topics(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[NotificationTopic, ...]:
    api = services(request)
    permissions = await api.access.permissions(identity, organisation_id)
    if Permission.NOTIFICATION_READ not in permissions:
        raise AuthorizationError("principal lacks notification.read")
    return tuple(topic for topic, permission in TOPICS if permission in permissions)


@router.get("/endpoints", response_model=tuple[EmailNotificationEndpoint, ...])
async def list_endpoints(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[EmailNotificationEndpoint, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_READ)
    endpoints = await required(api.notifications, "notifications").list_email_endpoints(
        organisation_id,
        identity.actor_id,
    )
    return tuple(_email_endpoint(endpoint) for endpoint in endpoints)


@router.post(
    "/endpoints",
    response_model=EmailNotificationEndpoint,
    status_code=status.HTTP_201_CREATED,
)
async def create_endpoint(
    organisation_id: Identifier,
    body: EmailEndpointRequest,
    identity: Identity,
    request: Request,
) -> EmailNotificationEndpoint:
    api = services(request)
    permissions = await api.access.permissions(identity, organisation_id)
    if Permission.NOTIFICATION_READ not in permissions:
        raise AuthorizationError("principal lacks notification.read")
    available = {topic.id: topic for topic, permission in TOPICS if permission in permissions}
    if not body.topics.issubset(available):
        raise AuthorizationError("one or more notification types are not available")
    event_kinds = frozenset(
        kind for topic_id in body.topics for kind in available[topic_id].event_kinds
    )
    endpoint = await required(api.notifications, "notifications").register_email_endpoint(
        body.id,
        organisation_id,
        identity.actor_id,
        body.email_address,
        event_kinds,
    )
    return _email_endpoint(endpoint)


@router.post("/endpoints/{endpoint_id}/state", response_model=EmailNotificationEndpoint)
async def set_endpoint_state(
    organisation_id: Identifier,
    endpoint_id: Identifier,
    body: EndpointStateRequest,
    identity: Identity,
    request: Request,
) -> EmailNotificationEndpoint:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.NOTIFICATION_READ)
    endpoint = await required(api.notifications, "notifications").set_email_endpoint_enabled(
        organisation_id,
        identity.actor_id,
        endpoint_id,
        body.expected_revision,
        body.enabled,
    )
    return _email_endpoint(endpoint)


def _email_endpoint(endpoint: NotificationEndpoint) -> EmailNotificationEndpoint:
    if not endpoint.recipients:
        raise StorageIntegrityError("email notification endpoint has no recipient")
    return EmailNotificationEndpoint(
        id=endpoint.id,
        organisation_id=endpoint.organisation_id,
        email_address=endpoint.recipients[0],
        event_kinds=endpoint.event_kinds,
        enabled=endpoint.enabled,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
        revision=endpoint.revision,
    )
