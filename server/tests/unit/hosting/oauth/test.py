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
