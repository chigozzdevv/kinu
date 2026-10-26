import base64
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from api.app import create_app
from core.auth.browser import BrowserAuth
from core.auth.cognito import CognitoConfig, CognitoTokenVerifier
from core.auth.options import BrowserOptions
from core.config import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from tests.browser import MemorySessions

ORIGIN = "https://kinu.example"
CONFIG = CognitoConfig("us-east-1_example", "kinuclient")
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
async def login(
    private_key: rsa.RSAPrivateKey,
) -> AsyncIterator[tuple[httpx.AsyncClient, MemorySessions, dict[str, str]]]:
    store = MemorySessions()
    provider = {"nonce": "", "verifier": "", "failure": "", "calls": "0"}
    key = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    key.update(kid="one", alg="RS256", use="sig")

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("jwks.json"):
            assert request.url.host == "cognito-idp.us-east-1.amazonaws.com"
            return httpx.Response(200, json={"keys": [key]})
        assert str(request.url) == "https://login.example.com/oauth2/token"
        provider["calls"] = str(int(provider["calls"]) + 1)
        data = parse_qs(request.content.decode())
        assert data["code_verifier"] == [provider["verifier"]]
        assert data["grant_type"] == ["authorization_code"]
        assert data["redirect_uri"] == [f"{ORIGIN}/auth/callback"]
        now = int(datetime.now(UTC).timestamp())
        signed = jwt.encode(
            {
                "iss": CONFIG.issuer,
                "aud": CONFIG.client_id,
                "sub": "user-one",
                "iat": now,
                "exp": now + 3600,
                "token_use": "id",
                "email": "owner@example.com",
                "email_verified": True,
                "nonce": "wrong" if provider["failure"] == "nonce" else provider["nonce"],
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "one"},
        )
        return httpx.Response(
            200,
            json={
                "id_token": signed,
                "access_token": "private-access",
                "refresh_token": "private-refresh",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider_http:
        browser = BrowserAuth(
            BrowserOptions(CONFIG, "https://login.example.com", ORIGIN),
            store,
            CognitoTokenVerifier(CONFIG, provider_http),
            provider_http,
            lambda: datetime.now(UTC),
        )
        app = create_app(browser=browser, settings=Settings(public_url=ORIGIN))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as http:
            yield http, store, provider


async def begin(
    http: httpx.AsyncClient, store: MemorySessions, provider: dict[str, str]
) -> dict[str, str]:
    response = await http.get("/auth/login", params={"return_to": "/dashboard/connections"})
    assert response.status_code == 303
    query = {
        key: values[0]
        for key, values in parse_qs(urlsplit(response.headers["location"]).query).items()
    }
    provider["nonce"] = query["nonce"]
    attempt = next(iter(store.attempts.values()))
    provider["verifier"] = attempt.verifier
    challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest())
    assert query["code_challenge"] == challenge.decode().rstrip("=")
    assert query["code_challenge_method"] == "S256"
    assert query["response_type"] == "code"
    return query


async def test_login_exchanges_code_for_an_opaque_secure_cookie(
    login: tuple[httpx.AsyncClient, MemorySessions, dict[str, str]],
) -> None:
    http, store, provider = login
    query = await begin(http, store, provider)
    callback = await http.get(
        "/auth/callback", params={"code": "issued-code", "state": query["state"]}
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == f"{ORIGIN}/dashboard/connections"
    cookie = http.cookies.get("__Host-kinu")
    assert cookie and len(cookie) == 43 and "." not in cookie
    headers = callback.headers.get_list("set-cookie")
    assert any(
        "HttpOnly" in value and "Secure" in value and "SameSite=lax" in value for value in headers
    )
    assert callback.headers["cache-control"] == "no-store"
    assert len(store.sessions) == 1
    current = await http.get("/auth/session")
    assert current.status_code == 200
    assert current.json()["identity"]["emailVerified"] is True
    assert "private-access" not in current.text + str(headers)
    assert "private-refresh" not in current.text + str(headers)


@pytest.mark.parametrize("failure", ["state", "binding", "nonce", "expired"])
async def test_invalid_login_cannot_create_a_session(
    login: tuple[httpx.AsyncClient, MemorySessions, dict[str, str]], failure: str
) -> None:
    http, store, provider = login
    query = await begin(http, store, provider)
    state = query["state"]
    if failure == "state":
        state = "forged"
    elif failure == "binding":
        http.cookies.clear()
    elif failure == "nonce":
        provider["failure"] = "nonce"
    else:
        key, value = next(iter(store.attempts.items()))
        store.attempts[key] = type(value)(
            value.verifier, value.nonce, value.return_path, datetime.now(UTC) - timedelta(seconds=1)
        )
    response = await http.get("/auth/callback", params={"code": "issued-code", "state": state})
    assert response.headers["location"] == f"{ORIGIN}/auth?error=signin"
    assert not store.sessions


async def test_callback_cannot_be_replayed(
    login: tuple[httpx.AsyncClient, MemorySessions, dict[str, str]],
) -> None:
    http, store, provider = login
    query = await begin(http, store, provider)
    binding = http.cookies.get("__Host-kinu.login")
    params = {"code": "issued-code", "state": query["state"]}
    await http.get("/auth/callback", params=params)
    replay = await http.get(
        "/auth/callback", params=params, headers={"Cookie": f"__Host-kinu.login={binding}"}
    )
    assert replay.headers["location"] == f"{ORIGIN}/auth?error=signin"
    assert provider["calls"] == "1"


async def test_logout_revokes_the_server_session(
    login: tuple[httpx.AsyncClient, MemorySessions, dict[str, str]],
) -> None:
    http, store, provider = login
    query = await begin(http, store, provider)
    await http.get("/auth/callback", params={"code": "issued-code", "state": query["state"]})
    current = await http.get("/auth/session")
    rejected = await http.post("/auth/logout")
    assert rejected.status_code == 403 and store.sessions
    accepted = await http.post(
        "/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": current.json()["csrf_token"]}
    )
    assert accepted.status_code == 200
    assert not store.sessions
    assert (await http.get("/auth/session")).status_code == 401
    assert accepted.json()["logout_url"].startswith("https://login.example.com/logout?")


async def test_logout_is_safe_to_repeat_after_expiry(
    login: tuple[httpx.AsyncClient, MemorySessions, dict[str, str]],
) -> None:
    http, _, _ = login
    response = await http.post("/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (await http.post("/auth/logout")).status_code == 403


@pytest.mark.parametrize(
    "path", ["//attacker.example", "https://attacker.example", "/auth", "/dashboard\\evil"]
)
async def test_return_path_cannot_redirect_to_another_site(
    login: tuple[httpx.AsyncClient, MemorySessions, dict[str, str]], path: str
) -> None:
    response = await login[0].get("/auth/login", params={"return_to": path})
    assert response.status_code == 401
