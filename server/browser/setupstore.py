from datetime import UTC, datetime

from contracts import Connection, ConnectionStatus, ManagedCredential, SetupSession
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.records import Records

from browser.catalog import Catalog


class Store:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.collection = catalog.vault.database.collection("browser_setups")

    async def save(self, session: SetupSession) -> None:
        await self.collection.update_one(
            {"organisation_id": session.organisation_id, "id": session.id},
            {"$set": {"data": session.model_dump(mode="json")}},
            upsert=True,
        )

    async def get(self, org: str, identity: str) -> SetupSession:
        row = await self.collection.find_one({"organisation_id": org, "id": identity})
        if not row:
            raise ResourceNotFoundError("Browser setup is unavailable")
        return SetupSession.model_validate(row["data"])

    async def authenticate(self, original: Connection, reference: str) -> Connection:
        org = original.organisation_id
        async with Records(self.catalog.database).write(org) as rows:
            current = await rows.get(Connection, org, original.id)
            if current != original or current.archived_at:
                raise ResourceConflictError("Connection changed during browser setup")
            keys = await rows.match(
                ManagedCredential, org, {"connection_id": current.id, "archived_at": None}
            )
            if await self.catalog.database.collection("run_locks").find_one(
                {"organisation_id": org, "credential_id": {"$in": [key.id for key in keys]}},
                session=rows.session,
            ):
                raise ResourceConflictError(
                    "Finish this provider's active rotations before reconnecting"
                )
            now = datetime.now(UTC)
            return await rows.replace(
                current.model_copy(
                    update={
                        "authorization_reference": reference,
                        "status": ConnectionStatus.READY,
                        "authenticated_at": now,
                        "authorization_expires_at": None,
                        "last_validated_at": now,
                        "updated_at": now,
                        "revision": current.revision + 1,
                    }
                ),
                current.revision,
            )
