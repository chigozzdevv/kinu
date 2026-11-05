import json
from collections.abc import AsyncIterator

import httpx
import pytest
from agents.native import NativeAgent
from connectors.base.errors import ConnectorError
from core.errors import ResourceConflictError, ResourceSetupRequiredError
from core.private import PrivateClient
from pydantic import SecretStr


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_public_explanation_arrives_before_the_final_result() -> None:
    received: list[str] = []
    result = {"control": "create_key", "summary": "I will create a scoped replacement."}

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield json.dumps({"type": "summary", "content": result["summary"]}).encode() + b"\n"
            assert received == [result["summary"]]
            yield json.dumps({"type": "result", "result": result}).encode() + b"\n"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-worker-token"
        assert request.url.path == "/_internal/navigation"
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, stream=Body())

    async def progress(text: str) -> None:
        received.append(text)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        agent = NativeAgent(
            PrivateClient(http, "https://agent.internal", SecretStr("private-worker-token"))
        )
        assert await agent.navigate("org_one", {"messages": []}, progress) == result


@pytest.mark.anyio
@pytest.mark.parametrize(
    "events",
    [
        [{"type": "summary", "content": "partial"}],
        [{"type": "summary", "content": "one"}, {"type": "summary", "content": "changed"}],
        [
            {"type": "summary", "content": "one"},
            {"type": "result", "result": {"summary": "different"}},
        ],
        [{"type": "summary", "content": "x" * 4097}],
        [{"type": "error", "message": "unavailable"}],
    ],
)
async def test_incomplete_or_inconsistent_streams_cannot_return_an_action(
    events: list[dict[str, object]],
) -> None:
    wire = "".join(json.dumps(event) + "\n" for event in events)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "application/x-ndjson"},
                content=wire,
            )
        )
    ) as http:
        agent = NativeAgent(
            PrivateClient(http, "https://agent.internal", SecretStr("worker-token"))
        )
        with pytest.raises(ResourceConflictError):
            await agent.navigate("org_one", {})


@pytest.mark.anyio
async def test_daily_limit_retains_its_distinct_error_code() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "application/x-ndjson"},
                content=json.dumps({"type": "error", "code": "ai-daily-limit"}) + "\n",
            )
        )
    ) as http:
        agent = NativeAgent(
            PrivateClient(http, "https://agent.internal", SecretStr("worker-token"))
        )
        with pytest.raises(ConnectorError) as error:
            await agent.navigate("org_one", {})
        assert error.value.code == "ai-daily-limit"


@pytest.mark.anyio
async def test_rotation_plan_uses_private_agent_and_distinguishes_missing_setup() -> None:
    facts = {
        "trigger": "exposure",
        "method": "api",
        "app_count": 1,
        "approval_required": True,
        "observation_seconds": 60,
    }
    response = {
        "strategy": "parallel",
        "stages": ["create", "store"],
        "rationale": "Keep the old key until verified.",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/_internal/rotation-plan"
        assert request.headers["Authorization"] == "Bearer worker-token"
        assert json.loads(request.content) == {"organisation_id": "org_one", "input": facts}
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        agent = NativeAgent(
            PrivateClient(http, "https://agent.internal", SecretStr("worker-token"))
        )
        assert await agent.plan_rotation("org_one", facts) == response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as http:
        agent = NativeAgent(
            PrivateClient(http, "https://agent.internal", SecretStr("worker-token"))
        )
        with pytest.raises(ResourceSetupRequiredError):
            await agent.plan_rotation("org_one", facts)
