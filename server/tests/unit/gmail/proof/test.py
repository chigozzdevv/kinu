import base64
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any

import dkim
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dns import resolver as dns_resolver
from mail.proof import ProofUnavailableError, authenticated


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=[1024, 2048])
def signed(request: pytest.FixtureRequest) -> tuple[bytes, bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=int(request.param))
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
            include_headers=headers,
            signature_algorithm=b"rsa-sha1",
        ),
        dkim.sign(
            raw,
            b"mail",
            b"provider.example",
            key,
            include_headers=[item for item in headers if item != b"subject"],
        ),
    ):
        assert not await authenticated(signature + raw, "security@provider.example", dns)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [dkim.DnsTimeoutError, dns_resolver.NoNameservers])
async def test_unavailable_dns_is_retryable_instead_of_a_failed_signature(
    signed: tuple[bytes, bytes, bytes],
    failure: type[Exception],
) -> None:
    raw, key, _ = signed
    signature = dkim.sign(
        raw,
        b"mail",
        b"provider.example",
        key,
        include_headers=[
            b"from",
            b"subject",
            b"date",
            b"mime-version",
            b"content-type",
            b"content-transfer-encoding",
        ],
    )

    def unavailable(name: bytes, **kwargs: Any) -> bytes:
        raise failure("temporary lookup failure")

    with pytest.raises(ProofUnavailableError):
        await authenticated(signature + raw, "security@provider.example", unavailable)

    def missing(name: bytes, **kwargs: Any) -> None:
        return None

    assert not await authenticated(signature + raw, "security@provider.example", missing)


@pytest.mark.anyio
async def test_default_resolver_recovers_after_a_transient_failure(
    signed: tuple[bytes, bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, key, public = signed
    signature = dkim.sign(
        raw,
        b"mail",
        b"provider.example",
        key,
        include_headers=[
            b"from",
            b"subject",
            b"date",
            b"mime-version",
            b"content-type",
            b"content-transfer-encoding",
        ],
    )
    calls = 0

    def resolve(name: str, kind: str, **kwargs: Any) -> list[Any]:
        nonlocal calls
        calls += 1
        assert name == "mail._domainkey.provider.example." and kind == "TXT"
        if calls == 1:
            raise dns_resolver.NoNameservers()  # type: ignore[no-untyped-call]
        return [SimpleNamespace(strings=(public,))]

    monkeypatch.setattr("mail.proof.dns_resolver.resolve", resolve)
    with pytest.raises(ProofUnavailableError):
        await authenticated(signature + raw, "security@provider.example")
    assert await authenticated(signature + raw, "security@provider.example")
