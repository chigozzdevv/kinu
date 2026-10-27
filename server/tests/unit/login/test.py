import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from api.app import create_app
from core.auth.browser import BrowserAuth
from core.auth.cognito import CognitoConfig, CognitoTokenVerifier
from core.auth.forms import AuthForm
from core.auth.options import BrowserOptions
from core.config import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from tests.browser import MemoryForms, MemorySessions

ORIGIN = "https://kinu.example"
CONFIG = CognitoConfig("us-east-1_example", "kinuclient")
PASSWORD = "Private-test-password-123!"
pytestmark = pytest.mark.anyio
Login = tuple[httpx.AsyncClient, MemorySessions, MemoryForms, dict[str, Any]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
async def login(private_key: rsa.RSAPrivateKey) -> AsyncIterator[Login]:
    store, forms = MemorySessions(), MemoryForms()
    provider: dict[str, Any] = {"claims": {}, "error": "", "calls": [], "challenge": False}
    key = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    key.update(kid="one", alg="RS256", use="sig")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "cognito-idp.us-east-1.amazonaws.com"
        if request.url.path.endswith("jwks.json"):
            return httpx.Response(200, json={"keys": [key]})
        action = request.headers["x-amz-target"].split(".")[-1]
        data = json.loads(request.content)
        assert data["ClientId"] == CONFIG.client_id
        provider["calls"].append((action, data))
        if provider["error"]:
            return httpx.Response(
                400,
                json={
                    "__type": provider["error"],
                    "message": PASSWORD + " private provider detail",
                },
            )
        if action == "InitiateAuth":
            assert data["AuthFlow"] == "USER_PASSWORD_AUTH"
            if provider["challenge"]:
                return httpx.Response(
                    200,
                    json={"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "private-challenge"},
                )
            now = int(datetime.now(UTC).timestamp())
            claims = {
                "iss": CONFIG.issuer,
                "aud": CONFIG.client_id,
                "sub": "user-one",
                "iat": now,
                "exp": now + 3600,
                "token_use": "id",
                "email": "owner@example.com",
                "email_verified": True,
                **provider["claims"],
            }
            token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "one"})
            return httpx.Response(
                200,
                json={
                    "AuthenticationResult": {
                        "IdToken": token,
                        "AccessToken": "private-access",
                        "RefreshToken": "private-refresh",
                    }
                },
            )
        return httpx.Response(200, json={"UserConfirmed": False} if action == "SignUp" else {})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider_http:
        browser = BrowserAuth(
            BrowserOptions(CONFIG, ORIGIN),
            store,
            forms,
            CognitoTokenVerifier(CONFIG, provider_http),
            provider_http,
            lambda: datetime.now(UTC),
        )
        app = create_app(browser=browser, settings=Settings(public_url=ORIGIN))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as http:
            yield http, store, forms, provider


async def headers(http: httpx.AsyncClient) -> dict[str, str]:
    response = await http.get("/auth/form")
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


async def signin(http: httpx.AsyncClient, **fields: str) -> httpx.Response:
    return await http.post(
        "/auth/signin",
        headers=await headers(http),
        json={
            "email": "owner@example.com",
            "password": PASSWORD,
            "return_to": "/dashboard/connections",
            **fields,
        },
    )


async def test_password_login_creates_only_an_opaque_secure_cookie(login: Login) -> None:
    http, store, forms, _ = login
    result = await signin(http)
    assert result.status_code == 200
    assert result.json() == {"next": "complete", "return_to": "/dashboard/connections"}
    token = http.cookies.get("__Host-kinu")
    assert token and len(token) == 43 and "." not in token
    assert "HttpOnly" in result.headers["set-cookie"] and "Secure" in result.headers["set-cookie"]
    assert "SameSite=lax" in result.headers["set-cookie"]
    assert not forms.values
    assert (await http.get("/auth/session")).status_code == 200
    public = result.text + str(store.sessions)
    for secret in (PASSWORD, "private-access", "private-refresh"):
        assert secret not in public


@pytest.mark.parametrize("failure", ["origin", "csrf", "cookie", "expiry", "duplicate"])
async def test_rejects_unbound_forms_before_calling_cognito(login: Login, failure: str) -> None:
    http, _, forms, provider = login
    request_headers = await headers(http)
    if failure == "origin":
        request_headers["Origin"] = "https://other.example"
    elif failure == "csrf":
        request_headers["X-CSRF-Token"] = "forged"
    elif failure == "cookie":
        http.cookies.clear()
    elif failure == "duplicate":
        token = http.cookies.get("__Host-kinu.form")
        request_headers["Cookie"] = f"__Host-kinu.form={token}; __Host-kinu.form={token}"
    else:
        token, form = next(iter(forms.values.items()))
        forms.values[token] = AuthForm(form.csrf_token, datetime.now(UTC) - timedelta(seconds=1))
    response = await http.post(
        "/auth/signin",
        headers=request_headers,
        json={"email": "owner@example.com", "password": PASSWORD},
    )
    assert response.status_code in {401, 403}
    assert not provider["calls"]


