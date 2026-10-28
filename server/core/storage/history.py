from contracts import (
    Approval,
    ApprovalEvidenceKind,
    ApprovalEvidenceSnapshot,
    ComputerUseActivity,
    RotationHistory,
    StageExecutionResult,
)
from policy import digest

from core.errors import ResourceConflictError, ResourceNotFoundError
from core.history import _activity
from core.storage.database import Database
from core.storage.records import Records
from core.storage.runs import RunStore


class HistoryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, organisation_id: str, run_id: str) -> RotationHistory:
        run = await RunStore(self._database).get(organisation_id, run_id)
        results = (
            await self._database.collection("executions")
            .find({"organisation_id": organisation_id, "run_id": run_id})
            .sort([("data.started_at", 1), ("stage", 1)])
            .to_list()
        )
        async with Records(self._database).read() as rows:
            approvals = await rows.match(Approval, organisation_id, {"run_id": run_id})
        return RotationHistory(
            run_id=run_id,
            computer_use=tuple(
                [
                    ComputerUseActivity.model_validate(
                        {key: value for key, value in row.items() if key != "_id"}
                    )
                    async for row in self._database.collection("browser_activity")
                    .find({"organisation_id": organisation_id, "run_id": run_id})
                    .sort("recorded_at", 1)
                ]
            ),
            stages=tuple(
                _activity(StageExecutionResult.model_validate(row["data"]), run, approvals)
                for row in results
            ),
        )

    async def approval_evidence(
        self, organisation_id: str, approval_id: str
    ) -> ApprovalEvidenceSnapshot:
        async with Records(self._database).read() as rows:
            approval = await rows.get(Approval, organisation_id, approval_id)
        results = (
            await self._database.collection("executions")
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

    async def input_image(
        self, organisation_id: str, run_id: str, activity_id: str
    ) -> tuple[bytes, str]:
        raise ResourceNotFoundError("This rotation has no browser screenshot")
