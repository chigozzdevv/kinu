from contracts import (
    Approval,
    ApprovalEvidenceKind,
    ApprovalEvidenceSnapshot,
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
        async with self._database.connection() as sql:
            results = await (
                await sql.execute(
                    "SELECT data FROM kinu.executions WHERE organisation_id=%s AND run_id=%s "
                    "ORDER BY data->>'started_at',stage",
                    (organisation_id, run_id),
                )
            ).fetchall()
        async with Records(self._database).read() as rows:
            approvals = await rows.match(Approval, organisation_id, {"run_id": run_id})
        return RotationHistory(
            run_id=run_id,
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
        async with self._database.connection() as sql:
            results = await (
                await sql.execute(
                    "SELECT data FROM kinu.executions WHERE organisation_id=%s AND run_id=%s "
                    "AND stage IN ('verify','observe')",
                    (organisation_id, approval.run_id),
                )
            ).fetchall()
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
