from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from core.auth import AccessControl, AuthenticatedIdentity, PrincipalGrant, Role
from mail.bridge import Bridge
from mail.models import Mailbox
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio
IDENTITY = AuthenticatedIdentity(
    subject="owner", issuer="https://identity.example", email="owner@example.com"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Access:
    def __init__(self, role: Role) -> None:
        self.role = role

    async def get(
        self, organisation_id: str, identity: AuthenticatedIdentity
    ) -> PrincipalGrant | None:
        return (
            PrincipalGrant(subject=identity.subject, roles=frozenset({self.role}))
            if organisation_id == "org_one"
            else None
        )


class Mail(Bridge):
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def begin(self, org: str, actor: str, binding: str, identity: str | None) -> str:
        self.calls.append((org, binding))
        assert len(binding) == 64 and "valid-token" not in binding
        return "https://accounts.google.com/o/oauth2/v2/auth?state=fixture"

    async def owner(self, state: str, actor: str, binding: str) -> str:
        return "org_one"

    async def finish(
        self, org: str, actor: str, binding: str, state: str, code: str | None
    ) -> Mailbox:
        self.calls.append(code)
        return Mailbox(
            id="gmail_one",
            organisation_id=org,
            email="owner@gmail.com",
            connected_at=datetime.now(UTC),
        )


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ADMINISTRATOR])
async def test_gmail_setup_requires_workspace_admin_and_csrf(role: Role) -> None:
    gmail = Mail()
    app = create_app(
        ApiServices(access=AccessControl(Access(role)), gmail=gmail), browser=make_browser(IDENTITY)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        path = "/v1/organisations/org_one/connections/gmail"
        assert (await client.post(path, json={})).status_code == 401
        assert (
            await client.post(path, headers={"Cookie": HEADERS["Cookie"]}, json={})
        ).status_code == 403
        response = await client.post(path, headers=HEADERS, json={})
        assert response.status_code == (200 if role is Role.ADMINISTRATOR else 403)
    assert bool(gmail.calls) == (role is Role.ADMINISTRATOR)


async def test_callback_does_not_forward_oauth_code_or_state_to_the_browser() -> None:
    gmail = Mail()
    app = create_app(
        ApiServices(access=AccessControl(Access(Role.ADMINISTRATOR)), gmail=gmail),
        browser=make_browser(IDENTITY),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/connections/gmail/callback?code=private-code&state=private-state", headers=HEADERS
        )
        assert response.status_code == 303
        assert (
            response.headers["location"]
            == "/dashboard/connections?gmail=gmail_one&workspace=org_one"
        )
        assert (
            "private" not in str(response.headers)
            and response.headers["cache-control"] == "no-store"
        )
        assert response.headers["referrer-policy"] == "no-referrer"
        expired = await client.get(
            "/connections/gmail/callback?code=private-code&state=private-state"
        )
        assert expired.headers["location"] == "/dashboard/connections?gmail=expired"
