from datetime import UTC, datetime

import pytest
from contracts import Connection, ConnectionStatus
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.connection.models import AwsLink
from core.connection.roles import ConnectionRoles
from core.connection.service import AwsSetup, ConnectionService
from core.connection.template import role_template
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 27, tzinfo=UTC)
ARN = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu/test-Ab12Cd"


class Verifier:
    fail = True

    async def verify(self, link: AwsLink, connection: Connection) -> None:
        if self.fail:
            raise ResourceConflictError("role trust is missing")


async def test_role_is_unusable_until_verified_and_cannot_cross_workspace(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workspace = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "AWS connection")
    org = workspace.organisation.id
    verifier = Verifier()
    broker = "arn:aws:iam::123456789012:role/kinu-broker"
    service = ConnectionService(database, verifier, (broker,), lambda: NOW)
    prepared = await service.prepare(
        org,
        AwsSetup(
            account_id="123456789012",
            region="eu-west-2",
            display_name="AWS vault",
            secret_arns=(ARN,),
        ),
    )
    assert prepared.connection.status is ConnectionStatus.SETUP_REQUIRED
    roles = ConnectionRoles(database)
    with pytest.raises(ResourceConflictError):
        await roles.resolve(prepared.connection)
    with pytest.raises(ResourceConflictError, match="trust"):
        await service.confirm(org, prepared.connection.id, 0)
    with pytest.raises(ResourceNotFoundError):
        await service.confirm("org_other", prepared.connection.id, 0)
    verifier.fail = False
    confirmed = await service.confirm(org, prepared.connection.id, 0)
    role = await roles.resolve(confirmed)
    assert role.tags == (("kinu-org", org),)
    assert role.external_id == prepared.external_id
    with pytest.raises(ResourceConflictError):
        await roles.resolve(confirmed.model_copy(update={"allowed_resources": (ARN + "other",)}))
    template = role_template(prepared)
    properties = template["Resources"]["ConnectionRole"]["Properties"]
    assert properties["RoleName"] == prepared.connection.id
    assert properties["Path"] == "/kinu/"
    statement = properties["AssumeRolePolicyDocument"]["Statement"][0]
    assert statement["Principal"]["AWS"] == [broker]
    assert statement["Condition"]["StringEquals"]["sts:ExternalId"] == prepared.external_id
    tags = properties["AssumeRolePolicyDocument"]["Statement"][1]
    assert tags["Action"] == "sts:TagSession"
    assert tags["Condition"]["StringEquals"] == {"aws:RequestTag/kinu-org": org}
    assert properties["Policies"][0]["PolicyDocument"]["Statement"][0]["Resource"] == [ARN]
    assert await service.prepared(org, confirmed.id) == prepared.model_copy(
        update={"connection": confirmed}
    )
    assert role.policy and role.policy["Statement"][0]["Resource"] == [ARN]
    readonly = await ConnectionRoles(database, managed=True, read_only=True).resolve(confirmed)
    assert readonly.policy
    assert "secretsmanager:PutSecretValue" not in readonly.policy["Statement"][0]["Action"]
