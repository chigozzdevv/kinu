import asyncio
import json
from typing import Any

from connectors.aws import AwsApi, Role
from connectors.base.errors import ConnectorError


async def invoke(
    aws: AwsApi,
    region: str,
    function: str,
    payload: dict[str, Any],
    role: Role | None = None,
) -> dict[str, Any]:
    result = await aws.call(
        "lambda",
        "invoke",
        region,
        role=role,
        FunctionName=function,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    stream = result["Payload"]
    try:
        body = await asyncio.to_thread(stream.read, 16_385)
    finally:
        stream.close()
    if result.get("FunctionError") or len(body) > 16_384:
        raise ConnectorError("demo-operation-failed", "The demo provider operation failed")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ConnectorError("demo-result-invalid", "The demo provider result is invalid")
    return value
