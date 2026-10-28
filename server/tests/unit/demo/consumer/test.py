from typing import Any

import demo.consumer as consumer
import httpx
import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    ("statuses", "state"),
    [([503, 200], None), ([401], "provider-rejected"), ([503, 503, 503], "provider-unavailable")],
)
async def test_health_retries_transient_errors_without_confusing_them_with_rejection(
    statuses: list[int], state: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Aws:
        async def call(self, *args: Any, **parameters: Any) -> dict[str, bytes]:
            return {"SecretBinary": b"private-fixture"}

    for key, value in {
        "AWS_REGION": "eu-west-2",
        "KINU_DEMO_KEY": "selected-secret",
        "KINU_DEMO_ORGANISATION": "org_demo",
        "KINU_SECRET_VERSION": "a" * 64,
        "KINU_PROVIDER_URL": "https://provider.example",
    }.items():
        monkeypatch.setenv(key, value)
    client = httpx.AsyncClient
    seen: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer private-fixture"
        status = statuses[len(seen)]
        seen.append(status)
        return httpx.Response(status, json={"ok": True, "provider_id": "b" * 24})

    monkeypatch.setattr(consumer, "AwsClient", Aws)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond)),
    )
    result = await consumer.check({})
    assert seen == statuses
    assert result["ok"] is (state is None)
    assert result.get("state") == state
    assert "private-fixture" not in str(result)
