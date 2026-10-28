import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

from connectors.aws import AwsClient

from infra.stack import outputs


async def main() -> None:
    aws, region = AwsClient(), os.environ.get("AWS_REGION", "eu-west-2")
    store = await outputs(aws, region, "kinu-store")
    artifacts = await outputs(aws, region, "kinu-artifacts")
    archive = Path(".build/server.zip").read_bytes()
    key = f"server/{hashlib.sha256(archive).hexdigest()}.zip"
    await aws.call(
        "s3",
        "put_object",
        region,
        Bucket=artifacts["Bucket"],
        Key=key,
        Body=archive,
        ServerSideEncryption="AES256",
    )
    parameters = {
        "ClusterArn": store["ClusterArn"],
        "DatabaseSecretArn": store["AppSecretArn"],
        "CodeBucket": artifacts["Bucket"],
        "CodeKey": key,
    }
    await asyncio.to_thread(
        subprocess.run,
        [
            os.environ.get("KINU_AWS_CLI", "aws"),
            "cloudformation",
            "deploy",
            "--region",
            region,
            "--stack-name",
            "kinu-executor",
            "--template-file",
            "infra/executor.yaml",
            "--capabilities",
            "CAPABILITY_NAMED_IAM",
            "--no-fail-on-empty-changeset",
            "--parameter-overrides",
            *[f"{name}={value}" for name, value in parameters.items()],
        ],
        check=True,
    )
    print("Kinu connection and rotation functions deployed.")


if __name__ == "__main__":
    asyncio.run(main())
