from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from connectors.base.errors import ConnectorAuthenticationError
from hosting.oauth import OAuth

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_railway_consent_uses_pkce_and_only_required_project_scopes() -> None:
    async with httpx.AsyncClient() as http:
        oauth = OAuth(
            http,
            "railway",
            {"client_id": "app", "client_secret": "private"},
            "https://kinu.example",
        )
        url, verifier = oauth.begin("state")
        query = parse_qs(urlsplit(url).query)
        assert query["scope"] == ["openid offline_access project:member"]
        assert query["prompt"] == ["consent"] and query["code_challenge_method"] == ["S256"]
        assert verifier not in url and "private" not in url


async def test_oauth_does_not_forward_client_credentials_across_redirects_or_error_bodies() -> None:
    calls = []

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302, headers={"location": "https://untrusted.example"}, text="private-error-body"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as http:
        oauth = OAuth(
            http,
            "vercel",
            {"client_id": "app", "client_secret": "private", "slug": "kinu"},
            "https://kinu.example",
        )
        url, _ = oauth.begin("state")
        assert urlsplit(url).hostname == "vercel.com"
        with pytest.raises(ConnectorAuthenticationError) as error:
            await oauth.exchange("code", "verifier")
        assert "private" not in str(error.value)
        assert len(calls) == 1


def test_hosting_oauth_apps_load_from_environment_and_secret_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from executor.config import ExecutorSettings
    from pydantic import SecretStr

    secret = tmp_path / "railway-secret"
    secret.write_text("file-private-railway\n")
    for key, value in {
        "KINU_VERCEL_SLUG": "kinu",
        "KINU_VERCEL_CLIENT_ID": "vercel-app",
        "KINU_VERCEL_CLIENT_SECRET": "env-private-vercel",
        "KINU_RAILWAY_CLIENT_ID": "railway-app",
        "KINU_RAILWAY_CLIENT_SECRET": "ignored-env-private-railway",
        "KINU_RAILWAY_CLIENT_SECRET_FILE": str(secret),
    }.items():
        monkeypatch.setenv(key, value)
    config = ExecutorSettings(
        vault_key=SecretStr("vault-test-material-at-least-thirty-two"),
        executor_token=SecretStr("executor-private"),
        native_token=SecretStr("native-private"),
    )
    assert config.oauth_apps == {
        "vercel": {
            "slug": "kinu",
            "client_id": "vercel-app",
            "client_secret": "env-private-vercel",
        },
        "railway": {"client_id": "railway-app", "client_secret": "file-private-railway"},
    }
    assert "env-private-vercel" not in repr(config)
    assert "file-private-railway" not in repr(config)
    assert "ignored-env-private-railway" not in repr(config)


@pytest.mark.parametrize("provider", ["vercel", "railway"])
def test_partial_hosting_configuration_fails_without_logging_credentials(provider: str) -> None:
    from executor.config import ExecutorSettings
    from pydantic import SecretStr, ValidationError

    with pytest.raises(ValidationError) as error:
        ExecutorSettings(
            vault_key=SecretStr("vault-test-material-at-least-thirty-two"),
            executor_token=SecretStr("executor-private"),
            native_token=SecretStr("native-private"),
            vercel_client_secret=SecretStr("private-value-never-in-errors")
            if provider == "vercel"
            else None,
            railway_client_secret=SecretStr("private-value-never-in-errors")
            if provider == "railway"
            else None,
        )
    assert f"KINU_{provider.upper()}" in str(error.value)
    assert "private-value-never-in-errors" not in str(error.value)
