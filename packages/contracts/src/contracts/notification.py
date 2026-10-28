from enum import StrEnum

from pydantic import AwareDatetime, Field, field_validator, model_validator

from contracts.base import Contract, Identifier
from contracts.incident import Severity


class NotificationKind(StrEnum):
    INCIDENT = "incident"
    INCIDENT_CONFIRMATION = "incident-confirmation"
    ROTATION_DUE = "rotation-due"
    ROTATION_FAILED = "rotation-failed"
    RECOVERY_STARTED = "recovery-started"
    APPROVAL_REQUIRED = "approval-required"
    OLD_KEY_USED = "old-key-used"
    CONNECTION_UNHEALTHY = "connection-unhealthy"
    PLAYBOOK_REVIEW = "playbook-review"
    REVOCATION_SUCCEEDED = "revocation-succeeded"
    ROTATION_COMPLETED = "rotation-completed"
    CLEANUP_REQUIRED = "cleanup-required"
    TEAM_INVITATION = "team-invitation"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    CHAT = "chat"
    INCIDENT = "incident"


class NotificationProvider(StrEnum):
    RESEND = "resend"
    SLACK = "slack"
    PAGERDUTY = "pagerduty"


class NotificationState(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class NotificationEndpoint(Contract):
    id: Identifier
    organisation_id: Identifier
    principal_id: Identifier | None = None
    display_name: str = Field(min_length=1, max_length=160)
    channel: NotificationChannel
    provider: NotificationProvider
    auth_reference: str = Field(
        pattern=r"^(?:arn:[A-Za-z0-9_:/+.,=@-]+|[a-z][a-z0-9+.-]*://[^\s]+)$",
        max_length=1024,
    )
    event_kinds: frozenset[NotificationKind] = Field(min_length=1)
    recipients: tuple[str, ...] = Field(default=(), max_length=50)
    sender: str | None = Field(default=None, max_length=320)
    enabled: bool = True
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_provider(self) -> "NotificationEndpoint":
        expected = {
            NotificationProvider.RESEND: NotificationChannel.EMAIL,
            NotificationProvider.SLACK: NotificationChannel.CHAT,
            NotificationProvider.PAGERDUTY: NotificationChannel.INCIDENT,
        }
        if self.channel is not expected[self.provider]:
            raise ValueError("notification provider does not match its channel")
        if self.provider is NotificationProvider.RESEND:
            if not self.recipients or self.sender is None:
                raise ValueError("email notifications require recipients and a sender")
            if not all(_email(value) for value in (*self.recipients, self.sender)):
                raise ValueError("email notification addresses are invalid")
        elif self.recipients or self.sender is not None:
            raise ValueError("non-email notification endpoints cannot contain email addresses")
        return self


class EmailNotificationEndpoint(Contract):
    id: Identifier
    organisation_id: Identifier
    email_address: str = Field(min_length=3, max_length=320)
    event_kinds: frozenset[NotificationKind] = Field(min_length=1)
    enabled: bool = True
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @field_validator("email_address")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalised = value.strip().lower()
        if not _email(normalised):
            raise ValueError("email address is invalid")
        return normalised


class NotificationTopic(Contract):
    id: Identifier
    label: str = Field(min_length=1, max_length=80)
    event_kinds: frozenset[NotificationKind] = Field(min_length=1)


class Notification(Contract):
    id: Identifier
    organisation_id: Identifier
    kind: NotificationKind
    severity: Severity
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=1024)
    link_path: str = Field(min_length=1, max_length=1024)
    resource_id: Identifier
    run_id: Identifier | None = None
    incident_id: Identifier | None = None
    approval_id: Identifier | None = None
    created_at: AwareDatetime
    read_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_link(self) -> "Notification":
        if not self.link_path.startswith("/") or self.link_path.startswith("//"):
            raise ValueError("notification links must be relative application paths")
        if "?" in self.link_path or "#" in self.link_path or "\\" in self.link_path:
            raise ValueError("notification links cannot contain capabilities or fragments")
        return self


class NotificationDelivery(Contract):
    id: Identifier
    organisation_id: Identifier
    notification_id: Identifier
    endpoint_id: Identifier
    endpoint_revision: int = Field(ge=0)
    provider: NotificationProvider
    state: NotificationState = NotificationState.PENDING
    available_at: AwareDatetime
    attempts: int = Field(default=0, ge=0)
    started_at: AwareDatetime | None = None
    lease_owner: Identifier | None = None
    lease_expires_at: AwareDatetime | None = None
    sent_at: AwareDatetime | None = None
    provider_receipt: str | None = Field(default=None, min_length=1, max_length=512)
    last_error: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_delivery(self) -> "NotificationDelivery":
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("notification lease owner and expiry must be set together")
        if self.state is NotificationState.SENDING and self.lease_owner is None:
            raise ValueError("a sending notification must hold a lease")
        if self.state is not NotificationState.SENDING and self.lease_owner is not None:
            raise ValueError("only a sending notification may hold a lease")
        if self.state is NotificationState.SENT:
            if self.sent_at is None or self.provider_receipt is None:
                raise ValueError("a sent notification requires a receipt and sent time")
        elif self.sent_at is not None or self.provider_receipt is not None:
            raise ValueError("only sent notifications may contain provider delivery evidence")
        return self


def _email(value: str) -> bool:
    local, separator, domain = value.rpartition("@")
    return bool(separator and local and "." in domain and not domain.startswith("."))
