from collections.abc import Awaitable, Callable
from typing import Any

from core.errors import ResourceConflictError
from core.private import PrivateClient

from agents.decision import Decision, DecisionInput


class NativeAgent:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

    async def navigate(
        self,
        organisation_id: str,
        value: dict[str, Any],
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        previous = ""
        async for event in self.client.stream(
            "/_internal/navigation", {"organisation_id": organisation_id, "input": value}
        ):
            if event.get("type") == "summary":
                content = event.get("content")
                if (
                    not isinstance(content, str)
                    or len(content) > 4096
                    or not content.startswith(previous)
                ):
                    raise ResourceConflictError("Browser explanation changed while streaming")
                if progress:
                    await progress(content)
                previous = content
            elif event.get("type") == "result" and isinstance(event.get("result"), dict):
                result: dict[str, Any] = event["result"]
                if previous and result.get("summary") != previous:
                    raise ResourceConflictError("Browser explanation did not match its result")
                return result
            else:
                raise ResourceConflictError(
                    "Browser inference failed; check the Builder connection"
                )
        raise ResourceConflictError("Browser inference was incomplete")

    async def assess(self, organisation_id: str, value: DecisionInput) -> Decision:
        return Decision.model_validate(
            await self.client.call(
                "/_internal/assessment",
                {
                    "organisation_id": organisation_id,
                    "input": value.model_dump(mode="json"),
                },
            )
        )
