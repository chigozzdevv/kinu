from core.private import PrivateClient

from agents.decision import Decision, DecisionInput


class NativeAgent:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

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
