import asyncio
from typing import Any

from connectors.aws import AwsClient
from demo.admin import administer
from demo.app import create_app
from mangum import Mangum

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_http = Mangum(create_app(), lifespan="off")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    asyncio.set_event_loop(_loop)
    if event.get("version") == "2.0" and "requestContext" in event:
        return _http(event, context)
    return _loop.run_until_complete(administer(AwsClient(), event))
