import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest
from connectors.aws import Role
from connectors.aws.secrets import AwsSecrets
from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from contracts import (
    Connection,
)

pytestmark = pytest.mark.anyio
ARN = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu/test-Ab12Cd"
VERSION = "a" * 32


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Roles:
    async def resolve(self, connection: Connection) -> Role:
        return Role("arn:aws:iam::123456789012:role/kinu", "external", "test")


class Aws:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stages = ["AWSCURRENT"]

    async def call(
        self,
        service: str,
        operation: str,
        region: str,
        *,
        role: Role | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        self.calls.append((operation, parameters))
        if operation == "put_secret_value":
            return {"VersionId": parameters["ClientRequestToken"]}
        if operation == "get_secret_value":
            return {"SecretBinary": b"hidden"}
        if operation == "list_secret_version_ids":
            return {"Versions": [{"VersionId": VERSION, "VersionStages": self.stages}]}
        return {}


async def test_staged_write_is_idempotent_and_never_moves_current(connection: Connection) -> None:
    aws = Aws()
    store = AwsSecrets(aws, Roles())
    value = SecretValue(b"hidden")
    first = await store.add_version_for(connection, ARN, value)
    second = await store.add_version_for(connection, ARN, value)
    assert first == second
    token = hashlib.sha256(ARN.encode() + b"\0hidden").hexdigest()
    assert first["secret_reference"] == f"{ARN}/versions/{token}"
    assert aws.calls[0][1]["VersionStages"] == [f"kinu-candidate-{token[:24]}"]
    assert "hidden" not in str(first)


async def test_secret_reads_require_ready_scoped_connection(connection: Connection) -> None:
    aws = Aws()
    store = AwsSecrets(aws, Roles())
    with pytest.raises(ConnectorError, match="outside this connection"):
        await store.access_for(connection, ARN.replace("test-", "other-") + f"/versions/{VERSION}")
    with pytest.raises(ConnectorError, match="outside this connection"):
        await store.access_for(
            connection.model_copy(update={"archived_at": datetime.now(UTC)}),
            f"{ARN}/versions/{VERSION}",
        )
    with pytest.raises(ConnectorError, match="requires a workspace"):
        await store.access(f"{ARN}/versions/{VERSION}")
    assert not aws.calls


async def test_current_cannot_be_deprecated_and_deprecation_does_not_claim_destruction(
    connection: Connection,
) -> None:
    aws = Aws()
    store = AwsSecrets(aws, Roles())
    reference = f"{ARN}/versions/{VERSION}"
    with pytest.raises(ConnectorError, match="current secret"):
        await store.disable_for(connection, reference)
    aws.stages = ["AWSPREVIOUS"]
    result = await store.disable_for(connection, reference)
    assert result == {"name": reference, "state": "DEPRECATED", "destroyed": False}
    assert aws.calls[-1] == (
        "update_secret_version_stage",
        {"SecretId": ARN, "VersionStage": "AWSPREVIOUS", "RemoveFromVersionId": VERSION},
    )


async def test_promotion_rejects_changed_predecessor(connection: Connection) -> None:
    store = AwsSecrets(Aws(), Roles())
    with pytest.raises(ConnectorError, match="current secret version changed"):
        await store.promote(connection, f"{ARN}/versions/{'b' * 32}", f"{ARN}/versions/{'c' * 32}")
