import asyncio
import hashlib
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from connectors.aws import AwsClient

from infra.assessment import bundle
from infra.stack import outputs


def package() -> Path:
    env = dict(os.environ, NITRO_PRESET="aws_lambda")
    subprocess.run(["npm", "--prefix", "agent", "run", "build"], env=env, check=True)
    target = Path(".build/native")
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree("agent/.output/server", target / "server")
    bundle(target / "assessment.mjs")
    archive = Path(".build/native.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(target))
    return archive


async def main() -> None:
    archive = package()
    aws, region = AwsClient(), os.environ.get("AWS_REGION", "eu-west-2")
    store = await outputs(aws, region, "kinu-store")
    app = await outputs(aws, region, "kinu-app")
    artifacts = await outputs(aws, region, "kinu-artifacts")
    blob = archive.read_bytes()
    key = f"native/{hashlib.sha256(blob).hexdigest()}.zip"
    await aws.call(
        "s3",
        "put_object",
        region,
        Bucket=artifacts["Bucket"],
        Key=key,
        Body=blob,
        ServerSideEncryption="AES256",
    )
    api_id = app["PublicUrl"].split("//")[1].split(".")[0]
    subprocess.run(
        [
            os.environ.get("KINU_AWS_CLI", "aws"),
            "cloudformation",
            "deploy",
            "--region",
            region,
            "--stack-name",
            "kinu-native",
            "--template-file",
            "infra/native.yaml",
            "--capabilities",
            "CAPABILITY_IAM",
            "--no-fail-on-empty-changeset",
            "--parameter-overrides",
            f"ApiId={api_id}",
            f"PublicUrl={app['PublicUrl']}",
            f"ClusterArn={store['ClusterArn']}",
            f"CodeBucket={artifacts['Bucket']}",
            f"CodeKey={key}",
        ],
        check=True,
    )
    print("Kinu Agent-Native service deployed")


if __name__ == "__main__":
    asyncio.run(main())
