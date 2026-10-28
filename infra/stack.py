from typing import Any

from connectors.aws import AwsApi
from connectors.aws.client import AwsError


async def outputs(aws: AwsApi, region: str, name: str) -> dict[str, str]:
    result = await aws.call("cloudformation", "describe_stacks", region, StackName=name)
    return {item["OutputKey"]: item["OutputValue"] for item in result["Stacks"][0]["Outputs"]}


async def existing(aws: AwsApi, region: str, name: str) -> dict[str, Any] | None:
    try:
        result = await aws.call("cloudformation", "describe_stacks", region, StackName=name)
    except AwsError as error:
        if error.code != "ValidationError":
            raise
        return None
    stack: dict[str, Any] = result["Stacks"][0]
    if stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
        raise RuntimeError(f"{name} requires attention before deployment: {stack['StackStatus']}")
    return stack
