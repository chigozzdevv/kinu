from contracts import (
    Approval,
    ApprovalEvidenceKind,
    ApprovalEvidenceSnapshot,
    BrowserActionResult,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    RotationHistory,
    SecureCaptureResult,
    Stage,
    StageExecutionResult,
)
from policy import digest

from core.errors import ResourceConflictError, ResourceNotFoundError
from core.history import BrowserEvidenceReader, _activity
from core.storage.database import Database
from core.storage.records import Records
from core.storage.runs import RunStore


class HistoryStore:
    def __init__(self, database: Database, images: BrowserEvidenceReader | None = None) -> None:
        self._database, self._images = database, images

    async def get(self, organisation_id: str, run_id: str) -> RotationHistory:
        run = await RunStore(self._database).get(organisation_id, run_id)
        results = (
            await self._database.collection("executions")
            .find({"organisation_id": organisation_id, "run_id": run_id})
            .sort([("data.started_at", 1), ("stage", 1)])
            .to_list()
        )
        results.extend(
            await self._database.collection("execution_reviews")
            .find({"organisation_id": organisation_id, "run_id": run_id})
            .to_list()
        )
        results.sort(key=lambda row: row["data"]["started_at"])
        async with Records(self._database).read() as rows:
            approvals = await rows.match(Approval, organisation_id, {"run_id": run_id})
        return RotationHistory(
            run_id=run_id,
            computer_use=await self._browser_activity(organisation_id, run_id),
            stages=tuple(
                _activity(StageExecutionResult.model_validate(row["data"]), run, approvals)
                for row in results
            ),
        )

    async def _browser_activity(self, org: str, run: str) -> tuple[ComputerUseActivity, ...]:
        activities = [
            ComputerUseActivity.model_validate(
                {key: value for key, value in row.items() if key != "_id"}
            )
            async for row in self._database.collection("browser_activity")
            .find({"organisation_id": org, "run_id": run})
            .sort("recorded_at", 1)
        ]
        sessions = {
            item.session_id
            for item in activities
            if item.phase is ComputerUseActivityPhase.EXECUTION
            and item.status is ComputerUseActivityStatus.SUCCEEDED
            and item.stage is Stage.CREATE
            and item.result is None
        }
        captures = (
            {
                row["session_id"]: SecureCaptureResult.model_validate(
                    {key: value for key, value in row.items() if key != "_id"}
                )
                async for row in self._database.collection("browser_captures").find(
                    {"organisation_id": org, "session_id": {"$in": sorted(sessions)}}
                )
            }
            if sessions
            else {}
        )
        return tuple(
            item.model_copy(
                update={
                    "result": BrowserActionResult(
                        provider_id=captures[item.session_id].provider_id,
                        secret_reference=captures[item.session_id].secret_reference,
                    )
                }
            )
            if item.session_id in captures
            and item.phase is ComputerUseActivityPhase.EXECUTION
            and item.status is ComputerUseActivityStatus.SUCCEEDED
            and item.stage is Stage.CREATE
            and item.result is None
            else item
            for item in activities
        )

    async def approval_evidence(
        self, organisation_id: str, approval_id: str
    ) -> ApprovalEvidenceSnapshot:
        async with Records(self._database).read() as rows:
            approval = await rows.get(Approval, organisation_id, approval_id)
        results = []
        for collection in ("executions", "execution_reviews"):
            results.extend(
                await self._database.collection(collection)
                .find(
                    {
                        "organisation_id": organisation_id,
                        "run_id": approval.run_id,
                        "stage": {"$in": ["verify", "observe"]},
                    }
                )
                .to_list()
            )
        for row in results:
            evidence = StageExecutionResult.model_validate(row["data"])
            if digest(evidence) == approval.evidence_hash:
                return ApprovalEvidenceSnapshot(
                    approval_id=approval.id,
                    evidence_hash=approval.evidence_hash,
                    kind=ApprovalEvidenceKind.VERIFICATION,
                    status=evidence.status.value,
                    checks=tuple(sorted(evidence.checks)),
                    evidence_count=len(evidence.evidence_ids),
                    recorded_at=evidence.completed_at,
                )
        raise ResourceConflictError("Approval evidence is unavailable or no longer matches")

    async def image(self, organisation_id: str, run_id: str, activity_id: str) -> tuple[bytes, str]:
        await RunStore(self._database).get(organisation_id, run_id)
        row = await self._database.collection("browser_activity").find_one(
            {"organisation_id": organisation_id, "run_id": run_id, "id": activity_id}
        )
        if not row or self._images is None:
            raise ResourceNotFoundError("This rotation has no browser screenshot")
        activity = ComputerUseActivity.model_validate(
            {key: value for key, value in row.items() if key != "_id"}
        )
        if not activity.image_reference or not activity.image_digest:
            raise ResourceNotFoundError("This step has no saved browser screenshot")
        if activity.image_reference != f"kinu://browser-images/{run_id}/{activity_id}":
            raise ResourceConflictError("Browser screenshot belongs to another activity")
        return await self._images.read_image(
            organisation_id, run_id, activity_id, activity.image_digest
        )
