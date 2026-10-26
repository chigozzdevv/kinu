import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from contracts import (
    Approval,
    ApprovalDecision,
    NotificationKind,
    ProtectedAction,
    Severity,
)
from policy import digest

from core.audit.writer import AuditWriter
from core.errors import ApprovalError

_LIST_SCAN_LIMIT = 500


@dataclass(frozen=True, slots=True)
class ApprovalCapability:
    approval: Approval
    token: str


class ApprovalRepository(Protocol):
    async def create(self, approval: Approval, action: ProtectedAction) -> Approval: ...

    async def list_approvals(self, organisation_id: str, limit: int) -> tuple[Approval, ...]: ...

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
        decided_at: datetime,
    ) -> Approval: ...

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability_hash: str,
        action_digest: str,
        plan_hash: str,
        evidence_hash: str,
        consumed_at: datetime,
    ) -> Approval: ...


class ApprovalNotifier(Protocol):
    async def emit(
        self,
        event_id: str,
        organisation_id: str,
        kind: NotificationKind,
        severity: Severity,
        title: str,
        body: str,
        link_path: str,
        resource_id: str,
        run_id: str | None = None,
        incident_id: str | None = None,
        approval_id: str | None = None,
    ) -> tuple[object, bool]: ...


class ApprovalService:
    def __init__(
        self,
        repository: ApprovalRepository,
        clock: Callable[[], datetime],
        notifier: ApprovalNotifier | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._notifier = notifier
        self._audit = audit

    async def request(
        self,
        approval_id: str,
        action: ProtectedAction,
        plan_hash: str,
        evidence_hash: str,
        requester_id: str,
        expires_at: datetime,
        token: str,
    ) -> ApprovalCapability:
        now = self._clock()
        if action.plan_hash != plan_hash or action.evidence_hash != evidence_hash:
            raise ApprovalError("approval hashes do not match the protected action")
        if expires_at <= now:
            raise ApprovalError("approval expiry must be in the future")
        if len(token) < 43 or not token.replace("-", "").replace("_", "").isalnum():
            raise ApprovalError("approval capability must be a URL-safe 256-bit value")
        approval = Approval(
            id=approval_id,
            organisation_id=action.organisation_id,
            run_id=action.run_id,
            action_id=action.id,
            action_digest=digest(action),
            plan_hash=plan_hash,
            evidence_hash=evidence_hash,
            generation_id=action.generation_id,
            requested_by=requester_id,
            capability_hash=_hash(token),
            expires_at=expires_at,
            created_at=now,
        )
        stored = await self._repository.create(approval, action)
        if self._notifier is not None:
            await self._notifier.emit(
                stored.id,
                stored.organisation_id,
                NotificationKind.APPROVAL_REQUIRED,
                Severity.HIGH,
                "Protected action needs approval",
                f"Kinu run {stored.run_id} is waiting for approval {stored.id}.",
                f"/organisations/{stored.organisation_id}/approvals/{stored.id}",
                stored.id,
                run_id=stored.run_id,
                approval_id=stored.id,
            )
        if self._audit is not None:
            await self._audit.append(
                _audit_id(stored.id, "requested"),
                stored.organisation_id,
                "approval.requested",
                requester_id,
                f"approvals/{stored.id}",
                {
                    "action_id": stored.action_id,
                    "action_digest": stored.action_digest,
                    "expires_at": stored.expires_at.isoformat(),
                },
                run_id=stored.run_id,
            )
        return ApprovalCapability(approval=stored, token=token)

    async def list_approvals(
        self,
        organisation_id: str,
        decisions: frozenset[ApprovalDecision] | None = None,
        limit: int = 100,
    ) -> tuple[Approval, ...]:
        approvals = await self._repository.list_approvals(organisation_id, _LIST_SCAN_LIMIT)
        if decisions is not None:
            approvals = tuple(approval for approval in approvals if approval.decision in decisions)
        ordered = sorted(
            approvals, key=lambda approval: (approval.created_at, approval.id), reverse=True
        )
        return tuple(ordered[:limit])

    async def decide(
        self,
        organisation_id: str,
        approval_id: str,
        expected_revision: int,
        decision: ApprovalDecision,
        actor_id: str,
    ) -> Approval:
        if decision is ApprovalDecision.PENDING:
            raise ApprovalError("a pending decision cannot be submitted")
        decided = await self._repository.decide(
            organisation_id,
            approval_id,
            expected_revision,
            decision,
            actor_id,
            self._clock(),
        )
        if self._audit is not None:
            await self._audit.append(
                _audit_id(decided.id, str(decided.revision)),
                decided.organisation_id,
                f"approval.{decided.decision.value}",
                actor_id,
                f"approvals/{decided.id}",
                {"action_id": decided.action_id, "revision": decided.revision},
                run_id=decided.run_id,
            )
        return decided

    async def consume(
        self,
        organisation_id: str,
        approval_id: str,
        capability: str,
        action: ProtectedAction,
        plan_hash: str,
        evidence_hash: str,
        actor_id: str = "coordinator_one",
    ) -> Approval:
        if action.plan_hash != plan_hash or action.evidence_hash != evidence_hash:
            raise ApprovalError("approval hashes do not match the protected action")
        consumed = await self._repository.consume(
            organisation_id,
            approval_id,
            _hash(capability),
            digest(action),
            plan_hash,
            evidence_hash,
            self._clock(),
        )
        if self._audit is not None:
            await self._audit.append(
                _audit_id(consumed.id, str(consumed.revision)),
                consumed.organisation_id,
                "approval.consumed",
                actor_id,
                f"approvals/{consumed.id}",
                {"action_id": consumed.action_id, "revision": consumed.revision},
                run_id=consumed.run_id,
            )
        return consumed


def verify_capability(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash(token), expected_hash)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _audit_id(*values: str) -> str:
    checksum = hashlib.sha256("\0".join(values).encode()).hexdigest()
    return f"audit_{checksum[:40]}"
