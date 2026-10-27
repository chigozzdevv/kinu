from typing import Any

import pytest
from connectors.aws import Role
from connectors.aws.browser import AgentCoreBrowser, _headers
from connectors.base.errors import ConnectorError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class BrowserApi:
    def __init__(self, recording: dict[str, Any], endpoint: str = "wss://unexpected.example"):
        self.recording, self.endpoint = recording, endpoint
        self.operations: list[str] = []

    async def call(
        self,
        service: str,
        operation: str,
        region: str,
        *,
        role: Role | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        self.operations.append(operation)
        if operation == "get_browser":
            return self.recording
        if operation == "start_browser_session":
            assert parameters["sessionTimeoutSeconds"] == 300
            return {
                "sessionId": "session_test",
                "streams": {"automationStream": {"streamEndpoint": self.endpoint}},
            }
        assert operation == "stop_browser_session"
        assert parameters["sessionId"] == "session_test"
        return {}


@pytest.mark.parametrize("recording", [{}, {"recording": {"enabled": True}}])
async def test_raw_recording_prevents_browser_start(recording: dict[str, Any]) -> None:
    aws = BrowserApi(recording)
    with pytest.raises(ConnectorError, match="recording"):
        async with AgentCoreBrowser(aws, "eu-west-2", "browser_test").session("capture"):
            pytest.fail("the browser must not become available")
    assert aws.operations == ["get_browser"]


async def test_rejected_endpoint_still_stops_the_browser_session() -> None:
    aws = BrowserApi({"recording": {"enabled": False}})
    with pytest.raises(ConnectorError, match="endpoint"):
        async with AgentCoreBrowser(aws, "eu-west-2", "browser_test").session("capture"):
            pytest.fail("an unexpected host must not receive signed credentials")
    assert aws.operations == ["get_browser", "start_browser_session", "stop_browser_session"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://bedrock-agentcore.eu-west-2.amazonaws.com/stream",
        "wss://bedrock-agentcore.us-east-1.amazonaws.com/stream",
        "wss://bedrock-agentcore.eu-west-2.amazonaws.com/stream?token=untrusted",
        "wss://bedrock-agentcore.eu-west-2.amazonaws.com/stream#fragment",
    ],
)
def test_only_the_assigned_region_stream_can_receive_signatures(endpoint: str) -> None:
    with pytest.raises(ConnectorError, match="endpoint"):
        _headers(endpoint, "eu-west-2")
