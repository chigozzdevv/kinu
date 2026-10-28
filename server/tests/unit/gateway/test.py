import json

import httpx
import pytest
from api.app import create_app
from api.deps import ApiServices
from core.auth import AccessControl, AuthenticatedIdentity, PrincipalGrant, Role
from core.config import Settings
from core.private import PrivateClient
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect
from tests.browser import make_browser

IDENTITY = AuthenticatedIdentity(subject="gateway-user", issuer="test")


class Access:
    async def get(self, org: str, identity: AuthenticatedIdentity) -> PrincipalGrant | None:
        return (
            PrincipalGrant(subject=identity.subject, roles=frozenset({Role.ADMINISTRATOR}))
            if org == "org_one"
            else None
        )


@pytest.mark.parametrize(
    "origin,cookie,org,success",
    [
        ("http://localhost:5173", "kinu.session=valid-token", "org_one", True),
        ("https://attacker.example", "kinu.session=valid-token", "org_one", False),
        ("http://localhost:5173", "", "org_one", False),
        ("http://localhost:5173", "kinu.session=valid-token", "org_other", False),
    ],
)
def test_gateway_requires_current_cookie_origin_workspace_and_private_setup_token(
    origin: str,
    cookie: str,
    org: str,
    success: bool,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["subject"] == IDENTITY.subject
        assert body["token"] == "setup-token-private"
        assert body["data"] == {"type": "status"}
        calls.append(body)
        return httpx.Response(200, json={"type": "status", "authenticated": False})

    private = PrivateClient(
        httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        "http://executor",
        SecretStr("private"),
    )
    app = create_app(
        ApiServices(access=AccessControl(Access()), private=private),
        browser=make_browser(IDENTITY),
        settings=Settings(mongo_url=SecretStr("")),
    )
    client = TestClient(app)

    def connect() -> None:
        with client.websocket_connect(
            "/browser-gateway/v1/setup/live", headers={"Origin": origin, "Cookie": cookie}
        ) as socket:
            socket.send_json(
                {"organisation_id": org, "setup_id": "setup_one", "token": "setup-token-private"}
            )
            socket.send_json({"type": "status"})
            assert socket.receive_json() == {"type": "status", "authenticated": False}

    if success:
        connect()
        assert len(calls) == 1
    else:
        with pytest.raises(WebSocketDisconnect):
            connect()
        assert not calls
