import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from time import time
from typing import Any

from connectors.base.errors import ConnectorAuthenticationError
from core.errors import ResourceConflictError, ResourceNotFoundError
from executor.vault import Vault
from pymongo import ReturnDocument

from hosting.oauth import OAuth


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Grants:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.pending = vault.database.collection("host_pending")
        self.saved = vault.database.collection("host_grants")

    async def initialize(self) -> None:
        await self.pending.create_index("state", unique=True)
        await self.pending.create_index("expires_on", expireAfterSeconds=0)
        await self.saved.create_index([("organisation_id", 1), ("id", 1)], unique=True)

    async def begin(
        self, state: str, org: str, actor: str, session: str, details: dict[str, Any]
    ) -> None:
        await self.pending.insert_one(
            {
                "state": digest(state),
                "organisation_id": org,
                "actor": actor,
                "session": session,
                "expires_on": datetime.now(UTC) + timedelta(minutes=10),
                "value": self.vault.seal(details, "host-pending:" + digest(state)),
            }
        )

    async def owner(self, state: str, actor: str, session: str, platform: str) -> dict[str, Any]:
        if len(state) != 43:
            raise ResourceConflictError("Hosting connection expired; connect again")
        row = await self.pending.find_one(
            {
                "state": digest(state),
                "actor": actor,
                "session": session,
                "expires_on": {"$gt": datetime.now(UTC)},
            }
        )
        if not row:
            raise ResourceConflictError("Hosting connection expired; connect again")
        details = self.vault.open(row["value"], "host-pending:" + digest(state))
        if details["platform"] != platform:
            raise ResourceConflictError("Hosting authorization does not match this integration")
        return row | {"details": details}

    async def consume(self, state: str, actor: str, session: str, platform: str) -> dict[str, Any]:
        row = await self.owner(state, actor, session, platform)
        consumed = await self.pending.delete_one({"_id": row["_id"]})
        if not consumed.deleted_count:
            raise ResourceConflictError("Hosting authorization was already used")
        return row

    async def put(self, org: str, identity: str, grant: dict[str, Any]) -> None:
        await self.saved.insert_one(
            {
                "organisation_id": org,
                "id": identity,
                "revision": 0,
                "value": self.vault.seal(grant, f"host:{org}:{identity}"),
            }
        )

    async def get(self, org: str, identity: str) -> tuple[dict[str, Any], dict[str, Any]]:
        row = await self.saved.find_one({"organisation_id": org, "id": identity})
        if not row:
            raise ResourceNotFoundError("Hosting authorization is unavailable")
        if row.get("uncertain"):
            raise ConnectorAuthenticationError("Reconnect this hosting account")
        return row, self.vault.open(row["value"], f"host:{org}:{identity}")

    async def access(self, org: str, identity: str, oauth: OAuth) -> dict[str, Any]:
        async with asyncio.timeout(25):
            while True:
                row, grant = await self.get(org, identity)
                if row.get("lease_until") and row["lease_until"] <= datetime.now(UTC):
                    await self.saved.update_one(
                        {"_id": row["_id"], "lease": row.get("lease")},
                        {"$set": {"uncertain": True}},
                    )
                    raise ConnectorAuthenticationError("Reconnect this hosting account")
                if grant.get("expires_at", time() + 3600) > time() + 60:
                    return grant
                owner = secrets.token_hex(16)
                acquired = await self.saved.find_one_and_update(
                    {
                        "_id": row["_id"],
                        "revision": row["revision"],
                        "$or": [
                            {"lease_until": {"$exists": False}},
                            {"lease_until": {"$lte": datetime.now(UTC)}},
                        ],
                    },
                    {
                        "$set": {
                            "lease": owner,
                            "lease_until": datetime.now(UTC) + timedelta(seconds=30),
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                )
                if not acquired:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    updated = await oauth.refresh(grant)
                    changed = await self.saved.update_one(
                        {"_id": row["_id"], "revision": row["revision"], "lease": owner},
                        {
                            "$set": {"value": self.vault.seal(updated, f"host:{org}:{identity}")},
                            "$inc": {"revision": 1},
                            "$unset": {"lease": "", "lease_until": ""},
                        },
                    )
                    if not changed.modified_count:
                        raise ConnectorAuthenticationError("Reconnect this hosting account")
                    return updated
                except BaseException:
                    # A lost refresh response can contain an already-rotated token.
                    await self.saved.update_one(
                        {"_id": row["_id"], "lease": owner}, {"$set": {"uncertain": True}}
                    )
                    raise
