from contracts import (
    GitHubInstallation,
    GitHubOnboardingSession,
    GitHubRepository,
    GitHubRepositoryCandidate,
)

from core.private import PrivateClient


class GitHubBridge:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

    async def begin(
        self, organisation_id: str, subject: str
    ) -> tuple[GitHubOnboardingSession, str, str, str, str]:
        value = await self.client.call(
            "/incident-sources",
            {"operation": "github-begin", "organisation_id": organisation_id, "subject": subject},
        )
        return (
            GitHubOnboardingSession.model_validate(value["session"]),
            value["state"],
            value["pkce_verifier"],
            value["installation_url"],
            value["authorization_url"],
        )

    async def discover(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
        state: str,
        verifier: str,
        code: str,
        installation_id: int | None,
    ) -> tuple[GitHubOnboardingSession, GitHubInstallation, tuple[GitHubRepositoryCandidate, ...]]:
        value = await self.client.call(
            "/incident-sources",
            {
                "operation": "github-discover",
                "organisation_id": organisation_id,
                "session_id": session_id,
                "subject": subject,
                "state": state,
                "verifier": verifier,
                "code": code,
                "installation_id": installation_id,
            },
            timeout=60,
        )
        return (
            GitHubOnboardingSession.model_validate(value["session"]),
            GitHubInstallation.model_validate(value["installation"]),
            tuple(GitHubRepositoryCandidate.model_validate(item) for item in value["repositories"]),
        )

    async def complete(
        self, organisation_id: str, session_id: str, subject: str
    ) -> tuple[GitHubOnboardingSession, GitHubInstallation, tuple[GitHubRepository, ...]]:
        value = await self.client.call(
            "/incident-sources",
            {
                "operation": "github-complete",
                "organisation_id": organisation_id,
                "session_id": session_id,
                "subject": subject,
            },
        )
        return (
            GitHubOnboardingSession.model_validate(value["session"]),
            GitHubInstallation.model_validate(value["installation"]),
            tuple(GitHubRepository.model_validate(item) for item in value["repositories"]),
        )
