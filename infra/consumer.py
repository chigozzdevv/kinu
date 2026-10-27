import asyncio

from connectors.aws import AwsClient
from connectors.aws.client import AwsError


async def initialize(aws: AwsClient, region: str, function: str, version: str) -> None:
    try:
        alias = await aws.call("lambda", "get_alias", region, FunctionName=function, Name="live")
    except AwsError as error:
        if error.code != "ResourceNotFoundException":
            raise
    else:
        current = await aws.call(
            "lambda",
            "get_function_configuration",
            region,
            FunctionName=function,
            Qualifier=alias["FunctionVersion"],
        )
        if (
            current.get("Environment", {}).get("Variables", {}).get("KINU_SECRET_VERSION")
            != version
        ):
            raise RuntimeError("existing live consumer differs from the initial setup")
        return
    config = await aws.call("lambda", "get_function_configuration", region, FunctionName=function)
    environment = config["Environment"]["Variables"] | {"KINU_SECRET_VERSION": version}
    await aws.call(
        "lambda",
        "update_function_configuration",
        region,
        FunctionName=function,
        RevisionId=config["RevisionId"],
        Environment={"Variables": environment},
    )
    for _ in range(30):
        ready = await aws.call(
            "lambda", "get_function_configuration", region, FunctionName=function
        )
        if ready.get("LastUpdateStatus") == "Successful":
            break
        if ready.get("LastUpdateStatus") == "Failed":
            raise RuntimeError("initial consumer configuration failed")
        await asyncio.sleep(1)
    else:
        raise RuntimeError("initial consumer update did not finish")
    published = await aws.call(
        "lambda",
        "publish_version",
        region,
        FunctionName=function,
        RevisionId=ready["RevisionId"],
        CodeSha256=ready["CodeSha256"],
        Description="kinu-initial",
    )
    await aws.call(
        "lambda",
        "create_alias",
        region,
        FunctionName=function,
        Name="live",
        FunctionVersion=published["Version"],
    )
