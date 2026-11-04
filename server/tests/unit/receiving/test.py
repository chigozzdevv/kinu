import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser

import httpx
import pytest
from mail.receiving import Receiving, confirmation, verify_webhook
from pydantic import SecretStr

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 5, tzinfo=UTC)
KEY = b"receiving-signature-fixture-32bytes"
SECRET = SecretStr("whsec_" + base64.b64encode(KEY).decode())


def signed(body: bytes, at: datetime = NOW) -> dict[str, str]:
    timestamp = str(int(at.timestamp()))
    content = b"msg_fixture." + timestamp.encode() + b"." + body
    signature = base64.b64encode(hmac.new(KEY, content, hashlib.sha256).digest()).decode()
    return {
        "svix-id": "msg_fixture",
        "svix-timestamp": timestamp,
        "svix-signature": "v1," + signature,
    }


def test_receiving_signatures_bind_the_original_body_and_reject_old_or_forged_requests() -> None:
    body = b'{"type":"email.received"}'
    headers = signed(body)
    verify_webhook(body, headers, SECRET, NOW)
    verify_webhook(
        body, headers | {"svix-signature": "v1,obsolete " + headers["svix-signature"]}, SECRET, NOW
    )
    for changed, proof in (
        (body + b" ", headers),
        (body, signed(body, NOW - timedelta(minutes=6))),
        (body, signed(body, NOW + timedelta(minutes=6))),
        (body, {}),
        (body, headers | {"svix-signature": "v1,ñ"}),
    ):
        with pytest.raises(ValueError, match="Invalid email webhook signature"):
            verify_webhook(changed, proof, SECRET, NOW)


def test_forwarding_confirmation_checks_google_authentication_and_workspace() -> None:
    address = "kinu-fixture@inbound.example.com"
    url = "https://mail-settings.google.com/mail/vf-fixture?token=example"
    data = {
        "from": "Gmail Forwarding <forwarding-noreply@google.com>",
        "authentication": {"dmarc": "pass"},
        "text": address + " " + url,
    }
    assert confirmation(data, address) == url
    for changed in (
        data | {"from": "sender@example.com"},
        data | {"authentication": {"dmarc": "fail"}},
        data | {"text": "kinu-other@inbound.example.com " + url},
        data | {"text": address + " https://mail-settings.google.com.evil.example/mail/vf-example"},
    ):
        assert confirmation(changed, address) is None


async def test_received_content_is_fetched_privately_and_attachments_are_not_loaded() -> None:
    identity = "37e4414c-5e25-4dbc-a071-43552a4bd53b"
    calls = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url == f"https://api.resend.com/emails/receiving/{identity}"
        assert request.headers["authorization"] == "Bearer private-fixture-token"
        return httpx.Response(
            200,
            json={
                "id": identity,
                "from": "security@example.com",
                "subject": "Credential alert",
                "text": "A key is expiring.",
                "attachments": [{"id": "attachment-one"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        receiving = Receiving(http, SecretStr("private-fixture-token"))
        data = await receiving.get(identity)
        raw = await receiving.raw(data)
        message = BytesParser(policy=policy.default).parsebytes(raw)
        assert "A key is expiring." in message.get_content()
        assert len(calls) == 1
        with pytest.raises(ValueError):
            await receiving.get("../../api-keys")
        with pytest.raises(ValueError):
            await receiving.raw(data | {"subject": "Alert\r\nBcc: someone@example.com"})


async def test_received_email_cannot_change_identity_or_exceed_the_content_limit() -> None:
    identity = "37e4414c-5e25-4dbc-a071-43552a4bd53b"
    for value in ({"id": "another-email"}, {"id": identity, "text": "x" * 1_048_576}):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request, value=value: httpx.Response(200, content=json.dumps(value))
            )
        ) as http:
            with pytest.raises(ValueError):
                await Receiving(http, SecretStr("fixture-token")).get(identity)
