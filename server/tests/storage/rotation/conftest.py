from datetime import UTC, datetime

import pytest
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.storage.database import Database
from demo.config import DemoConfig
from demo.seed import seed

SECRET = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu-demo-key-ABC123"


@pytest.fixture
async def demo(database: Database, identity: AuthenticatedIdentity) -> DemoConfig:
    def clock() -> datetime:
        return datetime.now(UTC)

    member = await AccountService(AccountStore(database, clock), clock).create_organisation(
        identity, "API demo"
    )
    config = DemoConfig(
        organisation_id=member.organisation.id,
        provider_function="arn:aws:lambda:eu-west-2:123456789012:function:kinu-demo-provider",
        consumer_function="arn:aws:lambda:eu-west-2:123456789012:function:kinu-demo-consumer",
        secret=SECRET,
        region="eu-west-2",
        assessment_function="kinu-assess",
    )
    await seed(
        database,
        config,
        "a" * 24,
        SECRET + "/versions/" + "1" * 64,
        "arn:aws:iam::123456789012:role/kinu-demo-access",
        "test-external-id-" + "x" * 32,
        identity.actor_id,
        ready=True,
    )
    return config
