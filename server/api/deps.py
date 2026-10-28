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
from core.account.installation import Installation
from core.approval import ApprovalService
from core.audit import AuditWriter
from core.auth import AccessControl, AuthenticatedIdentity
from core.auth.browser import BrowserAuth
from core.auth.session import BrowserSession
from core.config import Settings
from core.connection.service import ConnectionService
from core.errors import (
    AuthenticationError,
    IntegrationNotConfiguredError,
    ResourceSetupRequiredError,
)
from core.github import GitHubOnboardingService
from core.history import RunHistoryService
from core.incident import IncidentService
from core.inventory import InventoryService
from core.notification import NotificationService
from core.overview import OverviewService
from core.playbook import PlaybookService, WalkthroughService
from core.private import PrivateClient
from core.verification import ProbeService
from core.workflow import RunWorkflow
from core.workflow.dispatch import RunDispatcher
from demo.mail import Source
from fastapi import Depends, Header, Request
from mail.bridge import Bridge as GmailBridge


@dataclass(frozen=True, slots=True)
class ApiServices:
    access: AccessControl
    workflow: RunWorkflow | None = None
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
    connections: ConnectionService | None = None
    dispatcher: RunDispatcher | None = None
    demo: Source | None = None
    gmail: GmailBridge | None = None
    private: PrivateClient | None = None
    installation: Installation | None = None


def services(request: Request) -> ApiServices:
    configured = request.app.state.services
    if configured is None:
        raise IntegrationNotConfiguredError("Kinu backend integrations are not configured")
    return cast(ApiServices, configured)


async def authenticated_identity(
    request: Request,
) -> AuthenticatedIdentity:
    session = await browser_session(request)
    return session.identity


async def browser_session(request: Request) -> BrowserSession:
    config = cast(Settings, request.app.state.config)
    name = "__Host-kinu" if config.public_url.startswith("https://") else "kinu.session"
    token = session_cookie(request, name)
    if not token:
        raise AuthenticationError("session cookie is required")
    browser = required(
        cast(BrowserAuth | None, request.app.state.browser), "browser authentication"
    )
    session = await browser.store.get(token)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        browser.require_csrf(
            session, request.headers.get("origin"), request.headers.get("x-csrf-token")
        )
    return session


def session_binding(request: Request) -> str:
    config = cast(Settings, request.app.state.config)
    name = "__Host-kinu" if config.public_url.startswith("https://") else "kinu.session"
    return hashlib.sha256((session_cookie(request, name) or "").encode()).hexdigest()


def session_cookie(request: Request, name: str) -> str | None:
    values = [
        value.strip()
        for header in request.headers.getlist("cookie")
        for part in header.split(";")
        for key, separator, value in [part.strip().partition("=")]
        if separator and key == name
    ]
    if len(values) > 1:
        raise AuthenticationError("duplicate session cookies are not accepted")
    return values[0] if values else None


def idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=256)],
) -> str:
    return value


def command_id(identity: AuthenticatedIdentity, organisation_id: str, key: str) -> str:
    payload = f"{identity.subject}\x00{organisation_id}\x00{key}".encode()
    return f"cmd_{hashlib.sha256(payload).hexdigest()[:40]}"


def required[T](value: T | None, name: str) -> T:
    if value is None:
        unavailable = {
            "gmail": "Gmail setup is not complete.",
            "browsers": "Browser execution is not enabled for this deployment.",
            "browser setup": "Browser sign-in is not enabled for this deployment.",
            "connections": "AWS account onboarding is not enabled for this deployment.",
            "github": "GitHub connections are not enabled for this deployment.",
            "agents": "Playbook generation is not enabled for this deployment.",
            "walkthroughs": "Playbook source processing is not enabled for this deployment.",
        }
        if name in unavailable:
            raise ResourceSetupRequiredError(unavailable[name])
        raise IntegrationNotConfiguredError(f"API service {name} is not configured")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


Identity = Annotated[AuthenticatedIdentity, Depends(authenticated_identity)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
