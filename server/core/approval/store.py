import hmac
from datetime import datetime

from agents.redact import redact
from contracts import (
    Approval,
    ApprovalDecision,
    CredentialGeneration,
    ManagedCredential,
    ProtectedAction,
    RotationRun,
    RunStatus,
    StageExecutionResult,
    StageExecutionStatus,
)
from policy import digest

from core.auth import AuthenticatedIdentity
from core.errors import ApprovalError, RevisionConflictError
from core.storage.database import Database
from core.storage.records import Records, RecordTransaction


class ApprovalStore:
    def __init__(self, database: Database) -> None:
        self._records = Records(database)

    async def _binding(self, rows: RecordTransaction, action: ProtectedAction) -> None:
        row = await rows.database.collection("runs").find_one(
            {"organisation_id": action.organisation_id, "id": action.run_id},
            session=rows.session,
        )
        if row is None:
            raise ApprovalError("approval run is unavailable")
        run = RotationRun.model_validate(row["data"])
        credential = await rows.get(ManagedCredential, action.organisation_id, action.credential_id)
        generation = await rows.get(
            CredentialGeneration, action.organisation_id, action.generation_id
        )
        if (
            run.status not in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.RECOVERING}
            or run.credential_id != action.credential_id
            or run.control_version != action.control_version
            or credential.control_version != action.control_version
            or run.plan_hash != action.plan_hash
            or action.generation_id not in {run.current_generation_id, run.target_generation_id}
            or generation.credential_id != credential.id
            or generation.provider_id != action.provider_id
        ):
            raise ApprovalError("approval no longer matches the active run and credential")
        evidence = []
        for collection in ("executions", "execution_reviews"):
            evidence.extend(
                await rows.database.collection(collection)
                .find(
                    {
                        "organisation_id": action.organisation_id,
                        "run_id": action.run_id,
                        "stage": {"$in": ["verify", "observe"]},
                    },
                    session=rows.session,
                )
                .to_list()
            )
        if not any(
            result.status is StageExecutionStatus.SUCCEEDED
            and digest(result) == action.evidence_hash
            for result in (StageExecutionResult.model_validate(row["data"]) for row in evidence)
        ):
            raise ApprovalError("approval requires matching saved verification evidence")

    async def _is_only_administrator(
        self, rows: RecordTransaction, organisation_id: str, actor_id: str
    ) -> bool:
        members = await rows.database.collection("members").find(
            {
                "organisation_id": organisation_id,
                "data.enabled": True,
                "data.roles": "administrator",
            },
            session=rows.session,
        ).to_list()
        administrator_ids = {
            AuthenticatedIdentity(subject=member["subject"], issuer="local").actor_id
            for member in members
        }
        return administrator_ids == {actor_id}

    async def create(self, approval: Approval, action: ProtectedAction) -> Approval:
        if redact(action.parameters) != action.parameters:
            raise ApprovalError("protected actions cannot contain secret values")
        async with self._records.write(approval.organisation_id) as rows:
            await self._binding(rows, action)
            existing = await rows.match(Approval, approval.organisation_id, {"id": approval.id})
            if existing:
                excluded = {
                    "created_at",
                    "decision",
                    "approver_id",
                    "decided_at",
                    "consumed_at",
                    "revision",
                }
                if existing[0].model_dump(exclude=excluded) != approval.model_dump(
                    exclude=excluded
                ):
                    raise ApprovalError("approval identity already belongs to another request")
                return existing[0]
            actions = await rows.match(ProtectedAction, action.organisation_id, {"id": action.id})
            if actions and actions[0] != action:
                raise ApprovalError(
                    "protected action identity already belongs to another operation"
                )
            if not actions:
                await rows.add(action, action.organisation_id)
            return await rows.add(approval, approval.organisation_id)

    async def list_approvals(self, organisation_id: str, limit: int) -> tuple[Approval, ...]:
        async with self._records.read() as rows:
            values = await rows.list(Approval, organisation_id)
        return tuple(
            sorted(values, key=lambda value: (value.created_at, value.id), reverse=True)[:limit]
        )

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
        decided_at: datetime,
    ) -> Approval:
        async with self._records.write(organisation_id) as rows:
            current = await rows.get(Approval, organisation_id, approval_id)
            if current.revision != expected_revision:
                raise RevisionConflictError("approval revision changed")
            if current.decision is not ApprovalDecision.PENDING or current.consumed_at is not None:
                raise ApprovalError("approval already has a decision")
            if decision is not ApprovalDecision.CANCELLED:
                if current.expires_at <= decided_at:
                    raise ApprovalError("approval evidence has expired")
                action = await rows.get(ProtectedAction, organisation_id, current.action_id)
                if current.requested_by == actor_id and not (
                    decision is ApprovalDecision.APPROVED
                    and action.kind == "provider.revokeCredential"
                    and await self._is_only_administrator(rows, organisation_id, actor_id)
                ):
                    raise ApprovalError(
                        "approval requires another operator unless you are the only administrator"
                    )
                await self._binding(rows, action)
            changed = current.model_copy(
                update={
                    "decision": decision,
                    "approver_id": actor_id,
                    "decided_at": decided_at,
                    "revision": current.revision + 1,
                }
            )
            return await rows.replace(changed, expected_revision)

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability_hash: str,
        action_digest: str,
        plan_hash: str,
        evidence_hash: str,
        consumed_at: datetime,
    ) -> Approval:
        async with self._records.write(organisation_id) as rows:
            current = await rows.get(Approval, organisation_id, approval_id)
            if (
                current.decision is not ApprovalDecision.APPROVED
                or current.consumed_at is not None
                or current.expires_at <= consumed_at
                or not hmac.compare_digest(current.capability_hash, capability_hash)
                or (current.action_digest, current.plan_hash, current.evidence_hash)
                != (action_digest, plan_hash, evidence_hash)
            ):
                raise ApprovalError("approval capability is expired, consumed or does not match")
            action = await rows.get(ProtectedAction, organisation_id, current.action_id)
            if digest(action) != action_digest:
                raise ApprovalError("protected action changed")
            await self._binding(rows, action)
            return await rows.replace(
                current.model_copy(
                    update={"consumed_at": consumed_at, "revision": current.revision + 1}
                ),
                current.revision,
            )
