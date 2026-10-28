from contracts import Connection
from core.connection.models import AwsLink
from core.connection.roles import link_role
from core.errors import ResourceConflictError

from connectors.aws import AwsApi


class AwsLinkVerifier:
    def __init__(self, aws: AwsApi) -> None:
        self._aws = aws

    async def verify(self, link: AwsLink, connection: Connection) -> None:
        role = link_role(link, connection, read_only=True)
        identity = await self._aws.call("sts", "get_caller_identity", link.region, role=role)
        if identity.get("Account") != link.account_id:
            raise ResourceConflictError("connection resolved to another AWS account")
        for resource in connection.allowed_resources:
            if resource.startswith("arn:aws:secretsmanager:"):
                result = await self._aws.call(
                    "secretsmanager", "describe_secret", link.region, role=role, SecretId=resource
                )
                if result.get("ARN") != resource:
                    raise ResourceConflictError("AWS returned another selected secret")
            elif resource.startswith("arn:aws:lambda:"):
                result = await self._aws.call(
                    "lambda",
                    "get_function_configuration",
                    link.region,
                    role=role,
                    FunctionName=resource,
                )
                if result.get("FunctionArn") != resource:
                    raise ResourceConflictError("AWS returned another selected function")
            else:
                raise ResourceConflictError("connection resource is unsupported")
