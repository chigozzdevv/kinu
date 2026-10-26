import httpx
import pytest
from api.app import create_app
from api.deps import Identity
from core.auth import AuthenticatedIdentity
from core.auth.browser import BrowserAuth
from core.config import Settings
from core.errors import StorageUnavailableError
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def application() -> FastAPI:
    app = create_app(
        browser=make_browser(
            AuthenticatedIdentity(
                subject="configured-provider#user-one", issuer="https://id.example"
            )
        )
    )

    @app.get("/identity")
    @app.post("/identity")
    async def identity(current: Identity) -> dict[str, str]:
        return {"subject": current.subject}

    return app


async def test_session_verification_is_independent_of_domain_service_setup() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as http:
        response = await http.get("/identity", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"subject": "configured-provider#user-one"}


@pytest.mark.parametrize(
    "cookie", ["", "kinu.session=invalid", "kinu.session=valid-token; kinu.session=valid-token"]
)
async def test_rejects_invalid_or_duplicate_cookies(cookie: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as http:
        response = await http.get("/identity", headers={"Cookie": cookie})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Cookie": HEADERS["Cookie"]},
        {**HEADERS, "Origin": "https://attacker.example"},
        {**HEADERS, "X-CSRF-Token": "incorrect"},
    ],
)
async def test_cookie_mutations_require_origin_and_csrf(headers: dict[str, str]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as http:
        rejected = await http.post("/identity", headers=headers)
        accepted = await http.post("/identity", headers=HEADERS)
    assert rejected.status_code == 403
    assert accepted.status_code == 200


async def test_bearer_tokens_do_not_replace_browser_sessions() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as http:
        response = await http.get("/identity", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 401


async def test_storage_outage_returns_retryable_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = application()

    async def unavailable(token: str) -> None:
        raise StorageUnavailableError("application storage is unavailable")

    monkeypatch.setattr(app.state.browser.store, "get", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/identity", headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["code"] == "storage-unavailable"


async def test_browser_authentication_follows_application_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []

    class Database:
        def __init__(self, url: str) -> None:
            assert url == "postgresql://test"

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    monkeypatch.setattr("api.runtime.Database", Database)
    app = create_app(
        settings=Settings(
            cognito_pool_id="us-east-1_example",
            cognito_client_id="kinuclient",
            cognito_domain="https://login.example.com",
            database_url=SecretStr("postgresql://test"),
        )
    )
    before = app.state.browser
    assert before is None
    async with app.router.lifespan_context(app):
        current = app.state.browser
        assert isinstance(current, BrowserAuth)
    after = app.state.browser
    assert after is None
    assert events == ["open", "close"]


@pytest.mark.parametrize(
    "values",
    [
        {"cognito_pool_id": "us-east-1_example"},
        {"cognito_client_id": "kinuclient"},
        {"cognito_pool_id": "https://attacker.example", "cognito_client_id": "kinuclient"},
        {"cognito_pool_id": "us-east-1_example", "cognito_client_id": "bad/client"},
        {"cognito_domain": "https://login.example.com"},
        {"environment": "production", "public_url": "http://localhost:5173"},
    ],
)
def test_invalid_authentication_configuration_is_rejected(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(values)
