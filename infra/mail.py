import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

from connectors.aws import AwsClient

from infra.stack import existing, outputs


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--credentials", type=Path, help="Configure a downloaded Google web OAuth client"
    )
    args = parser.parse_args()
    aws, region = AwsClient(), os.environ.get("AWS_REGION", "eu-west-2")
    if args.credentials:
        stack = await outputs(aws, region, "kinu-mail")
        client = json.loads(args.credentials.read_text())["web"]
        if stack["Callback"] not in client.get("redirect_uris", []):
            raise RuntimeError("Google client does not include Kinu's callback URL")
        saved = await aws.call(
            "secretsmanager", "get_secret_value", region, SecretId=stack["ConfigArn"]
        )
        config = json.loads(saved["SecretString"])
        config.update(client_id=client["client_id"], client_secret=client["client_secret"])
        await aws.call(
            "secretsmanager",
            "put_secret_value",
            region,
            SecretId=stack["ConfigArn"],
            SecretString=json.dumps(config),
        )
        print("Gmail OAuth client configured in the private AWS vault")
        return
    store, application, native, executor, artifacts = [
        await outputs(aws, region, "kinu-" + name)
        for name in ("store", "app", "native", "executor", "artifacts")
    ]
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
        "PublicUrl": application["PublicUrl"],
        "AssessArn": native["AssessArn"],
        "RotationQueueArn": executor["QueueArn"],
        "RotationQueueUrl": executor["QueueUrl"],
        "CodeBucket": artifacts["Bucket"],
        "CodeKey": key,
    }
    demo = await existing(aws, region, "kinu-demo")
    if demo:
        values = {item["OutputKey"]: item["OutputValue"] for item in demo["Outputs"]}
        parameters.update(
            DemoQueueArn=values["QueueArn"],
            DemoQueueUrl=values["QueueUrl"],
            DemoOrganisation=values["Organisation"],
        )
    await asyncio.to_thread(
        subprocess.run,
        [
            os.environ.get("KINU_AWS_CLI", "aws"),
            "cloudformation",
            "deploy",
            "--region",
            region,
            "--stack-name",
            "kinu-mail",
            "--template-file",
            "infra/mail.yaml",
            "--capabilities",
            "CAPABILITY_IAM",
            "--no-fail-on-empty-changeset",
            "--parameter-overrides",
            *[f"{name}={value}" for name, value in parameters.items()],
        ],
        check=True,
    )
    print("Private Gmail reader deployed")


if __name__ == "__main__":
    asyncio.run(main())
