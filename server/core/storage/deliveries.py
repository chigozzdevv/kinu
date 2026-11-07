from datetime import datetime, timedelta
from typing import Any

from contracts import Notification, NotificationDelivery, NotificationEndpoint
from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection

from core.errors import LeaseConflictError
from core.storage.database import Database
from core.storage.notification import NotificationClaim
from core.storage.records import Records


class Deliveries:
    def __init__(self, database: Database) -> None:
        self.database = database

    @property
    def collection(self) -> AsyncCollection[dict[str, Any]]:
        return self.database.collection("notification_deliveries")

    async def claim(
        self, owner_id: str, now: datetime, lease_duration: timedelta, limit: int
    ) -> tuple[NotificationClaim, ...]:
        results = []
        for _ in range(limit):
            row = await self.collection.find_one_and_update(
                {
                    "$or": [
                        {"state": "pending", "available_at": {"$lte": now}},
                        {"state": "sending", "lease_expires_at": {"$lte": now}},
                    ]
                },
                [
                    {
                        "$set": {
                            "state": "sending",
                            "lease_owner": owner_id,
                            "lease_expires_at": now + lease_duration,
                            "started_at": {"$ifNull": ["$started_at", now]},
                            "attempts": {"$add": ["$attempts", 1]},
                        },
                    }
                ],
                sort=[("available_at", 1), ("id", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if row is None:
                break
            delivery = NotificationDelivery.model_validate(
                {k: v for k, v in row.items() if k != "_id"}
            )
            async with Records(self.database).read() as rows:
                notification = await rows.get(
                    Notification, delivery.organisation_id, delivery.notification_id
                )
                endpoint = await rows.get(
                    NotificationEndpoint, delivery.organisation_id, delivery.endpoint_id
                )
            results.append(NotificationClaim(delivery.id, delivery, notification, endpoint))
        return tuple(results)

    async def problems(self, organisation_id: str, limit: int) -> tuple[NotificationDelivery, ...]:
        rows = (
            await self.collection.find(
                {
                    "organisation_id": organisation_id,
                    "$or": [{"state": "failed"}, {"status_error": {"$exists": True}}],
                }
            )
            .sort("available_at", -1)
            .to_list(limit)
        )
        return tuple(
            NotificationDelivery.model_validate({k: v for k, v in row.items() if k != "_id"})
            for row in rows
        )

    async def mark_sent(
        self, claim: NotificationClaim, owner_id: str, receipt: str, sent_at: datetime
    ) -> None:
        await self.finish(
            claim,
            owner_id,
            {
                "state": "sent",
                "sent_at": sent_at,
                "provider_receipt": receipt,
                "last_error": None,
                "provider_status": "accepted",
                "status_due": sent_at + timedelta(minutes=1),
            },
        )

    async def mark_failed(
        self,
        claim: NotificationClaim,
        owner_id: str,
        error: str,
        available_at: datetime,
        terminal: bool,
    ) -> None:
        await self.finish(
            claim,
            owner_id,
            {
                "state": "failed" if terminal else "pending",
                "available_at": available_at,
                "last_error": error,
            },
        )

    async def finish(self, claim: NotificationClaim, owner: str, fields: dict[str, object]) -> None:
        result = await self.collection.update_one(
            {
                "organisation_id": claim.delivery.organisation_id,
                "id": claim.delivery.id,
                "state": "sending",
                "lease_owner": owner,
                "attempts": claim.delivery.attempts,
                "lease_expires_at": claim.delivery.lease_expires_at,
            },
            {"$set": fields | {"lease_owner": None, "lease_expires_at": None}},
        )
        if not result.modified_count:
            raise LeaseConflictError("Email delivery lease changed")
