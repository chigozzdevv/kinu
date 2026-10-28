import re
from datetime import UTC, datetime, timedelta

import httpx
from core.storage.database import Database
from pydantic import SecretStr
from pymongo import ReturnDocument

TERMINAL = {"delivered", "bounced", "complained", "failed", "opened", "clicked", "canceled"}


class Receipts:
    def __init__(self, database: Database, http: httpx.AsyncClient, key: SecretStr | None) -> None:
        self.collection, self.http, self.key = (
            database.collection("notification_deliveries"),
            http,
            key,
        )

    async def poll(self) -> None:
        if not self.key:
            return
        for _ in range(2):
            now = datetime.now(UTC)
            row = await self.collection.find_one_and_update(
                {"state": "sent", "status_due": {"$lte": now}},
                {"$set": {"status_due": now + timedelta(minutes=5)}},
                sort=[("status_due", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                break
            identity = row["provider_receipt"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", identity):
                continue
            try:
                response = await self.http.get(
                    "https://api.resend.com/emails/" + identity,
                    headers={"Authorization": "Bearer " + self.key.get_secret_value()},
                    timeout=10,
                    follow_redirects=False,
                )
                status = response.json().get("last_event") if response.is_success else None
                fields: dict[str, object] = {"status_checked_at": now}
                terminal = (
                    status in TERMINAL
                    or response.status_code in {401, 403, 404}
                    or now - row["sent_at"] > timedelta(days=1)
                )
                if isinstance(status, str) and status in TERMINAL | {
                    "sent",
                    "delivery_delayed",
                    "scheduled",
                    "queued",
                }:
                    fields["provider_status"] = status
                if response.status_code in {401, 403, 404}:
                    fields["status_error"] = "Provider receipt lookup is unavailable"
                update: dict[str, object] = {"$set": fields}
                if terminal:
                    update["$unset"] = {"status_due": ""}
                await self.collection.update_one(
                    {"_id": row["_id"], "provider_receipt": identity}, update
                )
            except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                continue
