import json

from contracts import Connection
from core.connection.models import AwsLink
from core.connection.roles import link_role
from core.errors import ResourceConflictError

from connectors.aws import AwsApi


class AwsLinkVerifier:
    def __init__(self, aws: AwsApi) -> None:
        self._aws = aws

    async def verify(self, link: AwsLink, connection: Connection) -> None:
        role = link_role(link)
        identity = await self._aws.call("sts", "get_caller_identity", link.region, role=role)
        if identity.get("Account") != link.account_id:
            raise ResourceConflictError("connection resolved to another AWS account")
        for resource in connection.allowed_resources:
            if resource.startswith("arn:aws:secretsmanager:"):
                await self._aws.call(
                    "secretsmanager", "describe_secret", link.region, role=role, SecretId=resource
                )
            elif resource.startswith("arn:aws:lambda:"):
                await self._aws.call(
                    "lambda",
                    "get_function_configuration",
                    link.region,
                    role=role,
                    FunctionName=resource,
                )
            else:
                raise ResourceConflictError("connection resource is unsupported")


class BrokerLinkVerifier:
    def __init__(self, aws: AwsApi, region: str, function: str) -> None:
        self._aws, self._region, self._function = aws, region, function

    async def verify(self, link: AwsLink, connection: Connection) -> None:
        result = await self._aws.call(
            "lambda",
            "invoke",
            self._region,
            FunctionName=self._function,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "operation": "verify-connection",
                    "link": link.model_dump(mode="json"),
                    "connection": connection.model_dump(mode="json"),
                }
            ).encode(),
        )
        stream = result["Payload"]
        try:
            payload = json.loads(stream.read())
        finally:
            stream.close()
        if result.get("FunctionError") or payload != {"verified": True}:
            raise ResourceConflictError(
                "AWS role verification failed; check trust and resource permissions"
            )
