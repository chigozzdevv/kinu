from datetime import UTC, datetime

import pytest
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
)

ARN = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu/test-Ab12Cd"


@pytest.fixture
def connection() -> Connection:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    return Connection(
        id="aws_one",
        organisation_id="org_one",
        platform="aws",
        display_name="AWS",
        roles=frozenset({ConnectionRole.SECRET_STORE}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
        authorization_reference="aws-role://aws_one",
        capabilities=frozenset({"secretStore.getVersion"}),
        allowed_resources=(ARN,),
        status=ConnectionStatus.READY,
        region="eu-west-2",
        created_at=now,
        updated_at=now,
    )
