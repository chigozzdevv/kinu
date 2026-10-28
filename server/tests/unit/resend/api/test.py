import json

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from core.auth import AccessControl, AuthenticatedIdentity, PrincipalGrant, Role
from core.private import PrivateClient
from pydantic import SecretStr
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
IDENTITY = AuthenticatedIdentity(subject="resend-owner", issuer="local")
DOMAIN = "0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c"
TOKEN = "private-management-token-fixture"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Access:
    def __init__(self, role: Role) -> None:
        self.role = role

    async def get(self, org: str, identity: AuthenticatedIdentity) -> PrincipalGrant | None:
        if org != "org_one" or identity != IDENTITY:
            return None
        return PrincipalGrant(subject=identity.subject, roles=frozenset({self.role}))


@pytest.mark.parametrize("role", [Role.ADMINISTRATOR, Role.VIEWER])
async def test_domain_lookup_keeps_management_tokens_private_and_requires_workspace_write_access(
    role: Role,
) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/providers/resend/domains"
        assert json.loads(request.content) == {"organisation_id": "org_one", "token": TOKEN}
        calls.append(request.url.path)
        return httpx.Response(200, json=[{"id": DOMAIN, "name": "example.com"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as private:
        services = ApiServices(
            access=AccessControl(Access(role)),
            private=PrivateClient(private, "http://executor", SecretStr("service-token")),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(services, browser=make_browser(IDENTITY))),
            base_url="http://test",
        ) as client:
            path = "/v1/organisations/org_one/connections/resend/domains"
            for headers in ({}, {"Cookie": HEADERS["Cookie"], "Origin": HEADERS["Origin"]}):
                denied = await client.post(path, headers=headers, json={"token": TOKEN})
                assert denied.status_code in {401, 403} and not calls
                assert TOKEN not in denied.text
            response = await client.post(path, headers=HEADERS, json={"token": TOKEN})
            assert response.status_code == (200 if role is Role.ADMINISTRATOR else 403)
            assert len(calls) == (1 if role is Role.ADMINISTRATOR else 0)
            assert TOKEN not in response.text
            invalid = await client.post(
                "/v1/organisations/org_one/connections/provider",
                headers=HEADERS,
                json={
                    "platform": "resend",
                    "display_name": "Resend",
                    "domain_id": [DOMAIN],
                    "token": TOKEN,
                },
            )
            assert invalid.status_code == 422 and TOKEN not in invalid.text
