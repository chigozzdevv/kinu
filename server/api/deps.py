import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast

from agents.continuity import AgentContinuityService
from agents.runtime import AgentRuntimeService
from agents.storage import AgentRepository
from browser.access import BrowserAccessService
from browser.setup import BrowserSetupApi
from core.account import AccountService
from core.approval import ApprovalService
from core.audit import AuditWriter
from core.auth import AccessControl, AuthenticatedIdentity, IdentityTokenVerifier
from core.errors import AuthenticationError, IntegrationNotConfiguredError
from core.github import GitHubOnboardingService
from core.history import RunHistoryService
from core.incident import IncidentService
from core.inventory import InventoryService
from core.notification import NotificationService
from core.overview import OverviewService
from core.playbook import PlaybookService, WalkthroughService
from core.verification import ProbeService
from core.workflow import RunWorkflow
from fastapi import Depends, Header, Request


@dataclass(frozen=True, slots=True)
class ApiServices:
    workflow: RunWorkflow
    access: AccessControl
    tokens: IdentityTokenVerifier
    inventory: InventoryService | None = None
    playbooks: PlaybookService | None = None
    approvals: ApprovalService | None = None
    incidents: IncidentService | None = None
    browsers: BrowserAccessService | None = None
    agents: AgentRuntimeService | None = None
    agent_repository: AgentRepository | None = None
    agent_continuity: AgentContinuityService | None = None
    walkthroughs: WalkthroughService | None = None
    probes: ProbeService | None = None
    notifications: NotificationService | None = None
    audit: AuditWriter | None = None
    overview: OverviewService | None = None
    browser_setup: BrowserSetupApi | None = None
    github: GitHubOnboardingService | None = None
    history: RunHistoryService | None = None
    accounts: AccountService | None = None


def services(request: Request) -> ApiServices:
    configured = request.app.state.services
    if configured is None:
        raise IntegrationNotConfiguredError("Kinu backend integrations are not configured")
    return cast(ApiServices, configured)


async def authenticated_identity(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> AuthenticatedIdentity:
    if authorization is None:
        raise AuthenticationError("bearer identity token is required")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or (not token):
        raise AuthenticationError("authorization must use a bearer token")
    return await services(request).tokens.verify(token)


def idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=256)],
) -> str:
    return value


def command_id(identity: AuthenticatedIdentity, organisation_id: str, key: str) -> str:
    payload = f"{identity.subject}\x00{organisation_id}\x00{key}".encode()
    return f"cmd_{hashlib.sha256(payload).hexdigest()[:40]}"


def required[T](value: T | None, name: str) -> T:
    if value is None:
        raise IntegrationNotConfiguredError(f"API service {name} is not configured")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


Identity = Annotated[AuthenticatedIdentity, Depends(authenticated_identity)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
