from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from browser.proposal import Proposals
from core.errors import ResourceConflictError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field, value",
    [("organisation_id", "org_other"), ("id", "version_other"), ("playbook_id", "book_other")],
)
async def test_task_rejects_another_workspace_or_unpinned_playbook(field: str, value: str) -> None:
    sessions, images, planner, driver = (AsyncMock() for _ in range(4))
    session = SimpleNamespace(
        organisation_id="org_one", playbook_version="version_one", playbook_id="book_one"
    )
    version = SimpleNamespace(organisation_id="org_one", id="version_one", playbook_id="book_one")
    setattr(version, field, value)
    with pytest.raises(ResourceConflictError, match="pinned playbook"):
        await Proposals(sessions, images, planner).record(
            cast(Any, session), cast(Any, object()), cast(Any, version), driver
        )
    driver.validate_step.assert_not_awaited()
    images.save.assert_not_awaited()
    sessions.record_activity.assert_not_awaited()
    planner.choose.assert_not_awaited()
