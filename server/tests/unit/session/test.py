import httpx
import pytest
from api.app import create_app
from api.deps import Identity
from core.auth import AuthenticatedIdentity
from core.auth.cognito import CognitoTokenVerifier
from core.config import Settings
from core.errors import AuthenticationError, IdentityUnavailableError
from fastapi import FastAPI
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Tokens:
    async def verify(self, token: str) -> AuthenticatedIdentity:
        if token == "unavailable":
            raise IdentityUnavailableError("identity signing keys are unavailable")
        if token != "valid":
            raise AuthenticationError("identity token is invalid")
        return AuthenticatedIdentity(
            subject="configured-provider#user-one", issuer="https://identity.example"
        )


def application() -> FastAPI:
    app = create_app(tokens=Tokens(), settings=Settings())

    @app.get("/identity")
    async def identity(current: Identity) -> dict[str, str]:
        return {"subject": current.subject}

    return app


async def test_identity_verification_is_independent_of_domain_service_setup() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as http:
        response = await http.get("/identity", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert response.json() == {"subject": "configured-provider#user-one"}


@pytest.mark.parametrize("authorization", ["Basic valid", "Bearer ", "Bearer invalid"])
async def test_rejects_invalid_authorization(authorization: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as http:
        response = await http.get("/identity", headers={"Authorization": authorization})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_provider_outage_returns_retryable_service_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application()), base_url="http://test"
    ) as http:
        response = await http.get("/identity", headers={"Authorization": "Bearer unavailable"})
    assert response.status_code == 503
    assert response.json()["code"] == "identity-unavailable"


async def test_configured_cognito_verifier_follows_application_lifecycle() -> None:
    app = create_app(
        settings=Settings(cognito_pool_id="us-east-1_example", cognito_client_id="kinuclient")
    )
    before = app.state.tokens
    assert before is None
    async with app.router.lifespan_context(app):
        current = app.state.tokens
        assert isinstance(current, CognitoTokenVerifier)
    after = app.state.tokens
    assert after is None


@pytest.mark.parametrize(
    "values",
    [
        {"cognito_pool_id": "us-east-1_example"},
        {"cognito_client_id": "kinuclient"},
        {"cognito_pool_id": "https://attacker.example", "cognito_client_id": "kinuclient"},
        {"cognito_pool_id": "us-east-1_example", "cognito_client_id": "bad/client"},
    ],
)
def test_partial_or_invalid_cognito_configuration_is_rejected(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)
