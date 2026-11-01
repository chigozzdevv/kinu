from contracts import WalkthroughSource

from core.errors import PlaybookError, ResourceNotFoundError
from core.storage.database import Database


class Sources:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, org: str, playbook: str, source: str) -> WalkthroughSource:
        row = await self.database.collection("playbook_sources").find_one(
            {"organisation_id": org, "playbook_id": playbook, "id": source}
        )
        if not row:
            raise ResourceNotFoundError("Playbook source is unavailable")
        return WalkthroughSource.model_validate(row["data"])

    async def reserve(self, source: WalkthroughSource) -> tuple[WalkthroughSource, bool]:
        rows = self.database.collection("playbook_sources")
        async with self.database.transaction(source.organisation_id) as session:
            query = {"organisation_id": source.organisation_id, "id": source.id}
            existing = await rows.find_one(query, session=session)
            if existing:
                saved = WalkthroughSource.model_validate(existing["data"])
                if (saved.playbook_id, saved.kind, saved.resource) != (
                    source.playbook_id,
                    source.kind,
                    source.resource,
                ):
                    raise PlaybookError("This source already identifies different instructions")
                return saved, False
            await rows.insert_one(
                query | {"playbook_id": source.playbook_id, "data": source.model_dump(mode="json")},
                session=session,
            )
        return source, True
