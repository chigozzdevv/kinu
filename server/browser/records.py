from datetime import datetime

from contracts import (
    BrowserAction,
    BrowserActionRecord,
    BrowserActionStatus,
    BrowserSession,
    ComputerUseActivity,
    SecureCaptureResult,
)
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database


class Records:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.sessions = database.collection("browser_sessions")

    async def create(self, session: BrowserSession) -> BrowserSession:
        await self.sessions.insert_one(
            {
                "organisation_id": session.organisation_id,
                "id": session.id,
                "revision": session.revision,
                "data": session.model_dump(mode="json"),
            }
        )
        return session

    async def get(self, org: str, identity: str) -> BrowserSession:
        row = await self.sessions.find_one({"organisation_id": org, "id": identity})
        if not row:
            raise ResourceNotFoundError("Browser session is unavailable")
        return BrowserSession.model_validate(row["data"])

    async def update(
        self, organisation_id: str, session_id: str, expected_revision: int, changed: BrowserSession
    ) -> BrowserSession:
        result = await self.sessions.update_one(
            {"organisation_id": organisation_id, "id": session_id, "revision": expected_revision},
            {"$set": {"revision": changed.revision, "data": changed.model_dump(mode="json")}},
        )
        if not result.modified_count:
            raise ResourceConflictError("Browser session changed")
        return changed

    async def save_capture(self, result: SecureCaptureResult) -> SecureCaptureResult:
        await self.database.collection("browser_captures").insert_one(
            result.model_dump(mode="json")
        )
        return result

    async def complete_capture(
        self, current: BrowserSession, changed: BrowserSession, result: SecureCaptureResult
    ) -> BrowserSession:
        async with self.database.transaction(current.organisation_id) as transaction:
            saved = await self.sessions.update_one(
                {
                    "organisation_id": current.organisation_id,
                    "id": current.id,
                    "revision": current.revision,
                },
                {"$set": {"revision": changed.revision, "data": changed.model_dump(mode="json")}},
                session=transaction,
            )
            if not saved.modified_count:
                raise ResourceConflictError("Browser session changed during capture")
            await self.database.collection("browser_captures").insert_one(
                result.model_dump(mode="json"), session=transaction
            )
        return changed

    async def save_activity(self, activity: ComputerUseActivity) -> ComputerUseActivity:
        await self.database.collection("browser_activity").insert_one(
            activity.model_dump(mode="json")
        )
        return activity

    async def begin_action(
        self,
        current: BrowserSession,
        changed: BrowserSession,
        action: BrowserAction,
        authorised_at: datetime,
    ) -> BrowserSession:
        record = BrowserActionRecord(
            id=action.id,
            organisation_id=current.organisation_id,
            session_id=current.id,
            action=action,
            status=BrowserActionStatus.AUTHORIZED,
            authorised_at=authorised_at,
        )
        async with self.database.transaction(current.organisation_id) as transaction:
            saved = await self.sessions.update_one(
                {
                    "organisation_id": current.organisation_id,
                    "id": current.id,
                    "revision": current.revision,
                },
                {"$set": {"revision": changed.revision, "data": changed.model_dump(mode="json")}},
                session=transaction,
            )
            if not saved.modified_count:
                raise ResourceConflictError("Browser session changed before action")
            await self.database.collection("browser_actions").insert_one(
                record.model_dump(mode="json"), session=transaction
            )
        return changed

    async def finish_action(
        self,
        organisation_id: str,
        session_id: str,
        action_id: str,
        status: BrowserActionStatus,
        error: str | None,
        completed_at: datetime,
    ) -> BrowserActionRecord:
        from pymongo import ReturnDocument

        row = await self.database.collection("browser_actions").find_one_and_update(
            {
                "organisation_id": organisation_id,
                "session_id": session_id,
                "id": action_id,
                "status": "authorized",
            },
            {
                "$set": {
                    "status": status.value,
                    "error": error,
                    "completed_at": completed_at.isoformat(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not row:
            raise ResourceConflictError("Browser action changed")
        return BrowserActionRecord.model_validate(
            {key: value for key, value in row.items() if key != "_id"}
        )
