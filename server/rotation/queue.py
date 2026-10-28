from typing import Literal

from connectors.aws import AwsApi
from contracts import Contract, Identifier, ManagedCredential
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records
from core.storage.runs import RunStore
from demo.queue import Queue

from rotation.snapshot import snapshot


class RotationTask(Contract):
    kind: Literal["rotation"] = "rotation"
    organisation_id: Identifier
    run_id: Identifier


class Dispatcher:
    def __init__(
        self,
        database: Database,
        aws: AwsApi,
        region: str,
        url: str | None,
        demo: Queue | None = None,
    ) -> None:
        self.database, self.aws, self.region, self.url, self.demo = database, aws, region, url, demo

    async def require_scope(self, organisation_id: str, credential_id: str) -> None:
        if (
            self.demo
            and organisation_id == self.demo.organisation_id
            and credential_id == "credential_demo"
        ):
            self.demo.require_scope(organisation_id, credential_id)
            return
        if not self.url:
            raise ResourceConflictError("API rotation is not configured for this deployment")
        async with Records(self.database).read() as rows:
            credential = await rows.get(ManagedCredential, organisation_id, credential_id)
        await snapshot(self.database, organisation_id, credential_id, credential.control_version)

    async def submit(self, organisation_id: str, run_id: str) -> None:
        run = await RunStore(self.database).get(organisation_id, run_id)
        await self.require_scope(organisation_id, run.credential_id)
        if (
            self.demo
            and organisation_id == self.demo.organisation_id
            and run.credential_id == "credential_demo"
        ):
            await self.demo.submit(organisation_id, run_id)
            return
        await self.aws.call(
            "sqs",
            "send_message",
            self.region,
            QueueUrl=self.url,
            MessageBody=RotationTask(
                organisation_id=organisation_id, run_id=run_id
            ).model_dump_json(),
        )
