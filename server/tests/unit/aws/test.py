import json
from datetime import UTC, datetime

import pytest
from boto3.session import Session
from botocore.stub import Stubber
from connectors.aws import AwsClient, Role
from connectors.aws.client import AwsError
from connectors.aws.refs import SecretRef
from core.errors import ResourceConflictError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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


async def test_assumed_role_passes_the_selected_session_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="eu-west-2"
    )
    sts, vault = session.client("sts"), session.client("secretsmanager")
    monkeypatch.setattr(
        session, "client", lambda service, **kwargs: sts if service == "sts" else vault
    )
    monkeypatch.setattr("connectors.aws.client.Session", lambda **kwargs: session)
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "secretsmanager:DescribeSecret",
                "Resource": "arn:aws:secretsmanager:eu-west-2:123456789012:secret:selected-Ab12Cd",
            }
        ],
    }
    role = Role(
        "arn:aws:iam::123456789012:role/kinu/aws_one",
        "external-workspace",
        "kinu-test",
        (("kinu-org", "org_one"),),
        policy,
    )
    with Stubber(sts) as assumed, Stubber(vault) as resources:
        assumed.add_response(
            "assume_role",
            {
                "Credentials": {
                    "AccessKeyId": "A" * 20,
                    "SecretAccessKey": "B" * 40,
                    "SessionToken": "C" * 32,
                    "Expiration": datetime.now(UTC),
                }
            },
            {
                "RoleArn": role.arn,
                "ExternalId": role.external_id,
                "RoleSessionName": role.session_name,
                "DurationSeconds": 900,
                "Tags": [{"Key": "kinu-org", "Value": "org_one"}],
                "Policy": json.dumps(policy, separators=(",", ":")),
            },
        )
        resources.add_response("describe_secret", {"Name": "selected"}, {"SecretId": "selected"})
        result = await AwsClient(session).call(
            "secretsmanager", "describe_secret", "eu-west-2", role=role, SecretId="selected"
        )
        assert result["Name"] == "selected"
        assumed.assert_no_pending_responses()
