import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from connectors.aws import AwsClient
from contracts import IncidentStatus, RunStatus
from core.config import Settings
from core.incident.store import IncidentStore
from core.storage.factory import application_database
from rotation.effects import Effects
from rotation.queue import RotationTask
from rotation.rotate import Rotation
from rotation.snapshot import loader


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return asyncio.run(consume(event, "worker_" + context.aws_request_id.replace("-", "")))


async def consume(event: dict[str, Any], owner: str) -> dict[str, Any]:
    records = event.get("Records")
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("executor requires one queued rotation")
    message = records[0]
    if (
        message.get("eventSource") != "aws:sqs"
        or message.get("eventSourceARN") != os.environ["KINU_ROTATION_QUEUE_ARN"]
    ):
        raise ValueError("executor requires its assigned queue")
    task = RotationTask.model_validate_json(message["body"])
    config = Settings()
    database = application_database(config)
    await database.open()
    try:
        run = await Rotation(
            database, Effects(AwsClient(), database), loader(database), config.aws_region
        ).execute(task.organisation_id, task.run_id, owner)
        if run.status is RunStatus.COMPLETED:
            await IncidentStore(database).advance_run(
                task.organisation_id, run.id, IncidentStatus.RESOLVED, datetime.now(UTC)
            )
        return {"batchItemFailures": []}
    finally:
        await database.close()
