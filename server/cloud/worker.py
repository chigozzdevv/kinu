import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from connectors.aws import AwsClient
from connectors.aws.verify import AwsLinkVerifier
from contracts import Connection, Contract, Identifier, IncidentStatus, RunStatus
from core.config import Settings
from core.connection.models import AwsLink
from core.incident.store import IncidentStore
from core.storage.factory import application_database
from core.storage.records import Records
from demo.config import DemoConfig
from demo.effects import Effects
from demo.mail import Mailbox, MailTask, investigate
from demo.snapshot import loader
from pydantic import Field, TypeAdapter
from rotation.rotate import Rotation


class RotationTask(Contract):
    kind: Literal["rotation"]
    organisation_id: Identifier
    run_id: Identifier


_TASK: TypeAdapter[RotationTask | MailTask] = TypeAdapter(
    Annotated[RotationTask | MailTask, Field(discriminator="kind")]
)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return asyncio.run(consume(event, "worker_" + context.aws_request_id.replace("-", "")))


async def consume(event: dict[str, Any], owner: str) -> dict[str, Any]:
    if event.get("operation") == "verify-connection":
        config = DemoConfig.from_env()
        if event != {"operation": "verify-connection", "organisation_id": config.organisation_id}:
            raise ValueError("demo verification requires the assigned workspace")
        database = application_database(Settings())
        async with Records(database).read() as rows:
            link = await rows.get(AwsLink, config.organisation_id, "aws_demo")
            connections = [
                await rows.get(Connection, config.organisation_id, f"connection_demo_{name}")
                for name in ("provider", "store", "runtime")
            ]
        verifier = AwsLinkVerifier(AwsClient())
        for connection in connections:
            await verifier.verify(link, connection)
        return {"verified": True}
    records = event.get("Records")
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("the rotation worker accepts one queued job at a time")
    config, database = DemoConfig.from_env(), application_database(Settings())
    await database.open()
    try:
        message = records[0]
        if message.get("eventSource") != "aws:sqs":
            raise ValueError("the worker requires an SQS job envelope")
        task = _TASK.validate_json(message["body"])
        if task.organisation_id != config.organisation_id:
            raise ValueError("queued job belongs to another workspace")
        aws = AwsClient()
        run_id = (
            task.run_id
            if isinstance(task, RotationTask)
            else await investigate(database, aws, config, task)
        )
        if run_id is None:
            return {"batchItemFailures": []}
        run = await Rotation(
            database, Effects(aws, database, config), loader(database, config), config.region
        ).execute(task.organisation_id, run_id, owner)
        if run.status is RunStatus.COMPLETED:
            await IncidentStore(database).advance_run(
                task.organisation_id, run.id, IncidentStatus.RESOLVED, datetime.now(UTC)
            )
            if isinstance(task, MailTask):
                await Mailbox(database).complete(task)
        print(json.dumps({"run_id": run.id, "stage": run.stage.value, "status": run.status.value}))
        return {"batchItemFailures": []}
    finally:
        await database.close()
