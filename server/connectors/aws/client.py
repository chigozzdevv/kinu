import asyncio
import json
import re
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Protocol, cast

from boto3.session import Session
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from connectors.base.errors import ConnectorError


@dataclass(frozen=True, slots=True)
class Role:
    arn: str
    external_id: str
    session_name: str
    tags: tuple[tuple[str, str], ...] = ()
    policy: dict[str, Any] | None = None


class AwsApi(Protocol):
    async def call(
        self,
        service: str,
        operation: str,
        region: str,
        *,
        role: Role | None = None,
        **parameters: Any,
    ) -> dict[str, Any]: ...


class AwsClient:
    def __init__(self, session: Session | None = None) -> None:
        self._session = session or Session()

    async def call(
        self,
        service: str,
        operation: str,
        region: str,
        *,
        role: Role | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._call, service, operation, region, role, parameters)

    def _call(
        self,
        service: str,
        operation: str,
        region: str,
        role: Role | None,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._session
        try:
            if role is not None:
                with closing(session.client("sts", region_name=region, config=_CONFIG)) as sts:
                    credentials = sts.assume_role(
                        RoleArn=role.arn,
                        ExternalId=role.external_id,
                        RoleSessionName=role.session_name,
                        DurationSeconds=900,
                        **({"Policy": json.dumps(role.policy)} if role.policy else {}),
                        **(
                            {"Tags": [{"Key": key, "Value": value} for key, value in role.tags]}
                            if role.tags
                            else {}
                        ),
                    )["Credentials"]
                session = Session(
                    aws_access_key_id=credentials["AccessKeyId"],
                    aws_secret_access_key=credentials["SecretAccessKey"],
                    aws_session_token=credentials["SessionToken"],
                    region_name=region,
                )
            factory = cast(Callable[..., BaseClient], session.client)
            with closing(factory(service, region_name=region, config=_CONFIG)) as client:
                result: dict[str, Any] = getattr(client, operation)(**parameters)
                return result
        except (ClientError, BotoCoreError) as error:
            code = (
                error.response["Error"]["Code"]
                if isinstance(error, ClientError)
                else type(error).__name__
            )
            message = (
                error.response["Error"].get("Message", "") if isinstance(error, ClientError) else ""
            )
            state = re.search(r"SQLState:\s*([0-9A-Z]{5})", message, re.IGNORECASE)
            raise AwsError(service, operation, code, state[1] if state else None) from None


class AwsError(ConnectorError):
    def __init__(
        self, service: str, operation: str, code: str, sqlstate: str | None = None
    ) -> None:
        super().__init__(
            code,
            f"AWS {service} {operation} failed ({code})",
            retryable=code
            in {
                "ThrottlingException",
                "TooManyRequestsException",
                "ServiceUnavailableException",
                "DatabaseResumingException",
                "InternalServerError",
                "EndpointConnectionError",
            },
        )
        self.service, self.operation, self.sqlstate = service, operation, sqlstate


_CONFIG = Config(
    connect_timeout=5,
    read_timeout=60,
    retries={"max_attempts": 2, "mode": "standard"},
)
