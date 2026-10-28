import json
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    Contract,
)
from pydantic import Field

from core.connection.models import AwsLink
from core.connection.roles import link_role
from core.connection.scope import session_policy
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records


class AwsSetup(Contract):
    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(pattern=r"^[a-z]{2}(?:-[a-z]+)+-\d$")
    display_name: str = Field(min_length=1, max_length=160)
    secret_arns: tuple[str, ...] = Field(default=(), max_length=20)
    function_arns: tuple[str, ...] = Field(default=(), max_length=20)
    kms_arns: tuple[str, ...] = Field(default=(), max_length=10)


class AwsPrepared(Contract):
    connection: Connection
    role_arn: str
    external_id: str
    principals: tuple[str, ...]
    kms_arns: tuple[str, ...]
    organisation_id: str


class LinkVerifier(Protocol):
    async def verify(self, link: AwsLink, connection: Connection) -> None: ...


class ConnectionService:
    def __init__(
        self,
        database: Database,
        verifier: LinkVerifier,
        principals: tuple[str, ...],
        clock: Callable[[], datetime],
    ) -> None:
        self._rows = Records(database)
        self._verifier, self._principals, self._clock = verifier, principals, clock

    async def prepare(self, organisation_id: str, setup: AwsSetup) -> AwsPrepared:
        from connectors.aws.refs import SecretRef

        if not setup.secret_arns and not setup.function_arns:
            raise ResourceConflictError("select at least one secret or Lambda function")
        for arn in setup.secret_arns:
            ref = SecretRef.parse(arn)
            if ref.version or ref.account != setup.account_id or ref.region != setup.region:
                raise ResourceConflictError(
                    "secret must belong to the selected AWS account and region"
                )
        prefix = f"arn:aws:lambda:{setup.region}:{setup.account_id}:function:"
        for arn in setup.function_arns:
            if (
                not arn.startswith(prefix)
                or not arn[len(prefix) :].replace("-", "").replace("_", "").isalnum()
            ):
                raise ResourceConflictError("select an unqualified Lambda function ARN")
        if not self._principals:
            raise ResourceConflictError("AWS connection services are unavailable")
        for arn in setup.kms_arns:
            prefix = f"arn:aws:kms:{setup.region}:{setup.account_id}:key/"
            if not arn.startswith(prefix) or not arn[len(prefix) :].replace("-", "").isalnum():
                raise ResourceConflictError(
                    "select exact KMS key ARNs in the same account and region"
                )
        if len(set(setup.secret_arns + setup.function_arns + setup.kms_arns)) != len(
            setup.secret_arns + setup.function_arns + setup.kms_arns
        ):
            raise ResourceConflictError("AWS selections must not contain duplicates")
        now, identity = self._clock(), f"aws_{uuid4().hex}"
        link = AwsLink(
            id=identity,
            organisation_id=organisation_id,
            account_id=setup.account_id,
            role_arn=f"arn:aws:iam::{setup.account_id}:role/kinu/{identity}",
            kms_arns=setup.kms_arns,
            external_id=secrets.token_urlsafe(32),
            region=setup.region,
            created_at=now,
        )
        link_role(link)
        roles = set[ConnectionRole]()
        capabilities = set[str]()
        if setup.secret_arns:
            roles.add(ConnectionRole.SECRET_STORE)
            capabilities.update(
                {
                    "secretStore.listSecrets",
                    "secretStore.listVersions",
                    "secretStore.getVersion",
                    "secretStore.addVersion",
                    "secretStore.disableVersion",
                    "secretStore.testConsumerAccess",
                }
            )
        if setup.function_arns:
            roles.add(ConnectionRole.RUNTIME)
            capabilities.update(
                {
                    "runtime.listServices",
                    "runtime.getDeployment",
                    "runtime.updateConsumer",
                    "runtime.health",
                    "runtime.deployCandidate",
                    "runtime.shiftTraffic",
                    "runtime.rollback",
                }
            )
        connection = Connection(
            id=identity,
            organisation_id=organisation_id,
            platform="aws",
            display_name=setup.display_name,
            roles=frozenset(roles),
            interface=ConnectionInterface.API,
            authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
            authorization_reference=f"aws-role://{identity}",
            capabilities=frozenset(capabilities),
            allowed_resources=setup.secret_arns + setup.function_arns,
            status=ConnectionStatus.SETUP_REQUIRED,
            region=setup.region,
            created_at=now,
            updated_at=now,
        )
        if len(json.dumps(session_policy(link, connection, False), separators=(",", ":"))) > 2048:
            raise ResourceConflictError(
                "select fewer resources; the AWS session policy is too large"
            )
        async with self._rows.write(organisation_id) as rows:
            await rows.add(link, organisation_id)
            await rows.add(connection, organisation_id)
        return AwsPrepared(
            connection=connection,
            role_arn=link.role_arn,
            external_id=link.external_id,
            principals=self._principals,
            kms_arns=setup.kms_arns,
            organisation_id=organisation_id,
        )

    async def confirm(self, organisation_id: str, connection_id: str, revision: int) -> Connection:
        async with self._rows.read() as rows:
            connection = await rows.get(Connection, organisation_id, connection_id)
            link = await rows.get(AwsLink, organisation_id, connection_id)
        if connection.archived_at or connection.revision != revision:
            raise ResourceConflictError("connection changed before verification")
        await self._verifier.verify(link, connection)
        now = self._clock()
        confirmed = connection.model_copy(
            update={
                "status": ConnectionStatus.READY,
                "authenticated_at": now,
                "last_validated_at": now,
                "updated_at": now,
                "revision": revision + 1,
            }
        )
        async with self._rows.write(organisation_id) as rows:
            await rows.replace(
                link.model_copy(update={"confirmed_at": now, "revision": link.revision + 1}),
                link.revision,
            )
            await rows.replace(confirmed, revision)
        return confirmed

    async def prepared(self, organisation_id: str, connection_id: str) -> AwsPrepared:
        async with self._rows.read() as rows:
            connection = await rows.get(Connection, organisation_id, connection_id)
            link = await rows.get(AwsLink, organisation_id, connection_id)
        from core.connection.scope import managed_role

        managed_role(link)
        if connection.archived_at:
            raise ResourceConflictError("connection is archived")
        return AwsPrepared(
            connection=connection,
            role_arn=link.role_arn,
            external_id=link.external_id,
            principals=self._principals,
            kms_arns=link.kms_arns,
            organisation_id=organisation_id,
        )
