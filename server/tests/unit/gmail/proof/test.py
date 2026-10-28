import base64
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

import dkim
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mail.proof import authenticated


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def signed() -> tuple[bytes, bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = (
        "security@provider.example",
        "owner@gmail.com",
        "Credential exposed",
    )
    message["Date"] = format_datetime(datetime.now(UTC))
    message.set_content("Credential key_known was exposed. password=fixture-private.")
    raw = message.as_bytes().replace(b"\n", b"\r\n")
    return raw, private, b"v=DKIM1; k=rsa; p=" + base64.b64encode(public)


@pytest.mark.anyio
async def test_aligned_signature_is_required_and_forged_headers_do_not_help(
    signed: tuple[bytes, bytes, bytes],
) -> None:
    raw, key, public = signed

    def dns(name: bytes, **kwargs: Any) -> bytes:
        return public

    signature = dkim.sign(
        raw,
        b"mail",
        b"provider.example",
        key,
        include_headers=[
            b"from",
            b"to",
            b"subject",
            b"date",
            b"mime-version",
            b"content-type",
            b"content-transfer-encoding",
        ],
    )
    assert await authenticated(signature + raw, "security@provider.example", dns)
    assert not await authenticated(raw, "security@provider.example", dns)
    assert not await authenticated(
        signature + raw.replace(b"key_known", b"key_other"), "security@provider.example", dns
    )
    forged = b"Authentication-Results: mx.google.com; dkim=pass; dmarc=pass\r\n" + raw
    assert not await authenticated(forged, "security@provider.example", dns)


@pytest.mark.anyio
async def test_partial_body_unsigned_subject_and_other_domains_require_review(
    signed: tuple[bytes, bytes, bytes],
) -> None:
    raw, key, public = signed

    def dns(name: bytes, **kwargs: Any) -> bytes:
        return public

    headers = [
        b"from",
        b"subject",
        b"date",
        b"mime-version",
        b"content-type",
        b"content-transfer-encoding",
    ]
    for signature in (
        dkim.sign(raw, b"mail", b"attacker.example", key, include_headers=headers),
        dkim.sign(raw, b"mail", b"provider.example", key, include_headers=headers, length=True),
        dkim.sign(
            raw,
            b"mail",
            b"provider.example",
            key,
            include_headers=[item for item in headers if item != b"subject"],
        ),
    ):
        assert not await authenticated(signature + raw, "security@provider.example", dns)
