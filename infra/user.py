import asyncio
import json
import os
import secrets
from pathlib import Path

from connectors.aws import AwsClient
from connectors.aws.client import AwsError


async def main() -> None:
    aws, region = AwsClient(), os.environ.get("AWS_REGION", "eu-west-2")
    stack = await aws.call("cloudformation", "describe_stacks", region, StackName="kinu-auth")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Stacks"][0]["Outputs"]}
    target = Path("/private/tmp/kinu-aws/demo-user.json")
    email = "demo@kinu.invalid"
    saved = json.loads(target.read_text()) if target.exists() else None
    if saved and saved.get("ready"):
        print("Kinu synthetic sign-in account already exists")
        return
    password = saved["password"] if saved else "Aa9!" + secrets.token_urlsafe(36)
    if saved is None:
        with open(target, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            json.dump({"email": email, "password": password, "ready": False}, output)
    try:
        await aws.call(
            "cognito-idp",
            "admin_create_user",
            region,
            UserPoolId=outputs["PoolId"],
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": "Kinu demo"},
            ],
        )
    except AwsError as error:
        if error.code != "UsernameExistsException":
            raise
        existing = await aws.call(
            "cognito-idp", "admin_get_user", region, UserPoolId=outputs["PoolId"], Username=email
        )
        attributes = {item["Name"]: item["Value"] for item in existing["UserAttributes"]}
        if attributes.get("name") != "Kinu demo" or attributes.get("email") != email:
            raise RuntimeError("existing account is not Kinu's synthetic sign-in fixture") from None
    await aws.call(
        "cognito-idp",
        "admin_set_user_password",
        region,
        UserPoolId=outputs["PoolId"],
        Username=email,
        Password=password,
        Permanent=True,
    )
    user = await aws.call(
        "cognito-idp", "admin_get_user", region, UserPoolId=outputs["PoolId"], Username=email
    )
    subject = next(item["Value"] for item in user["UserAttributes"] if item["Name"] == "sub")
    with target.open("w") as output:
        json.dump({"email": email, "password": password, "sub": subject, "ready": True}, output)
    print("Kinu synthetic sign-in account created; no email was sent")


if __name__ == "__main__":
    asyncio.run(main())
