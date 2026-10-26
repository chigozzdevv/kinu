import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from connectors.base.errors import ConnectorSetupRequiredError
from connectors.github import GitHubWebhook
from contracts import (
    GitHubInstallation,
    GitHubOnboardingSession,
    GitHubOnboardingStatus,
    GitHubRepository,
    GitHubRepositoryCandidate,
    GitHubWebhookReceipt,
)
from core.errors import ResourceConflictError, ResourceSetupRequiredError
from core.github import GitHubOnboardingService

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_onboarding_verifies_repositories_and_signed_delivery() -> None:
    repository = Repository(
        GitHubWebhookReceipt(
            installation_id=123,
            delivery_id="delivery-one",
            event="installation",
            action="created",
            received_at=NOW,
        )
    )
    service = GitHubOnboardingService(
        repository,
        cast(Any, Connector()),
        "kinu-app",
        "client-one",
        "https://app.kinu.example/github/callback",
        lambda: NOW,
    )
    session, state, verifier, installation_url, authorization_url = await service.begin(
        "org_one", "user-one"
    )
    assert "state=" in installation_url
    assert "code_challenge_method=S256" in authorization_url
    assert state not in session.model_dump_json()
    assert verifier not in session.model_dump_json()
    with pytest.raises(ResourceConflictError, match="state is invalid"):
        await service.discover(
            "org_one", session.id, "user-one", "x" * len(state), verifier, "oauth-code", 123
        )
    staged, installation, candidates = await service.discover(
        "org_one", session.id, "user-one", state, verifier, "oauth-code", 123
    )
    assert staged.status is GitHubOnboardingStatus.DISCOVERED
    assert candidates[0].full_name == "customer/api"
    completed, installation, repositories = await service.complete(
        "org_one", session.id, "user-one"
    )
    assert completed.status is GitHubOnboardingStatus.COMPLETE
    assert installation.ready
    assert installation.webhook_verified_at == NOW
    assert repositories[0].full_name == "customer/api"


@pytest.mark.anyio
async def test_onboarding_maps_a_missing_installation_to_setup_required() -> None:
    repository = Repository(None)
    service = GitHubOnboardingService(
        repository,
        cast(Any, MissingInstallationConnector()),
        "kinu-app",
        "client-one",
        "https://app.kinu.example/github/callback",
        lambda: NOW,
    )
    session, state, verifier, _, _ = await service.begin("org_one", "user-one")
    with pytest.raises(ResourceSetupRequiredError, match="not installed"):
        await service.discover(
            "org_one", session.id, "user-one", state, verifier, "oauth-code", None
        )


def test_secret_scanning_event_uses_the_confirmed_source_connection() -> None:
    body = json.dumps(
        {
            "action": "created",
            "installation": {"id": 123},
            "repository": {"id": 456, "full_name": "customer/api"},
            "alert": {
                "number": 7,
                "html_url": "https://github.com/customer/api/security/secret-scanning/7",
                "secret_type": "customer_platform_token",
                "secret": "must-never-enter-kinu-metadata",
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            },
        }
    ).encode()
    event = GitHubWebhook().normalise(
        "org_one", "secret_scanning_alert", body, NOW, "connection_github"
    )
    assert event.resource.connection_id == "connection_github"
    assert event.resource.credential_id is None
    assert event.resource.repository == "customer/api"
    assert event.resource.provider is None
    assert "must-never-enter-kinu-metadata" not in event.model_dump_json()


def test_public_leak_event_is_an_exposure_trigger() -> None:
    body = json.dumps(
        {
            "action": "publicly_leaked",
            "repository": {"full_name": "customer/api"},
            "alert": {
                "number": 8,
                "html_url": "https://github.com/customer/api/security/secret-scanning/8",
                "secret_type": "another_customer_secret",
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            },
        }
    ).encode()
    event = GitHubWebhook().normalise(
        "org_one", "secret_scanning_alert", body, NOW, "connection_github"
    )
    assert event.kind == "credential-exposure-detected"
    assert event.severity.value == "critical"


class Connector:
    async def verify(
        self, code: str, verifier: str, installation_id: int | None
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        assert code == "oauth-code"
        assert verifier
        assert installation_id == 123
        return (
            {
                "installation_id": 123,
                "account_id": 44,
                "account_login": "customer",
                "account_type": "Organization",
                "repository_selection": "selected",
                "permissions": {"secret_scanning_alerts": "read"},
                "events": ["secret_scanning_alert"],
            },
            (
                {
                    "repository_id": 456,
                    "full_name": "customer/api",
                    "private": True,
                    "default_branch": "main",
                    "secret_scanning": "enabled",
                },
            ),
        )


class MissingInstallationConnector:
    async def verify(
        self, code: str, verifier: str, installation_id: int | None
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        raise ConnectorSetupRequiredError(
            "Kinu Security is not installed on an accessible GitHub account"
        )


class Repository:
    def __init__(self, receipt: GitHubWebhookReceipt | None) -> None:
        self.receipt_value = receipt
        self.session: GitHubOnboardingSession | None = None
        self.installation_value: GitHubInstallation | None = None
        self.repository_values: tuple[GitHubRepository, ...] = ()

    async def create_session(self, session: GitHubOnboardingSession) -> GitHubOnboardingSession:
        self.session = session
        return session

    async def get_session(self, organisation_id: str, session_id: str) -> GitHubOnboardingSession:
        assert self.session is not None
        return self.session

    async def receipt(self, installation_id: int) -> GitHubWebhookReceipt | None:
        return self.receipt_value

    async def stage(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepositoryCandidate, ...],
    ) -> GitHubOnboardingSession:
        staged = session.model_copy(
            update={
                "status": GitHubOnboardingStatus.DISCOVERED,
                "installation_id": installation.installation_id,
                "installation": installation,
                "repositories": repositories,
            }
        )
        self.session = staged
        return staged

    async def complete(
        self,
        session: GitHubOnboardingSession,
        installation: GitHubInstallation,
        repositories: tuple[GitHubRepository, ...],
    ) -> GitHubOnboardingSession:
        completed = session.model_copy(
            update={
                "status": GitHubOnboardingStatus.COMPLETE,
                "installation_id": installation.installation_id,
                "completed_at": installation.updated_at,
            }
        )
        self.session = completed
        self.installation_value = installation
        self.repository_values = repositories
        return completed

    async def installation(self, organisation_id: str, installation_id: int) -> GitHubInstallation:
        assert self.installation_value is not None
        return self.installation_value

    async def repositories(
        self, organisation_id: str, installation_id: int
    ) -> tuple[GitHubRepository, ...]:
        return self.repository_values
