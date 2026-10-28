from datetime import UTC, datetime

import httpx
from connectors.base.errors import ConnectorError
from contracts import Notification, NotificationEndpoint, NotificationKind, NotificationProvider
from core.auth import AuthenticatedIdentity, PrincipalGrant
from core.auth.access import ROLE_PERMISSIONS
from core.storage.database import Database
from core.storage.records import Records
from pydantic import SecretStr

from emailer.templates import message

REFERENCE = "kinu://email/resend"


class Mailer:
    def __init__(
        self,
        database: Database,
        http: httpx.AsyncClient,
        key: SecretStr | None,
        sender: str,
        origin: str,
    ) -> None:
        self.database, self.http, self.key, self.sender, self.origin = (
            database,
            http,
            key,
            sender,
            origin,
        )

    async def send(
        self, notification: Notification, endpoint: NotificationEndpoint, delivery_id: str
    ) -> str:
        if (
            not self.key
            or endpoint.provider is not NotificationProvider.RESEND
            or endpoint.auth_reference != REFERENCE
        ):
            raise ConnectorError("email-config", "Email delivery is not configured")
        async with Records(self.database).read() as rows:
            latest = await rows.get(NotificationEndpoint, endpoint.organisation_id, endpoint.id)
        if latest != endpoint or not latest.enabled:
            raise ConnectorError("endpoint-changed", "Email destination changed before delivery")
        await self.recipient(notification, endpoint)
        subject, text, html = message(
            notification.title,
            notification.body,
            self.origin + notification.link_path,
            notification.kind is NotificationKind.TEAM_INVITATION,
        )
        try:
            response = await self.http.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": "Bearer " + self.key.get_secret_value(),
                    "Idempotency-Key": delivery_id,
                },
                json={
                    "from": self.sender,
                    "to": list(endpoint.recipients),
                    "subject": subject,
                    "text": text,
                    "html": html,
                },
                timeout=10,
                follow_redirects=False,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise ConnectorError(
                    "email-temporary", "Email provider is unavailable", retryable=True
                )
            if not response.is_success:
                raise ConnectorError("email-rejected", "Email provider rejected this message")
            identity = response.json().get("id")
            if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
                raise ValueError
            return identity
        except httpx.HTTPError:
            raise ConnectorError(
                "email-temporary", "Email request was interrupted", retryable=True
            ) from None
        except ValueError:
            raise ConnectorError(
                "email-response", "Email provider returned no receipt", retryable=True
            ) from None

    async def recipient(self, notification: Notification, endpoint: NotificationEndpoint) -> None:
        org = notification.organisation_id
        if notification.kind is NotificationKind.TEAM_INVITATION:
            row = await self.database.collection("invitations").find_one(
                {"organisation_id": org, "id": notification.resource_id}
            )
            if row:
                invite = row["data"]
                from contracts import TeamInvitation

                current = TeamInvitation.model_validate(invite)
                if (
                    not current.accepted_at
                    and not current.revoked_at
                    and current.updated_at <= notification.created_at
                    and current.expires_at > datetime.now(UTC)
                    and endpoint.recipients == (current.email,)
                ):
                    return
            raise ConnectorError("invitation-inactive", "Invitation is no longer active")
        async for row in self.database.collection("members").find(
            {"organisation_id": org, "data.enabled": True}
        ):
            grant = PrincipalGrant.model_validate(row["data"])
            if (
                AuthenticatedIdentity(subject=grant.subject, issuer="membership").actor_id
                == endpoint.principal_id
            ):
                from notification.topics import TOPICS

                permissions = {item for role in grant.roles for item in ROLE_PERMISSIONS[role]}
                if any(
                    notification.kind in topic.event_kinds and permission in permissions
                    for topic, permission in TOPICS
                ):
                    return
        raise ConnectorError("recipient-inactive", "Email recipient no longer has access")
