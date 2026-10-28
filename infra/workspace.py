from datetime import UTC, datetime

from connectors.aws import AwsClient
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.storage.database import Database


async def demo_workspace(aws: AwsClient, region: str, database: Database, pool: str) -> str:
    email = "demo@kinu.invalid"
    user = await aws.call("cognito-idp", "admin_get_user", region, UserPoolId=pool, Username=email)
    attributes = {item["Name"]: item["Value"] for item in user["UserAttributes"]}
    if (
        attributes.get("email") != email
        or attributes.get("name") != "Kinu demo"
        or attributes.get("email_verified") != "true"
    ):
        raise ValueError("the synthetic Cognito test fixture changed")
    identity = AuthenticatedIdentity(
        subject=f"https://cognito-idp.{region}.amazonaws.com/{pool}#{attributes['sub']}",
        issuer=f"https://cognito-idp.{region}.amazonaws.com/{pool}",
        email=email,
        email_verified=True,
        display_name="Kinu demo",
        connected_via="Cognito",
    )

    def clock() -> datetime:
        return datetime.now(UTC)

    accounts = AccountStore(database, clock)
    memberships = await accounts.session(identity)
    for member in memberships:
        if member.organisation.name == "Kinu demo":
            return member.organisation.id
    created = await AccountService(accounts, clock).create_organisation(identity, "Kinu demo")
    return created.organisation.id
