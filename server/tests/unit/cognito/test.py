import json
from collections.abc import AsyncIterator
from time import time

import httpx
import jwt
import pytest
from core.auth.cognito import CognitoConfig, CognitoTokenVerifier
from core.errors import AuthenticationError, IdentityUnavailableError
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

CONFIG = CognitoConfig("us-east-1_example", "kinuclient")
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def claims(**changes: object) -> dict[str, object]:
    now = int(time())
    return {
        "iss": CONFIG.issuer,
        "sub": "user-one",
        "aud": CONFIG.client_id,
        "iat": now,
        "exp": now + 3600,
        "token_use": "id",
        "email": "owner@example.com",
        "email_verified": True,
        "name": "Kinu Owner",
        **changes,
    }


def token(key: rsa.RSAPrivateKey, payload: dict[str, object] | None = None) -> str:
    return jwt.encode(payload or claims(), key, algorithm="RS256", headers={"kid": "one"})


@pytest.fixture
async def verifier(private_key: rsa.RSAPrivateKey) -> AsyncIterator[CognitoTokenVerifier]:
    key = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    key.update(kid="one", alg="RS256", use="sig")

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{CONFIG.issuer}/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": [key]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        yield CognitoTokenVerifier(CONFIG, http)


async def test_verified_identity_uses_pool_and_subject(
    verifier: CognitoTokenVerifier, private_key: rsa.RSAPrivateKey
) -> None:
    identity = await verifier.verify(token(private_key))
    assert identity.subject == f"{CONFIG.issuer}#user-one"
    assert identity.issuer == CONFIG.issuer
    assert identity.email == "owner@example.com"
    assert identity.email_verified
    assert identity.display_name == "Kinu Owner"
    assert identity.connected_via == "Cognito"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("iss", "https://attacker.example"),
        ("aud", "otherclient"),
        ("aud", [CONFIG.client_id]),
        ("token_use", "access"),
        ("sub", ""),
        ("email", ""),
        ("email_verified", False),
        ("email_verified", "true"),
        ("name", 123),
        ("iat", True),
        ("iat", int(time()) + 3600),
        ("exp", int(time()) - 3600),
        ("exp", str(int(time()) + 3600)),
        ("nbf", int(time()) + 3600),
    ],
)
async def test_rejects_invalid_claims(
    verifier: CognitoTokenVerifier,
    private_key: rsa.RSAPrivateKey,
    field: str,
    value: object,
) -> None:
    with pytest.raises(AuthenticationError):
        await verifier.verify(token(private_key, claims(**{field: value})))


@pytest.mark.parametrize(
    "field", ["iss", "sub", "aud", "exp", "iat", "token_use", "email", "email_verified"]
)
async def test_rejects_missing_claims(
    verifier: CognitoTokenVerifier, private_key: rsa.RSAPrivateKey, field: str
) -> None:
    payload = claims()
    del payload[field]
    with pytest.raises(AuthenticationError):
        await verifier.verify(token(private_key, payload))


async def test_rejects_forged_signature(verifier: CognitoTokenVerifier) -> None:
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError):
        await verifier.verify(token(forged_key))


async def test_rejects_algorithm_substitution(verifier: CognitoTokenVerifier) -> None:
    forged = jwt.encode(claims(), "x" * 32, algorithm="HS256", headers={"kid": "one"})
    with pytest.raises(AuthenticationError):
        await verifier.verify(forged)


@pytest.mark.parametrize("value", ["", "not-a-token", "x" * 32769])
async def test_rejects_malformed_tokens(verifier: CognitoTokenVerifier, value: str) -> None:
    with pytest.raises(AuthenticationError):
        await verifier.verify(value)


async def test_rejects_unknown_signing_key(
    verifier: CognitoTokenVerifier, private_key: rsa.RSAPrivateKey
) -> None:
    signed = jwt.encode(claims(), private_key, algorithm="RS256", headers={"kid": "unknown"})
    with pytest.raises(AuthenticationError, match="signing key is unknown"):
        await verifier.verify(signed)


async def test_provider_failure_is_not_reported_as_invalid_credentials(
    private_key: rsa.RSAPrivateKey,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        verifier = CognitoTokenVerifier(CONFIG, http)
        for _ in range(2):
            with pytest.raises(IdentityUnavailableError):
                await verifier.verify(token(private_key))


@pytest.mark.parametrize("pool", ["", "https://attacker.example/pool", "us-east-1_pool/path"])
def test_rejects_invalid_pool_configuration(pool: str) -> None:
    with pytest.raises(ValueError):
        CognitoConfig(pool, CONFIG.client_id)


def test_china_pool_uses_its_own_aws_endpoint() -> None:
    config = CognitoConfig("cn-north-1_example", CONFIG.client_id)
    assert config.issuer == "https://cognito-idp.cn-north-1.amazonaws.com.cn/cn-north-1_example"
