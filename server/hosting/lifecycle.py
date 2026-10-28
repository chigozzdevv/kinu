from datetime import UTC, datetime
from typing import Any

from contracts import Connection, ConnectionStatus, ConsumerBinding
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records

from hosting.grants import Grants


class Lifecycle:
    def __init__(self, database: Database, grants: Grants) -> None:
        self.database, self.grants = database, grants

    async def pause(self, org: str, identity: str, revision: int) -> Connection:
        async with Records(self.database).write(org) as rows:
            connection = await rows.get(Connection, org, identity)
            if (
                connection.archived_at
                or connection.authorization_reference != "hosting://" + identity
            ):
                raise ResourceConflictError("Choose an active hosting connection")
            credentials = {
                binding.credential_id
                for binding in await rows.list(ConsumerBinding, org)
                if binding.runtime_connection_id == identity
            }
            if await self.database.collection("run_locks").find_one(
                {"organisation_id": org, "credential_id": {"$in": sorted(credentials)}},
                session=rows.session,
            ):
                raise ResourceConflictError("Finish this account's rotations before reconnecting")
            return await rows.replace(
                connection.model_copy(
                    update={
                        "status": ConnectionStatus.SETUP_REQUIRED,
                        "updated_at": datetime.now(UTC),
                        "revision": revision + 1,
                    }
                ),
                revision,
            )

    async def replace(self, org: str, grant: dict[str, Any]) -> Connection:
        identity = grant["id"]
        row = await self.grants.saved.find_one({"organisation_id": org, "id": identity})
        if not row or row.get("lease_until", datetime.min.replace(tzinfo=UTC)) > datetime.now(UTC):
            raise ResourceConflictError("Hosting access changed; try reconnecting again")
        connection = await self.pause(org, identity, grant.pop("expected_revision"))
        changed = await self.grants.saved.update_one(
            {"_id": row["_id"], "revision": row["revision"], "lease": row.get("lease")},
            {
                "$set": {
                    "value": self.grants.vault.seal(grant, f"host:{org}:{identity}"),
                    "active": True,
                },
                "$inc": {"revision": 1},
                "$unset": {"lease": "", "lease_until": "", "uncertain": ""},
            },
        )
        if not changed.modified_count:
            raise ResourceConflictError("Hosting access changed; try reconnecting again")
        return connection

    async def forget(self, org: str, identity: str) -> None:
        async with Records(self.database).read() as rows:
            connection = await rows.get(Connection, org, identity)
        if (
            not connection.archived_at
            or connection.authorization_reference != "hosting://" + identity
        ):
            raise ResourceConflictError("Disconnect the hosting connection first")
        await self.grants.saved.delete_one({"organisation_id": org, "id": identity})
