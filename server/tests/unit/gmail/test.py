import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from core.errors import ResourceConflictError
from mail.google import Google, ReconnectError
from mail.models import SCOPE, GoogleConfig, Grant, Mailbox, Sender
from mail.reader import Reader
from mail.vault import Vault
from pydantic import SecretStr

NOW = datetime.now(UTC)
CONFIG = GoogleConfig(
    client_id="kinu.apps.googleusercontent.com",
    client_secret=SecretStr("fixture-client"),
    key=SecretStr("x" * 64),
)
BOX = Mailbox(
    id="gmail_one",
    organisation_id="org_one",
    email="owner@gmail.com",
    connected_at=NOW,
    senders=(Sender(sender="security@provider.example", provider_connection_id="provider_one"),),
)
GRANT = Grant(
    access_token=SecretStr("fixture-access"),
    refresh_token=SecretStr("fixture-refresh"),
    expires_at=NOW + timedelta(hours=1),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_grants_are_encrypted_and_bound_to_the_workspace() -> None:
    vault = Vault(CONFIG)
    value = {"access_token": "fixture-access", "refresh_token": "fixture-refresh"}
    sealed = vault.seal(value, "grant:org_one:gmail_one")
    assert "fixture" not in sealed and "fixture" not in GRANT.model_dump_json()
    assert vault.open(sealed, "grant:org_one:gmail_one") == value
    with pytest.raises(ResourceConflictError):
        vault.open(sealed, "grant:org_other:gmail_one")


@pytest.mark.anyio
async def test_authorization_uses_read_only_access_and_pkce() -> None:
    async with httpx.AsyncClient() as client:
        target, verifier = Google(CONFIG, client).begin(
            "https://kinu.example/connections/gmail/callback", "state"
        )
    values = parse_qs(urlsplit(target).query)
    assert urlsplit(target).hostname == "accounts.google.com"
    assert values["scope"] == [SCOPE]
    assert values["include_granted_scopes"] == ["false"]
    assert values["access_type"] == ["offline"]
    assert values["code_challenge_method"] == ["S256"]
    assert values["code_challenge"] == [
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    ]


@pytest.mark.anyio
async def test_exchange_and_revocation_errors_do_not_expose_tokens() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        form = parse_qs(request.content.decode())
        assert request.url == "https://oauth2.googleapis.com/token"
        assert form["client_secret"] == ["fixture-client"]
        if form["grant_type"] == ["refresh_token"]:
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "fixture-refresh"}
            )
        return httpx.Response(
            200,
            json={
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "expires_in": 3600,
                "scope": SCOPE,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        google = Google(CONFIG, client)
        grant = await google.exchange("code", "verifier", "https://kinu.example/callback")
        assert grant.refresh_token.get_secret_value() == "fixture-refresh"
        with pytest.raises(ReconnectError) as error:
            await google.refresh(grant)
        assert "fixture" not in str(error.value)
        assert "code_verifier" in requests[0].content.decode()


@pytest.mark.anyio
async def test_sender_is_checked_before_any_body_is_fetched() -> None:
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "internalDate": str(int(NOW.timestamp()) * 1000),
                "sizeEstimate": 100,
                "payload": {"headers": [{"name": "From", "value": "attacker@provider.example"}]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await Reader(Google(CONFIG, client)).fetch(
            BOX, GRANT, "abc123", int(NOW.timestamp()) - 1, int(NOW.timestamp()) + 1
        )
    assert result is None and len(paths) == 1 and "format=metadata" in paths[0]


@pytest.mark.anyio
async def test_matching_new_message_is_read_without_modifying_mail() -> None:
    raw = b"From: security@provider.example\r\nSubject: Alert\r\n\r\nA credential was exposed."

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer fixture-access"
        if request.url.params["format"] == "metadata":
            return httpx.Response(
                200,
                json={
                    "internalDate": str(int(NOW.timestamp()) * 1000),
                    "sizeEstimate": len(raw),
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Security <security@provider.example>"}
                        ]
                    },
                },
            )
        return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(raw).decode()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await Reader(Google(CONFIG, client)).fetch(
            BOX, GRANT, "abc123", int(NOW.timestamp()) - 1, int(NOW.timestamp()) + 1
        )
    assert result and result[0] == raw and result[1] == BOX.senders[0]
