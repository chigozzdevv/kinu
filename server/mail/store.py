import json
from datetime import UTC, datetime, timedelta
from typing import Any

from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database
from pymongo.errors import DuplicateKeyError

from mail.models import Mailbox


class Store:
    def __init__(self, database: Database) -> None:
        self.collection = database.collection("mailboxes")

    async def initialize(self) -> None:
        await self.collection.create_index("pk", unique=True)
        await self.collection.create_index("expires_on", expireAfterSeconds=0)
        await self.collection.create_index([("enabled", 1), ("poll_due", 1)])

    async def get(self, key: str) -> dict[str, Any] | None:
        return await self.collection.find_one({"pk": key})

    async def pending(self, key: str, value: dict[str, Any]) -> None:
        await self.collection.insert_one(
            {
                **value,
                "pk": "pending:" + key,
                "expires_on": datetime.fromtimestamp(value["expires"], UTC),
            }
        )

    async def consume(
        self, state: str, session: str, actor: str, org: str, now: int
    ) -> dict[str, Any]:
        row = await self.collection.find_one_and_delete(
            {
                "pk": "pending:" + state,
                "binding": session,
                "actor": actor,
                "organisation_id": org,
                "expires": {"$gt": now},
            }
        )
        if not row:
            raise ResourceConflictError("Gmail authorization expired")
        return row

    async def mailbox(self, org: str, identity: str) -> tuple[Mailbox, dict[str, Any]]:
        item = await self.get(f"mailbox:{org}:{identity}")
        if item is None:
            raise ResourceNotFoundError("Gmail connection is unavailable")
        box = Mailbox.model_validate_json(item["data"])
        cursor = json.loads(item.get("cursor", "{}"))
        if cursor.get("checked"):
            box = box.model_copy(
                update={"last_checked_at": datetime.fromtimestamp(cursor["checked"], UTC)}
            )
        return box, item

    async def list(self, org: str) -> tuple[Mailbox, ...]:
        return tuple(
            [
                Mailbox.model_validate_json(item["data"])
                async for item in self.collection.find({"workspace": org}).limit(20)
            ]
        )

    async def save(
        self,
        mailbox: Mailbox,
        grant: str | None,
        previous: int | None,
        *,
        cursor: dict[str, Any] | None = None,
    ) -> None:
        key = f"mailbox:{mailbox.organisation_id}:{mailbox.id}"
        now = int(datetime.now(UTC).timestamp())
        item: dict[str, Any] = {
            "pk": key,
            "data": mailbox.model_dump_json(exclude={"last_checked_at"}),
            "rev": mailbox.revision,
            "enabled": mailbox.status == "ready",
            "cursor": json.dumps(cursor or {"since": now, "start": now}),
        }
        if mailbox.status != "disabled":
            item["workspace"] = mailbox.organisation_id
        if grant is not None:
            item["grant"] = grant
        if mailbox.status == "ready":
            item["poll_due"] = 0
        try:
            if previous is None:
                await self.collection.insert_one(item)
            else:
                result = await self.collection.replace_one({"pk": key, "rev": previous}, item)
                if not result.matched_count:
                    raise ResourceConflictError("Gmail connection changed")
        except DuplicateKeyError:
            raise ResourceConflictError("Gmail connection already exists") from None

    async def due(self, now: int) -> tuple[dict[str, Any], ...]:
        return tuple(
            await self.collection.find({"enabled": True, "poll_due": {"$lte": now}})
            .limit(10)
            .to_list()
        )

    async def claim(self, key: str, revision: int, owner: str, now: int) -> bool:
        result = await self.collection.update_one(
            {
                "pk": key,
                "rev": revision,
                "enabled": True,
                "$or": [{"lease_until": {"$exists": False}}, {"lease_until": {"$lt": now}}],
            },
            {"$set": {"lease_owner": owner, "lease_until": now + 300}},
        )
        return bool(result.matched_count)

    async def checkpoint(
        self,
        box: Mailbox,
        owner: str,
        cursor: dict[str, Any],
        grant: str | None = None,
        *,
        release: bool = False,
    ) -> None:
        values: dict[str, Any] = {"cursor": json.dumps(cursor)}
        update: dict[str, Any] = {"$set": values}
        if grant is not None:
            values["grant"] = grant
        if release:
            values["poll_due"] = int(datetime.now(UTC).timestamp()) + 300
            update["$unset"] = {"lease_owner": "", "lease_until": ""}
        result = await self.collection.update_one(
            {
                "pk": f"mailbox:{box.organisation_id}:{box.id}",
                "rev": box.revision,
                "enabled": True,
                "lease_owner": owner,
            },
            update,
        )
        if not result.matched_count:
            raise ResourceConflictError("Gmail connection changed")

    async def message(
        self, org: str, box: str, identity: str, value: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        query = {"pk": f"message:{org}:{box}:{identity}"}
        if value is not None:
            await self.collection.update_one(
                query,
                {
                    "$set": {
                        "result": value,
                        "expires_on": datetime.now(UTC) + timedelta(days=90),
                    }
                },
                upsert=True,
            )
        item = await self.collection.find_one(query)
        return item["result"] if item else None
