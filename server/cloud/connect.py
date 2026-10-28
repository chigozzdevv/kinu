import asyncio
from typing import Any

from connectors.aws import AwsClient
from core.config import Settings
from core.connection.control import ControlRequest, control
from core.storage.factory import application_database


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return asyncio.run(execute(ControlRequest.model_validate(event)))


async def execute(request: ControlRequest) -> dict[str, Any]:
    database = application_database(Settings())
    await database.open()
    try:
        return {"result": await control(database, AwsClient(), request)}
    except Exception:
        return {"error": "connection-check-failed"}
    finally:
        await database.close()
