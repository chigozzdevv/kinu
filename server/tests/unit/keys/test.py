import asyncio
import json

import httpx
import pytest
from core.auth.keys import SigningKeys
from core.errors import AuthenticationError, IdentityUnavailableError
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

URL = "https://identity.example/.well-known/jwks.json"
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def public_key() -> dict[str, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return dict(json.loads(RSAAlgorithm.to_jwk(key.public_key())), kid="one", alg="RS256")


async def test_concurrent_verification_fetches_keys_once(public_key: dict[str, object]) -> None:
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json={"keys": [public_key]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        keys = SigningKeys(URL, http)
        found = await asyncio.gather(*(keys.get("one") for _ in range(10)))
    assert calls == 1
    assert all(item.key_id == "one" for item in found)


async def test_new_key_refreshes_cache_without_request_flooding(
    public_key: dict[str, object],
) -> None:
    now = 0.0
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        key = public_key if calls == 1 else {**public_key, "kid": "two"}
        return httpx.Response(200, json={"keys": [key]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        keys = SigningKeys(URL, http, clock=lambda: now)
        await keys.get("one")
        for _ in range(5):
            with pytest.raises(AuthenticationError):
                await keys.get("unknown")
        assert calls == 1
        now = 11
        assert (await keys.get("two")).key_id == "two"
        assert calls == 2
        now = 312
        await keys.get("two")
        assert calls == 3


@pytest.mark.parametrize("body", [{}, {"keys": []}, {"keys": ["invalid"]}])
async def test_rejects_invalid_provider_responses(body: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as http:
        with pytest.raises(IdentityUnavailableError):
            await SigningKeys(URL, http).get("one")


async def test_rejects_duplicate_key_ids(public_key: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"keys": [public_key, public_key]})
        )
    ) as http:
        with pytest.raises(IdentityUnavailableError):
            await SigningKeys(URL, http).get("one")


async def test_does_not_follow_provider_redirects() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"Location": "https://attacker.example"})
        )
    ) as http:
        with pytest.raises(IdentityUnavailableError):
            await SigningKeys(URL, http).get("one")
