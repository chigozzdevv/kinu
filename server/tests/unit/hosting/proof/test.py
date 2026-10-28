from datetime import UTC, datetime

import httpx
import pytest
from connectors.base.errors import ConnectorError
from contracts import ConsumerService
from hosting.proof import Proof

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("change", ["nonce", "fingerprint", "oversized", "identity"])
async def test_health_proof_rejects_replays_wrong_generations_and_large_responses(
    change: str,
) -> None:
    now = datetime.now(UTC)
    service = ConsumerService(
        id="service_one",
        organisation_id="org_one",
        application_id="app_one",
        environment_id="env_one",
        runtime_connection_id="connection_one",
        runtime_resource="render://app/production",
        display_name="App",
        endpoint="https://app.example/health",
        created_at=now,
        updated_at=now,
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if change == "oversized":
            return httpx.Response(200, content=b"x" * 16385)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "nonce": "previous" if change == "nonce" else request.headers["x-kinu-probe"],
                "fingerprints": {"API_KEY": "b" * 64 if change == "fingerprint" else "a" * 64},
                "provider_ids": {"API_KEY": "other" if change == "identity" else "issuer-key"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(ConnectorError, match="prove"):
            await Proof(http).check(service, "API_KEY", "a" * 64, provider_id="issuer-key")
