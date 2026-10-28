from datetime import UTC, datetime
from uuid import uuid4

import httpx
from contracts import NotificationKind, OutboxEvent, Severity, TeamInvitation
from core.notification import NotificationDispatcher, NotificationService
from core.storage.database import Database
from core.storage.deliveries import Deliveries
from core.storage.notification import NotificationStore
from emailer.notification import Mailer
from emailer.receipts import Receipts
from pydantic import SecretStr

from notification.events import run_notification


class Worker:
    def __init__(
        self,
        database: Database,
        http: httpx.AsyncClient,
        key: SecretStr | None,
        sender: str,
        origin: str,
    ) -> None:
        self.database, self.ready = database, key is not None
        self.notifications = NotificationService(
            NotificationStore(database), lambda: datetime.now(UTC)
        )
        self.receipts = Receipts(database, http, key)
        self.dispatcher = NotificationDispatcher(
            Deliveries(database),
            Mailer(database, http, key, sender, origin),
            "email_" + uuid4().hex,
            lambda: datetime.now(UTC),
        )

    async def drain(self) -> None:
        async for row in (
            self.database.collection("outbox")
            .find({"notified": {"$ne": True}})
            .sort("data.event.occurred_at", 1)
            .limit(100)
        ):
            event = OutboxEvent.model_validate(row["data"]).event
            value = run_notification(event)
            if value:
                kind, severity, title, body = value
                await self.notifications.emit(
                    event.id,
                    event.organisation_id,
                    kind,
                    severity,
                    title,
                    body,
                    "/dashboard/rotations",
                    event.run_id,
                    run_id=event.run_id,
                )
            await self.database.collection("outbox").update_one(
                {"_id": row["_id"]}, {"$set": {"notified": True}}
            )
        if self.ready:
            await self.invitations()
            await self.dispatcher.drain(maximum=20)
            await self.receipts.poll()

    async def invitations(self) -> None:
        async for row in (
            self.database.collection("invitations")
            .find({"data.accepted_at": None, "data.revoked_at": None})
            .limit(100)
        ):
            invite = TeamInvitation.model_validate(row["data"])
            if invite.expires_at <= datetime.now(UTC):
                continue
            org = await self.database.collection("organisations").find_one(
                {"id": invite.organisation_id}
            )
            if not org:
                continue
            name = org["data"]["name"]
            await self.notifications.emit(
                f"team-invited-{invite.id}-{invite.revision}",
                invite.organisation_id,
                NotificationKind.TEAM_INVITATION,
                Severity.LOW,
                f"Join {name} on Kinu",
                f"You were invited to join {name}. Sign in with this email to accept.",
                "/auth",
                invite.id,
            )
