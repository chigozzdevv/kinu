from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import httpx
import pytest
from api.app import create_app
from core.auth import AuthenticatedIdentity
from core.auth.browser import BrowserAuth
from core.auth.forms import AuthForm
from core.auth.options import BrowserOptions
from core.config import Settings
from tests.browser import MemoryForms, MemoryPasswords, MemorySessions

ORIGIN = "https://kinu.example"
PASSWORD = "Private-test-password-123!"
pytestmark = pytest.mark.anyio
Login = tuple[httpx.AsyncClient, MemorySessions, MemoryForms, MemoryPasswords]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def login() -> AsyncIterator[Login]:
    store, forms = MemorySessions(), MemoryForms()
    passwords = MemoryPasswords(
        AuthenticatedIdentity(
            subject="user_one",
            issuer=ORIGIN + "/auth",
            email="owner@example.com",
            email_verified=True,
            display_name="Owner",
            connected_via="Email and password",
        )
    )
    browser = BrowserAuth(
        BrowserOptions(ORIGIN), store, forms, passwords, lambda: datetime.now(UTC)
    )
    app = create_app(browser=browser, settings=Settings(public_url=ORIGIN))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
        yield http, store, forms, passwords


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


async def test_signin_uses_an_opaque_secure_twelve_hour_session(login: Login) -> None:
    http, store, forms, _ = login
    now = datetime.now(UTC)
    response = await signin(http)
    assert response.json() == {"next": "complete", "return_to": "/dashboard/connections"}
    token = http.cookies.get("__Host-kinu")
    assert token and len(token) == 43 and "." not in token
    assert not forms.values
    cookie = SimpleCookie()
    for value in response.headers.get_list("set-cookie"):
        cookie.load(value)
    assert cookie["__Host-kinu"]["httponly"] and cookie["__Host-kinu"]["secure"]
    assert 43195 <= int(cookie["__Host-kinu"]["max-age"]) <= 43200
    assert 43195 <= (store.sessions[token].expires_at - now).total_seconds() <= 43201
    public = (await http.get("/auth/session")).text + response.text
    assert PASSWORD not in public and token not in public


@pytest.mark.parametrize("failure", ["origin", "csrf", "cookie", "expiry", "duplicate"])
async def test_forms_are_browser_bound_and_checked_before_passwords(
    login: Login, failure: str
) -> None:
    http, _, forms, passwords = login
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
    assert not passwords.calls


async def test_form_replay_and_unverified_accounts_cannot_create_sessions(login: Login) -> None:
    http, store, _, passwords = login
    request_headers = await headers(http)
    request_headers["Cookie"] = "__Host-kinu.form=" + str(http.cookies.get("__Host-kinu.form"))
    body = {"email": "owner@example.com", "password": PASSWORD}
    assert (await http.post("/auth/signin", headers=request_headers, json=body)).status_code == 200
    assert (await http.post("/auth/signin", headers=request_headers, json=body)).status_code == 401
    passwords.next = "verify"
    store.sessions.clear()
    assert (await signin(http)).json()["next"] == "verify"
    assert not store.sessions


async def test_verification_reset_and_logout_keep_the_existing_flow(login: Login) -> None:
    http, store, _, passwords = login
    request_headers = await headers(http)
    assert (
        await http.post(
            "/auth/signup",
            headers=request_headers,
            json={
                "email": "owner@example.com",
                "name": "Owner",
                "password": PASSWORD,
            },
        )
    ).json()["next"] == "verify"
    assert (
        await http.post(
            "/auth/resend", headers=request_headers, json={"email": "owner@example.com"}
        )
    ).status_code == 202
    assert (
        await http.post(
            "/auth/verify",
            headers=request_headers,
            json={"email": "owner@example.com", "code": "123456"},
        )
    ).json()["next"] == "signin"
    signed = await signin(http)
    assert signed.status_code == 200, signed.text
    current = (await http.get("/auth/session")).json()
    assert (await http.post("/auth/logout")).status_code == 403
    assert (
        await http.post(
            "/auth/logout",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": current["csrf_token"],
            },
        )
    ).status_code == 200
    assert not store.sessions
    assert (await http.get("/auth/session")).status_code == 401
    assert passwords.calls[:3] == ["signup", "resend", "verify"]


@pytest.mark.parametrize(
    "path", ["//attacker.example", "https://attacker.example", "/auth", "/dashboard\\evil"]
)
async def test_return_paths_and_validation_do_not_expose_secrets(login: Login, path: str) -> None:
    http, _, _, passwords = login
    assert (await signin(http, return_to=path)).status_code == 401
    assert not passwords.calls
    response = await http.post(
        "/auth/signin",
        headers=await headers(http),
        json={"email": "invalid", "password": PASSWORD, "unexpected": "private-code"},
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text and "private-code" not in response.text
