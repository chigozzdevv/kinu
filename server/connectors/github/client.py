import asyncio
from typing import Any, Protocol

import httpx

from connectors.base import SecretValue
from connectors.base.errors import (
    ConnectorAuthenticationError,
    ConnectorError,
    ConnectorSetupRequiredError,
)


class GitHubSecrets(Protocol):
    async def access(self, version: str) -> SecretValue: ...


class GitHubOnboardingConnector:
    def __init__(
        self,
        app_slug: str,
        client_id: str,
        client_secret_reference: str,
        redirect_uri: str,
        secrets: GitHubSecrets,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_slug = app_slug
        self._client_id = client_id
        self._client_secret_reference = client_secret_reference
        self._redirect_uri = redirect_uri
        self._secrets = secrets
        self._client = client or httpx.AsyncClient(timeout=30)

    async def verify(
        self,
        code: str,
        verifier: str,
        installation_id: int | None,
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        token = await self._exchange(code, verifier)
        try:
            installation = await self._accessible_installation(token, installation_id)
            installation_id = installation["installation_id"]
            repositories = await self._repositories(token, installation_id)
            semaphore = asyncio.Semaphore(10)

            async def inspect(repository: dict[str, Any]) -> dict[str, Any]:
                async with semaphore:
                    status = await self._secret_scanning(token, repository)
                return {**repository, "secret_scanning": status}

            checked = await asyncio.gather(*(inspect(value) for value in repositories))
            return installation, tuple(checked)
        finally:
            token.clear()

    async def _exchange(self, code: str, verifier: str) -> SecretValue:
        client_secret = await self._secrets.access(self._client_secret_reference)
        try:
            secret = client_secret.bytes().decode().strip()
            try:
                response = await self._client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": self._client_id,
                        "client_secret": secret,
                        "code": code,
                        "redirect_uri": self._redirect_uri,
                        "code_verifier": verifier,
                    },
                )
            except httpx.HTTPError:
                raise ConnectorError(
                    "github-oauth-unavailable",
                    "GitHub OAuth exchange was unavailable",
                    retryable=True,
                ) from None
        finally:
            client_secret.clear()
        if response.status_code != 200:
            raise ConnectorAuthenticationError("GitHub OAuth exchange was rejected")
        value = response.json()
        token = value.get("access_token") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            reason = value.get("error") if isinstance(value, dict) else None
            messages = {
                "bad_verification_code": "GitHub authorization expired or was already used",
                "incorrect_client_credentials": "GitHub OAuth client credentials were rejected",
                "redirect_uri_mismatch": (
                    "GitHub OAuth callback URL does not match the app configuration"
                ),
            }
            raise ConnectorAuthenticationError(
                messages.get(reason, "GitHub OAuth returned no user access token")
                if isinstance(reason, str)
                else "GitHub OAuth returned no user access token"
            )
        return SecretValue(token.encode())

    async def _accessible_installation(
        self,
        token: SecretValue,
        installation_id: int | None,
    ) -> dict[str, Any]:
        matches: list[dict[str, Any]] = []
        for page in range(1, 5):
            response = await self._request(
                token,
                "/user/installations",
                params={"per_page": "100", "page": str(page)},
            )
            installations = response.get("installations")
            if not isinstance(installations, list) or not all(
                isinstance(item, dict) for item in installations
            ):
                raise ConnectorError(
                    "github-installations-invalid", "GitHub returned invalid installations"
                )
            matches.extend(
                item
                for item in installations
                if item.get("app_slug") == self._app_slug
                and isinstance(item.get("id"), int)
                and item.get("suspended_at") is None
                and (installation_id is None or item["id"] == installation_id)
            )
            if len(installations) < 100:
                break
        if not matches:
            if installation_id is not None:
                raise ConnectorAuthenticationError(
                    "GitHub user cannot access the requested App installation"
                )
            raise ConnectorSetupRequiredError(
                "Kinu Security is not installed on an accessible GitHub account"
            )
        if installation_id is None and len(matches) != 1:
            raise ConnectorAuthenticationError(
                "Multiple Kinu Security installations are accessible; use GitHub to select one"
            )
        response = matches[0]
        resolved_id = response.get("id")
        account = response.get("account")
        permissions = response.get("permissions", {})
        events = response.get("events", [])
        if (
            not isinstance(resolved_id, int)
            or not isinstance(account, dict)
            or not isinstance(permissions, dict)
            or not isinstance(events, list)
        ):
            raise ConnectorError(
                "github-installation-invalid", "GitHub returned invalid installation metadata"
            )
        account_id = account.get("id")
        login = account.get("login")
        account_type = account.get("type")
        selection = response.get("repository_selection")
        if (
            not isinstance(account_id, int)
            or not isinstance(login, str)
            or not isinstance(account_type, str)
            or selection not in {"all", "selected"}
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in permissions.items()
            )
            or not all(isinstance(event, str) for event in events)
        ):
            raise ConnectorError(
                "github-installation-invalid", "GitHub installation metadata is incomplete"
            )
        return {
            "installation_id": resolved_id,
            "account_id": account_id,
            "account_login": login,
            "account_type": account_type,
            "repository_selection": selection,
            "permissions": permissions,
            "events": events,
        }

    async def _repositories(
        self, token: SecretValue, installation_id: int
    ) -> tuple[dict[str, Any], ...]:
        values: list[dict[str, Any]] = []
        for page in range(1, 5):
            response = await self._request(
                token,
                f"/user/installations/{installation_id}/repositories",
                params={"per_page": "100", "page": str(page)},
            )
            repositories = response.get("repositories")
            if not isinstance(repositories, list) or not all(
                isinstance(item, dict) for item in repositories
            ):
                raise ConnectorError(
                    "github-repositories-invalid", "GitHub returned invalid repositories"
                )
            total = response.get("total_count")
            if isinstance(total, int) and total > 400:
                raise ConnectorError(
                    "github-repositories-limit",
                    "select at most 400 repositories for one Kinu installation",
                )
            values.extend(_repository(item) for item in repositories)
            if len(repositories) < 100 or (isinstance(total, int) and len(values) >= total):
                return tuple(values)
        raise ConnectorError(
            "github-repositories-limit",
            "select at most 400 repositories for one Kinu installation",
        )

    async def _secret_scanning(self, token: SecretValue, repository: dict[str, Any]) -> str:
        declared = repository.get("secret_scanning")
        if isinstance(declared, str) and declared in {"enabled", "disabled"}:
            return declared
        try:
            response = await self._client.get(
                f"https://api.github.com/repos/{repository['full_name']}/secret-scanning/alerts",
                headers=_headers(token),
                params={"per_page": "1", "hide_secret": "true"},
            )
        except httpx.HTTPError:
            raise ConnectorError(
                "github-secret-scanning-check",
                "GitHub secret scanning check was unavailable",
                retryable=True,
            ) from None
        if response.status_code == 200:
            return "enabled"
        if response.status_code == 404:
            return "disabled"
        if response.status_code in {403, 451}:
            return "unavailable"
        raise ConnectorError(
            "github-secret-scanning-check",
            f"GitHub secret scanning check returned HTTP {response.status_code}",
            retryable=response.status_code in {408, 429, 500, 502, 503, 504},
        )

    async def _request(
        self,
        token: SecretValue,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(
                f"https://api.github.com{path}", headers=_headers(token), params=params
            )
        except httpx.HTTPError:
            raise ConnectorError(
                "github-api-unavailable", "GitHub API was unavailable", retryable=True
            ) from None
        if response.status_code in {401, 403, 404}:
            raise ConnectorAuthenticationError(
                "GitHub user cannot access the requested App installation"
            )
        if response.status_code != 200:
            raise ConnectorError(
                "github-api-error",
                f"GitHub API returned HTTP {response.status_code}",
                retryable=response.status_code in {408, 429, 500, 502, 503, 504},
            )
        value = response.json()
        if not isinstance(value, dict):
            raise ConnectorError("github-api-invalid", "GitHub returned a non-object response")
        return value


def _repository(value: dict[str, Any]) -> dict[str, Any]:
    repository_id = value.get("id")
    full_name = value.get("full_name")
    private = value.get("private")
    default_branch = value.get("default_branch")
    if (
        not isinstance(repository_id, int)
        or not isinstance(full_name, str)
        or not isinstance(private, bool)
        or not isinstance(default_branch, str)
    ):
        raise ConnectorError(
            "github-repository-invalid", "GitHub repository metadata is incomplete"
        )
    analysis = value.get("security_and_analysis")
    scanning = analysis.get("secret_scanning") if isinstance(analysis, dict) else None
    status = scanning.get("status") if isinstance(scanning, dict) else None
    return {
        "repository_id": repository_id,
        "full_name": full_name,
        "private": private,
        "default_branch": default_branch,
        "secret_scanning": status,
    }


def _headers(token: SecretValue) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token.bytes().decode()}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
