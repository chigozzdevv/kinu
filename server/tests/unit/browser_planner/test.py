from unittest.mock import AsyncMock

import pytest
from browser.planner import Planner
from connectors.base.errors import ConnectorError
from core.errors import ResourceConflictError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_visual_planner_sends_image_and_rejects_stop_or_unlisted_control() -> None:
    agent = AsyncMock()
    image = b"\x89PNG\r\n\x1a\nredacted"
    controls = {"control_1": "button: Add", "control_2": "button: Cancel"}
    agent.navigate.return_value = {"control": "control_1", "summary": "I will select Add."}
    choice = await Planner(agent).choose("org_one", "Create the key", controls, image)
    assert choice.control == "control_1"
    request = agent.navigate.await_args.args[1]
    assert request["image"] == "iVBORw0KGgpyZWRhY3RlZA=="
    assert request["schema"]["properties"]["control"]["enum"] == [*controls, "stop"]
    for control in ("stop", "unlisted"):
        agent.navigate.return_value = {"control": control, "summary": "I cannot choose."}
        with pytest.raises(ConnectorError):
            await Planner(agent).choose("org_one", "Create the key", controls, image)
    with pytest.raises(ResourceConflictError):
        await Planner(agent).choose("org_one", "Create the key", controls, b"raw data")
