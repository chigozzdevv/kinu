from typing import Any

from core.private import PrivateClient

from agents.decision import Decision, DecisionInput


class NativeAgent:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

    async def navigate(self, organisation_id: str, value: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await self.client.call(
            "/_internal/navigation",
            {"organisation_id": organisation_id, "input": value},
            timeout=60,
        )
        return result

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
