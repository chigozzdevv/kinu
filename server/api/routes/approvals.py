from typing import Annotated

from contracts import (
    Approval,
    ApprovalDecision,
    ApprovalEvidenceSnapshot,
    Contract,
    Identifier,
    ProtectedAction,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Query, Request, status
from pydantic import AwareDatetime, Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/approvals",
    tags=["approvals"],
)


class ApprovalRequest(Contract):
    id: Identifier
    action: ProtectedAction
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: AwareDatetime
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43,256}$")


class ApprovalCapabilityResponse(Contract):
    approval: Approval
    token: str = Field(min_length=32)


class DecisionRequest(Contract):
    expected_revision: int = Field(ge=0)
    decision: ApprovalDecision


class ConsumeRequest(Contract):
    token: str = Field(min_length=32)
    action: ProtectedAction
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("", response_model=tuple[Approval, ...])
async def list_approvals(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    decision: Annotated[list[ApprovalDecision] | None, Query()] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[Approval, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.APPROVAL_READ)
    decisions = frozenset(decision) if decision is not None else None
    return await required(api.approvals, "approvals").list_approvals(
        organisation_id, decisions, limit
    )


@router.post("", response_model=ApprovalCapabilityResponse, status_code=status.HTTP_201_CREATED)
async def request_approval(
    organisation_id: Identifier,
    body: ApprovalRequest,
    identity: Identity,
    request: Request,
) -> ApprovalCapabilityResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    if body.action.organisation_id != organisation_id:
        raise ResourceConflictError("protected action crosses organisation boundary")
    value = await required(api.approvals, "approvals").request(
        body.id,
        body.action,
        body.plan_hash,
        body.evidence_hash,
        identity.actor_id,
        body.expires_at,
        body.token,
    )
    return ApprovalCapabilityResponse(approval=value.approval, token=value.token)


@router.post("/{approval_id}/decision", response_model=Approval)
async def decide(
    organisation_id: Identifier,
    approval_id: Identifier,
    body: DecisionRequest,
    identity: Identity,
    request: Request,
) -> Approval:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.APPROVAL_DECIDE)
    return await required(api.approvals, "approvals").decide(
        organisation_id,
        approval_id,
        body.expected_revision,
        body.decision,
        identity.actor_id,
    )


@router.get("/{approval_id}/evidence", response_model=ApprovalEvidenceSnapshot)
async def evidence(
    organisation_id: Identifier,
    approval_id: Identifier,
    identity: Identity,
    request: Request,
) -> ApprovalEvidenceSnapshot:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.APPROVAL_READ)
    if api.history is None:
        raise ResourceConflictError("approval evidence is unavailable")
    return await api.history.approval_evidence(organisation_id, approval_id)


@router.post("/{approval_id}/consume", response_model=Approval)
async def consume(
    organisation_id: Identifier,
    approval_id: Identifier,
    body: ConsumeRequest,
    identity: Identity,
    request: Request,
) -> Approval:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    if body.action.organisation_id != organisation_id:
        raise ResourceConflictError("protected action crosses organisation boundary")
    return await required(api.approvals, "approvals").consume(
        organisation_id,
        approval_id,
        body.token,
        body.action,
        body.plan_hash,
        body.evidence_hash,
        identity.actor_id,
    )
