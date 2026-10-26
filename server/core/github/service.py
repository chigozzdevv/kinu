import base64
import hashlib
import hmac
from collections.abc import Callable
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Protocol
from urllib.parse import urlencode

from connectors.base.errors import ConnectorAuthenticationError, ConnectorSetupRequiredError
from connectors.github import GitHubOnboardingConnector
from contracts import (
    GitHubInstallation,
    GitHubOnboardingSession,
    GitHubOnboardingStatus,
    GitHubRepository,
    GitHubRepositoryCandidate,
    GitHubSecretScanningStatus,
    GitHubWebhookReceipt,
)

from core.errors import ResourceConflictError, ResourceSetupRequiredError
from core.ids import new_id


class GitHubRepositoryStore(Protocol):
    async def create_session(self, session: GitHubOnboardingSession) -> GitHubOnboardingSession: ...

    async def get_session(
        self, organisation_id: str, session_id: str
    ) -> GitHubOnboardingSession: ...

    async def receipt(self, installation_id: int) -> GitHubWebhookReceipt | None: ...

    async def stage(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepositoryCandidate, ...],
    ) -> GitHubOnboardingSession: ...

    async def complete(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepository, ...],
    ) -> GitHubOnboardingSession: ...

    async def installation(
        self, organisation_id: str, installation_id: int
    ) -> GitHubInstallation: ...

    async def repositories(
        self, organisation_id: str, installation_id: int
    ) -> tuple[GitHubRepository, ...]: ...


class GitHubOnboardingService:
    def __init__(
        self,
        repository: GitHubRepositoryStore,
        connector: GitHubOnboardingConnector,
        app_slug: str,
        client_id: str,
        redirect_uri: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._connector = connector
        self._app_slug = app_slug
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._clock = clock

    async def begin(
        self, organisation_id: str, subject: str
    ) -> tuple[GitHubOnboardingSession, str, str, str, str]:
        now = self._clock()
        state = token_urlsafe(32)
        verifier = token_urlsafe(64)
        challenge = _challenge(verifier)
        session = GitHubOnboardingSession(
            id=new_id("github"),
            organisation_id=organisation_id,
            subject=subject,
            state_hash=_hash(state),
            verifier_hash=_hash(verifier),
            status=GitHubOnboardingStatus.PENDING,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        stored = await self._repository.create_session(session)
        install_url = f"https://github.com/apps/{self._app_slug}/installations/new?" + urlencode(
            {"state": state}
        )
        authorization_url = "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return stored, state, verifier, install_url, authorization_url

    async def discover(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
        state: str,
        verifier: str,
        code: str,
        installation_id: int | None,
    ) -> tuple[
        GitHubOnboardingSession,
        GitHubInstallation,
        tuple[GitHubRepositoryCandidate, ...],
    ]:
        session = await self._repository.get_session(organisation_id, session_id)
        _authorise(session, subject, state, verifier, self._clock())
        if session.status is GitHubOnboardingStatus.COMPLETE:
            assert session.installation_id is not None
            return (
                session,
                await self._repository.installation(organisation_id, session.installation_id),
                tuple(
                    GitHubRepositoryCandidate(
                        repository_id=item.repository_id,
                        full_name=item.full_name,
                        private=item.private,
                        default_branch=item.default_branch,
                        secret_scanning=item.secret_scanning,
                    )
                    for item in await self._repository.repositories(
                        organisation_id, session.installation_id
                    )
                ),
            )
        if session.status is GitHubOnboardingStatus.DISCOVERED:
            assert session.installation is not None
            return session, session.installation, session.repositories
        try:
            metadata, repository_metadata = await self._connector.verify(
                code, verifier, installation_id
            )
        except ConnectorSetupRequiredError as error:
            raise ResourceSetupRequiredError(str(error)) from None
        except ConnectorAuthenticationError as error:
            raise ResourceConflictError(str(error)) from None
        resolved_installation_id = metadata["installation_id"]
        permissions = metadata["permissions"]
        events = metadata["events"]
        if permissions.get("secret_scanning_alerts") not in {"read", "write"}:
            raise ResourceConflictError("GitHub App requires read access to secret scanning alerts")
        if "secret_scanning_alert" not in events:
            raise ResourceConflictError(
                "GitHub App is not subscribed to secret scanning alert events"
            )
        receipt = await self._repository.receipt(resolved_installation_id)
        now = self._clock()
        repositories = tuple(
            GitHubRepositoryCandidate(
                repository_id=item["repository_id"],
                full_name=item["full_name"],
                private=item["private"],
                default_branch=item["default_branch"],
                secret_scanning=GitHubSecretScanningStatus(item["secret_scanning"]),
            )
            for item in repository_metadata
        )
        if not repositories:
            raise ResourceConflictError("GitHub App must be installed on at least one repository")
        repositories_ready = bool(repositories) and all(
            item.secret_scanning is GitHubSecretScanningStatus.ENABLED for item in repositories
        )
        installation = GitHubInstallation(
            installation_id=resolved_installation_id,
            organisation_id=organisation_id,
            account_id=metadata["account_id"],
            account_login=metadata["account_login"],
            account_type=metadata["account_type"],
            repository_selection=metadata["repository_selection"],
            permissions=permissions,
            events=tuple(events),
            webhook_verified_at=receipt.received_at if receipt is not None else None,
            repositories_ready=repositories_ready,
            ready=repositories_ready and receipt is not None,
            created_at=now,
            updated_at=now,
        )
        staged = await self._repository.stage(session, installation, repositories)
        return staged, installation, repositories

    async def complete(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
    ) -> tuple[
        GitHubOnboardingSession,
        GitHubInstallation,
        tuple[GitHubRepository, ...],
    ]:
        session = await self._repository.get_session(organisation_id, session_id)
        _owner(session, subject, self._clock())
        if session.status is GitHubOnboardingStatus.COMPLETE:
            assert session.installation_id is not None
            return (
                session,
                await self._repository.installation(organisation_id, session.installation_id),
                await self._repository.repositories(organisation_id, session.installation_id),
            )
        if session.status is not GitHubOnboardingStatus.DISCOVERED:
            raise ResourceConflictError("GitHub repositories have not been discovered")
        installation = session.installation
        if installation is None:
            raise ResourceConflictError("GitHub installation metadata is unavailable")
        now = self._clock()
        repositories = tuple(
            GitHubRepository(
                repository_id=item.repository_id,
                installation_id=installation.installation_id,
                organisation_id=organisation_id,
                full_name=item.full_name,
                private=item.private,
                default_branch=item.default_branch,
                secret_scanning=item.secret_scanning,
                updated_at=now,
            )
            for item in session.repositories
        )
        completed = await self._repository.complete(session, installation, repositories)
        return completed, installation, repositories


def _authorise(
    session: GitHubOnboardingSession,
    subject: str,
    state: str,
    verifier: str,
    now: datetime,
) -> None:
    _owner(session, subject, now)
    if not hmac.compare_digest(_hash(state), session.state_hash):
        raise ResourceConflictError("GitHub onboarding state is invalid")
    if not hmac.compare_digest(_hash(verifier), session.verifier_hash):
        raise ResourceConflictError("GitHub onboarding PKCE verifier is invalid")


def _owner(session: GitHubOnboardingSession, subject: str, now: datetime) -> None:
    if session.subject != subject:
        raise ResourceConflictError("GitHub onboarding belongs to another administrator")
    if session.expires_at <= now and session.status is not GitHubOnboardingStatus.COMPLETE:
        raise ResourceConflictError("GitHub onboarding session has expired")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
