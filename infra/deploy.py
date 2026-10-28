import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

from connectors.aws import AwsClient
from connectors.aws.client import AwsError


async def main() -> None:
    region = os.environ.get("AWS_REGION", "eu-west-2")
    aws = AwsClient()
    outputs: dict[str, dict[str, str]] = {}
    for name in ("auth", "store", "artifacts"):
        result = await aws.call(
            "cloudformation", "describe_stacks", region, StackName=f"kinu-{name}"
        )
        outputs[name] = {
            item["OutputKey"]: item["OutputValue"] for item in result["Stacks"][0]["Outputs"]
        }
    archive = Path(".build/server.zip").read_bytes()
    identity = hashlib.sha256(archive).hexdigest()
    key = f"server/{identity}.zip"
    bucket = outputs["artifacts"]["Bucket"]
    await aws.call(
        "s3",
        "put_object",
        region,
        Bucket=bucket,
        Key=key,
        Body=archive,
        ServerSideEncryption="AES256",
    )
    parameters = {
        "ClusterArn": outputs["store"]["ClusterArn"],
        "DatabaseSecretArn": outputs["store"]["AppSecretArn"],
        "CognitoPool": outputs["auth"]["PoolId"],
        "CognitoClient": outputs["auth"]["ClientId"],
        "CodeBucket": bucket,
        "CodeKey": key,
    }
    try:
        demo = await aws.call("cloudformation", "describe_stacks", region, StackName="kinu-demo")
    except AwsError as error:
        if error.code != "ValidationError":
            raise
    else:
        values = {item["OutputKey"]: item["OutputValue"] for item in demo["Stacks"][0]["Outputs"]}
        parameters.update(
            DemoQueueArn=values["QueueArn"],
            DemoQueueUrl=values["QueueUrl"],
            DemoOrganisation=values["Organisation"],
        )
    command = [
        os.environ.get("KINU_AWS_CLI", "aws"),
        "cloudformation",
        "deploy",
        "--region",
        region,
        "--stack-name",
        "kinu-app",
        "--template-file",
        "infra/app.yaml",
        "--capabilities",
        "CAPABILITY_IAM",
        "--no-fail-on-empty-changeset",
        "--parameter-overrides",
        *[f"{name}={value}" for name, value in parameters.items()],
    ]
    result = await asyncio.to_thread(subprocess.run, command, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    deployed = await aws.call("cloudformation", "describe_stacks", region, StackName="kinu-app")
    values = {item["OutputKey"]: item["OutputValue"] for item in deployed["Stacks"][0]["Outputs"]}
    print(f"Kinu application deployed: {values['PublicUrl']}")


if __name__ == "__main__":
    asyncio.run(main())
