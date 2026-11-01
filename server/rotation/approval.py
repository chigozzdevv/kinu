import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from contracts import (
    Approval,
    ApprovalDecision,
    ProtectedAction,
    RotationRun,
    Stage,
    StageExecutionResult,
    StageExecutionStatus,
)
from core.approval import ApprovalService
from core.approval.store import ApprovalStore
from core.audit.writer import AuditWriter
from core.auth import AuthenticatedIdentity
from core.errors import ApprovalError, ResourceConflictError, ResourceNotFoundError
from core.ids import new_id
from core.notification import NotificationService
from core.storage.database import Database
from core.storage.notification import NotificationStore
from core.storage.records import Records
from executor.vault import Vault
from policy import digest

from rotation.journal import Journal
from rotation.ports import Effects
from rotation.stages import stable
from rotation.state import Snapshot


class RevocationApprovals:
    def __init__(
        self, database: Database, vault: Vault, effects: Effects, audit: AuditWriter
    ) -> None:
        self.database, self.vault, self.effects = database, vault, effects
        self.journal = Journal(database)
        self.service = ApprovalService(
            ApprovalStore(database),
            lambda: datetime.now(UTC),
            NotificationService(NotificationStore(database), lambda: datetime.now(UTC)),
            audit,
        )

    async def ensure_approver(self, run: RotationRun) -> None:
        async for member in self.database.collection("members").find(
            {
                "organisation_id": run.organisation_id,
                "data.enabled": True,
                "data.roles": "administrator",
            }
        ):
            actor = AuthenticatedIdentity(subject=member["subject"], issuer="local").actor_id
            if actor != run.trigger.actor_id:
                return
        raise ResourceConflictError("Add another workspace administrator to approve this rotation")

    async def current(self, run: RotationRun) -> tuple[Approval, ProtectedAction, str] | None:
        try:
            saved = await self.vault.get(run.organisation_id, stable("revocation", run.id))
        except ResourceNotFoundError:
            return None
        async with Records(self.database).read() as rows:
            approval = await rows.get(Approval, run.organisation_id, saved["approval_id"])
            action = await rows.get(ProtectedAction, run.organisation_id, approval.action_id)
        if approval.run_id != run.id:
            raise ApprovalError("Approval belongs to another rotation")
        return approval, action, str(saved["token"])

    async def ready(self, run: RotationRun) -> bool:
        current = await self.current(run)
        return current is None or (
            current[0].decision is not ApprovalDecision.PENDING
            or current[0].expires_at <= datetime.now(UTC)
        )

    async def request(
        self, state: Snapshot, run: RotationRun, evidence: StageExecutionResult
    ) -> None:
        if not run.plan_hash or not state.current.provider_id:
            raise ApprovalError("Approval requires a bound plan and predecessor")
        identity, token = new_id("approval"), secrets.token_urlsafe(32)
        action = ProtectedAction(
            id=new_id("action"),
            organisation_id=run.organisation_id,
            run_id=run.id,
            kind="provider.revokeCredential",
            resource=state.current.provider_id,
            credential_id=run.credential_id,
            generation_id=state.current.id,
            provider_id=state.current.provider_id,
            control_version=run.control_version,
            playbook_version=run.browser_playbook_version,
            plan_hash=run.plan_hash,
            evidence_hash=digest(evidence),
        )
        await self.vault.put(
            run.organisation_id,
            stable("revocation", run.id),
            {"approval_id": identity, "token": token},
        )
        await self.service.request(
            identity,
            action,
            run.plan_hash,
            action.evidence_hash,
            run.trigger.actor_id,
            datetime.now(UTC) + timedelta(hours=24),
            token,
        )

    async def observe(self, state: Snapshot, run: RotationRun) -> StageExecutionResult:
        created = await self.journal.get(run.organisation_id, run.id, Stage.CREATE)
        if not created:
            raise ApprovalError("Approval requires the recorded replacement")
        started = datetime.now(UTC)
        output = await self.effects.observe(
            state, created.output | {"run_id": run.id, "fencing_token": run.fencing_token}
        )
        identity = new_id("review")
        evidence = StageExecutionResult(
            id=identity,
            organisation_id=run.organisation_id,
            run_id=run.id,
            stage=Stage.OBSERVE,
            status=StageExecutionStatus.SUCCEEDED,
            checks=frozenset({"consumers-current"}),
            evidence_ids=(identity,),
            output=output,
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        await self.journal.review(evidence)
        return evidence

    async def authorize(self, state: Snapshot, run: RotationRun) -> dict[str, Any] | None:
        current = await self.current(run)
        if current is None:
            evidence = await self.journal.get(run.organisation_id, run.id, Stage.OBSERVE)
            if evidence is None:
                raise ApprovalError("Approval requires completed observation")
            await self.request(state, run, evidence)
            return None
        approval, action, token = current
        if approval.decision in {ApprovalDecision.REJECTED, ApprovalDecision.CANCELLED}:
            raise ApprovalError("Revocation was declined; restoring the predecessor")
        if approval.decision is ApprovalDecision.PENDING and approval.expires_at > datetime.now(
            UTC
        ):
            return None
        evidence = await self.observe(state, run)
        if (
            approval.decision is not ApprovalDecision.APPROVED
            or approval.expires_at <= datetime.now(UTC)
        ):
            await self.request(state, run, evidence)
            return None
        await self.service.consume(
            run.organisation_id,
            approval.id,
            token,
            action,
            action.plan_hash,
            action.evidence_hash,
            run.lease.owner_id if run.lease else "worker_executor",
        )
        return {"approval_id": approval.id, "verification_review": evidence.id}
