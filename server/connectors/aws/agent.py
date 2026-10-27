import asyncio
import json

from agents.decision import Decision, DecisionInput

from connectors.aws import AwsApi
from connectors.base.errors import ConnectorError


class NativeAgent:
    def __init__(self, aws: AwsApi, region: str, function: str) -> None:
        self._aws, self._region, self._function = aws, region, function

    async def assess(self, organisation_id: str, value: DecisionInput) -> Decision:
        result = await self._aws.call(
            "lambda",
            "invoke",
            self._region,
            FunctionName=self._function,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {"organisation_id": organisation_id, "input": value.model_dump(mode="json")}
            ).encode(),
        )
        stream = result["Payload"]
        try:
            body = await asyncio.to_thread(stream.read, 4097)
        finally:
            stream.close()
        if result.get("FunctionError") or len(body) > 4096:
            raise ConnectorError("agent-unavailable", "Agent-Native assessment failed")
        return Decision.model_validate(json.loads(body))
