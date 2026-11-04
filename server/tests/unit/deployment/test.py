from datetime import UTC, datetime

import httpx
import pytest
from api.app import create_app
from core.auth import AuthenticatedIdentity
from core.auth.browser import BrowserAuth
from core.config import Settings
from pydantic import SecretStr, ValidationError
from tests.browser import MemoryForms, MemoryPasswords, MemorySessions

FRONT = "https://kinu.web.app"
API = "https://kinu-api.example.com"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_split_hosting_signin_cookie_cors_and_csrf_protection() -> None:
    config = Settings(
        public_url=FRONT,
        api_public_url=API,
        environment="production",
        cookie_samesite="none",
        auth_secret=SecretStr("fixture-auth-key-at-least-thirty-two"),
    )
    identity = AuthenticatedIdentity(
        subject="deployment-user",
        issuer=FRONT + "/auth",
        email="owner@example.com",
        email_verified=True,
    )
    assert config.browser is not None
    browser = BrowserAuth(
        config.browser,
        MemorySessions(),
        MemoryForms(),
        MemoryPasswords(identity),
        lambda: datetime.now(UTC),
    )
    app = create_app(browser=browser, settings=config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API) as http:
        preflight = await http.options(
            "/auth/signin",
            headers={
                "origin": FRONT,
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type,x-csrf-token",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == FRONT
        assert preflight.headers["access-control-allow-credentials"] == "true"
        form = await http.get("/auth/form", headers={"origin": FRONT})
        cookie = form.headers["set-cookie"].lower()
        assert "__host-kinu.form=" in cookie and "samesite=none" in cookie
        assert "httponly" in cookie and "secure" in cookie
        response = await http.post(
            "/auth/signin",
            json={"email": identity.email, "password": "fixture-password"},
            headers={"origin": FRONT, "x-csrf-token": form.json()["csrf_token"]},
        )
        assert response.status_code == 200, response.text
        assert any(
            "__Host-kinu=" in cookie and "SameSite=none" in cookie
            for cookie in response.headers.get_list("set-cookie")
        )
        session = await http.get("/auth/session", headers={"origin": FRONT})
        assert session.status_code == 200
        assert session.json()["identity"]["email"] == identity.email
        assert session.headers["access-control-allow-origin"] == FRONT
        csrf = session.json()["csrf_token"]
        for origin, token in (("https://attacker.example", csrf), (FRONT, "wrong-csrf")):
            denied = await http.post(
                "/auth/logout", headers={"origin": origin, "x-csrf-token": token}
            )
            assert denied.status_code == 403
        logout = await http.post("/auth/logout", headers={"origin": FRONT, "x-csrf-token": csrf})
        assert logout.status_code == 200 and logout.json()["logout_url"] == FRONT + "/auth"
        assert (await http.get("/auth/session", headers={"origin": FRONT})).status_code == 401
        for callback in ("/connections/railway/callback", "/connections/gmail/callback"):
            returned = await http.get(callback, params={"state": "a" * 43})
            assert returned.status_code == 303
            assert returned.headers["location"].startswith(FRONT + "/dashboard/connections?")
        denied = await http.options(
            "/auth/signin",
            headers={"origin": "https://attacker.example", "access-control-request-method": "POST"},
        )
        assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers


def test_split_hosting_requires_https_and_preserves_single_origin_defaults() -> None:
    with pytest.raises(ValidationError):
        Settings(api_public_url="http://localhost:8000", cookie_samesite="none")
    config = Settings(public_url=FRONT, api_public_url=API, environment="production")
    assert (
        config.frontend_location("/dashboard/connections?hosting=ready")
        == FRONT + "/dashboard/connections?hosting=ready"
    )
    legacy = Settings()
    assert legacy.api_origin == legacy.public_url
    assert legacy.cookie_samesite == "lax"
    assert legacy.frontend_location("/dashboard") == "/dashboard"
