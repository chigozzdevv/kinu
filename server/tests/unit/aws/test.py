from datetime import UTC, datetime
from typing import Any

import pytest
from boto3.session import Session
from botocore.stub import Stubber
from connectors.aws import AwsClient, Role
from connectors.aws.client import AwsError
from connectors.aws.refs import SecretRef
from core.errors import ResourceConflictError
from core.storage.dataapi import DataApi
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeAws:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result: dict[str, Any] = {}
        self.failure: AwsError | None = None

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
        if self.failure and operation == "execute_statement":
            raise self.failure
        return {"transactionId": "transaction_one"} | self.result


def data_api(aws: FakeAws) -> DataApi:
    return DataApi(aws, "eu-west-2", "cluster_arn", "database_secret_arn", "kinu")


async def test_data_api_binds_values_and_preserves_database_types() -> None:
    aws = FakeAws()
    aws.result = {
        "columnMetadata": [
            {"name": "identity", "typeName": "jsonb"},
            {"name": "expires_at", "typeName": "timestamptz"},
            {"name": "active", "typeName": "bool"},
        ],
        "records": [
            [
                {"stringValue": '{"subject":"user_one"}'},
                {"stringValue": "2026-10-27 12:00:00+00"},
                {"booleanValue": True},
            ]
        ],
    }
    attacker = "'); DROP SCHEMA kinu CASCADE;--"
    cursor = (
        await data_api(aws)
        .connection()
        .execute(
            "SELECT identity, expires_at, active FROM sessions WHERE token = %s AND data = %s "
            "AND expires_at > %s AND active = %s",
            (attacker, Jsonb({"subject": "user_one"}), datetime(2026, 10, 27, tzinfo=UTC), True),
        )
    )
    request = aws.calls[0][1]
    assert attacker not in request["sql"]
    assert request["parameters"][0]["value"]["stringValue"] == attacker
    assert "CAST(:p1 AS jsonb)" in request["sql"]
    assert "CAST(:p2 AS timestamptz)" in request["sql"]
    row = await cursor.fetchone()
    assert row == {
        "identity": {"subject": "user_one"},
        "expires_at": datetime(2026, 10, 27, 12, tzinfo=UTC),
        "active": True,
    }
    assert await cursor.fetchone() is None


async def test_data_api_commits_and_rolls_back_with_the_transaction_id() -> None:
    aws = FakeAws()
    connection = data_api(aws).connection()
    async with connection.transaction():
        await connection.execute("UPDATE inventory SET revision = %s", (1,))
    assert [name for name, _ in aws.calls] == [
        "begin_transaction",
        "execute_statement",
        "commit_transaction",
    ]
    assert aws.calls[1][1]["transactionId"] == "transaction_one"
    assert "database" not in aws.calls[2][1]
    aws.calls.clear()
    with pytest.raises(ValueError, match="abort"):
        async with connection.transaction():
            raise ValueError("abort")
    assert [name for name, _ in aws.calls] == ["begin_transaction", "rollback_transaction"]
    await connection.execute("SELECT 1")
    assert "transactionId" not in aws.calls[-1][1]


async def test_data_api_preserves_conflict_types_without_database_error_details() -> None:
    aws = FakeAws()
    aws.failure = AwsError("rds-data", "execute_statement", "DatabaseErrorException", "23505")
    with pytest.raises(UniqueViolation, match="application storage query failed"):
        await data_api(aws).connection().execute("INSERT INTO inventory VALUES (%s)", ("value",))


async def test_aws_errors_do_not_include_provider_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="eu-west-2"
    )
    client = session.client("secretsmanager")
    monkeypatch.setattr(session, "client", lambda *_args, **_kwargs: client)
    with Stubber(client) as stub:
        stub.add_client_error(
            "get_secret_value", "AccessDeniedException", "secret value: never-print-this"
        )
        with pytest.raises(AwsError) as failure:
            await AwsClient(session).call(
                "secretsmanager", "get_secret_value", "eu-west-2", SecretId="test"
            )
    assert "never-print-this" not in str(failure.value)
    assert failure.value.code == "AccessDeniedException"


@pytest.mark.parametrize(
    "value",
    [
        "kinu/key",
        "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu/key",
        "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu/key-ABC123/versions/AWSCURRENT",
    ],
)
async def test_secret_references_require_exact_resource_and_immutable_version(value: str) -> None:
    with pytest.raises(ResourceConflictError):
        SecretRef.parse(value, version=True)


async def test_secret_reference_round_trip() -> None:
    arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu/key-ABC123"
    version = "12345678-1234-1234-1234-123456789012"
    value = SecretRef.parse(f"{arn}/versions/{version}", version=True)
    assert value.region == "eu-west-2"
    assert value.account == "123456789012"
    assert value.reference(version) == f"{arn}/versions/{version}"
