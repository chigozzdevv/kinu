import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from demo.mail import MailTask

from connectors.aws import AwsApi
from core.errors import ResourceConflictError


class Queue:
    def __init__(self, aws: AwsApi, region: str, url: str, organisation_id: str) -> None:
        self.aws, self.region, self.url, self.organisation_id = aws, region, url, organisation_id

    def require_scope(self, organisation_id: str, credential_id: str) -> None:
        if organisation_id != self.organisation_id or credential_id != "credential_demo":
            raise ResourceConflictError("this worker manages the assigned demo credential")

    async def submit(self, organisation_id: str, run_id: str) -> None:
        self.require_scope(organisation_id, "credential_demo")
        await self.aws.call(
            "sqs",
            "send_message",
            self.region,
            QueueUrl=self.url,
            MessageBody=json.dumps(
                {"kind": "rotation", "organisation_id": organisation_id, "run_id": run_id}
            ),
        )

    async def submit_mail(self, task: "MailTask") -> None:
        self.require_scope(task.organisation_id, "credential_demo")
        await self.aws.call(
            "sqs",
            "send_message",
            self.region,
            QueueUrl=self.url,
            MessageBody=task.model_dump_json(),
        )