async def test_signin_form_cannot_be_replayed(login: Login) -> None:
    http, _, _, provider = login
    request_headers = await headers(http)
    request_headers["Cookie"] = "__Host-kinu.form=" + str(http.cookies.get("__Host-kinu.form"))
    data = {"email": "owner@example.com", "password": PASSWORD}
    assert (await http.post("/auth/signin", headers=request_headers, json=data)).status_code == 200
    assert (await http.post("/auth/signin", headers=request_headers, json=data)).status_code == 401
    assert len(provider["calls"]) == 1


@pytest.mark.parametrize(
    "claims",
    [
        {"email_verified": False},
        {"aud": "another-client"},
        {"iss": "https://other.example"},
        {"token_use": "access"},
        {"email": "other@example.com"},
        {"exp": 1},
    ],
)
async def test_rejects_signed_tokens_for_wrong_identity_or_context(
    login: Login, claims: dict[str, Any]
) -> None:
    http, store, _, provider = login
    provider["claims"] = claims
    assert (await signin(http)).status_code == 401
    assert not store.sessions


@pytest.mark.parametrize(
    ("error", "step"),
    [("UserNotConfirmedException", "verify"), ("PasswordResetRequiredException", "forgot")],
)
async def test_routes_accounts_that_need_verification_or_reset(
    login: Login, error: str, step: str
) -> None:
    http, store, _, provider = login
    provider["error"] = error
    response = await signin(http)
    assert response.json()["next"] == step
    assert not store.sessions
    assert PASSWORD not in response.text


async def test_signup_verification_and_resend_use_the_same_pool(login: Login) -> None:
    http, _, _, provider = login
    request_headers = await headers(http)
    signup = await http.post(
        "/auth/signup",
        headers=request_headers,
        json={"email": " Owner@example.com ", "name": "Owner", "password": PASSWORD},
    )
    assert signup.status_code == 202 and signup.json()["next"] == "verify"
    assert (
        await http.post(
            "/auth/resend", headers=request_headers, json={"email": "owner@example.com"}
        )
    ).status_code == 202
    verified = await http.post(
        "/auth/verify",
        headers=request_headers,
        json={"email": "owner@example.com", "code": "123456"},
    )
    assert verified.status_code == 200 and verified.json()["next"] == "signin"
    assert [call[0] for call in provider["calls"]] == [
        "SignUp",
        "ResendConfirmationCode",
        "ConfirmSignUp",
    ]
    assert provider["calls"][0][1]["Username"] == "owner@example.com"
    assert provider["calls"][2][1]["ConfirmationCode"] == "123456"


async def test_password_reset_revokes_kinu_sessions(login: Login) -> None:
    http, store, _, _ = login
    assert (await signin(http)).status_code == 200
    request_headers = await headers(http)
    assert (
        await http.post(
            "/auth/forgot", headers=request_headers, json={"email": "owner@example.com"}
        )
    ).status_code == 202
    reset = await http.post(
        "/auth/reset",
        headers=request_headers,
        json={"email": "owner@example.com", "code": "123456", "password": PASSWORD},
    )
    assert reset.status_code == 200 and not store.sessions
    assert (await http.get("/auth/session")).status_code == 401


@pytest.mark.parametrize("error", ["UserNotFoundException", "NotAuthorizedException"])
async def test_credentials_and_account_recovery_do_not_leak_provider_details(
    login: Login, error: str
) -> None:
    http, _, _, provider = login
    provider["error"] = error
    result = await signin(http)
    assert result.status_code == 401
    assert result.json()["message"] == "Email or password is incorrect."
    recovery = await http.post(
        "/auth/forgot", headers=await headers(http), json={"email": "unknown@example.com"}
    )
    assert recovery.status_code == 202
    assert PASSWORD not in result.text + recovery.text


async def test_validation_never_echoes_passwords_or_codes(login: Login) -> None:
    http, _, _, provider = login
    response = await http.post(
        "/auth/signin",
        headers=await headers(http),
        json={"email": "invalid", "password": PASSWORD, "unexpected": "private-code"},
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text and "private-code" not in response.text
    bad_code = await http.post(
        "/auth/verify",
        headers=await headers(http),
        json={"email": "owner@example.com", "code": "abcdef"},
    )
    assert bad_code.status_code == 422
    assert "abcdef" not in bad_code.text and not provider["calls"]


async def test_additional_cognito_challenges_cannot_be_bypassed(login: Login) -> None:
    http, store, _, provider = login
    provider["challenge"] = True
    response = await signin(http)
    assert response.status_code == 403 and not store.sessions
    assert "private-challenge" not in response.text


async def test_logout_revokes_the_server_session(login: Login) -> None:
    http, store, _, _ = login
    await signin(http)
    current = await http.get("/auth/session")
    assert (await http.post("/auth/logout")).status_code == 403
    response = await http.post(
        "/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": current.json()["csrf_token"]}
    )
    assert response.status_code == 200 and not store.sessions
    assert response.json()["logout_url"] == ORIGIN + "/auth"
    assert (await http.get("/auth/session")).status_code == 401


@pytest.mark.parametrize(
    "path", ["//attacker.example", "https://attacker.example", "/auth", "/dashboard\\evil"]
)
async def test_return_path_cannot_leave_the_dashboard(login: Login, path: str) -> None:
    response = await signin(login[0], return_to=path)
    assert response.status_code == 401 and not login[3]["calls"]
