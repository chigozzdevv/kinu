import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from connectors.aws import AwsClient
from core.storage.dataapi import DataApi
from core.storage.database import Database


async def outputs(aws: AwsClient, region: str, name: str) -> dict[str, str]:
    result = await aws.call("cloudformation", "describe_stacks", region, StackName=name)
    return {item["OutputKey"]: item["OutputValue"] for item in result["Stacks"][0]["Outputs"]}


async def bootstrap(
    aws: AwsClient, region: str, store: dict[str, str], native: dict[str, str]
) -> None:
    secret = await aws.call(
        "secretsmanager", "get_secret_value", region, SecretId=native["DatabaseSecretArn"]
    )
    password = json.loads(secret["SecretString"])["password"]
    admin = Database(
        data_api=DataApi(
            aws, region, store["ClusterArn"], store["AdminSecretArn"], store["Database"]
        )
    )
    async with admin.connection() as sql:
        await sql.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='kinu_native') "
            "THEN CREATE ROLE kinu_native LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE; "
            "END IF; END $$"
        )
        async with sql.transaction():
            await sql.execute("SELECT set_config('kinu.new_password', %s, true)", (password,))
            await sql.execute(
                "DO $$ BEGIN EXECUTE format('ALTER ROLE kinu_native PASSWORD %L', "
                "current_setting('kinu.new_password')); END $$"
            )
        existing = await (
            await sql.execute("SELECT datname FROM pg_database WHERE datname='kinu_native'")
        ).fetchone()
        if not existing:
            await sql.execute("CREATE DATABASE kinu_native")
        await sql.execute("GRANT CONNECT ON DATABASE kinu TO kinu_app")
        await sql.execute("GRANT CONNECT ON DATABASE kinu_native TO kinu_native")
        await sql.execute("REVOKE CONNECT ON DATABASE kinu FROM PUBLIC")
        await sql.execute("REVOKE CONNECT ON DATABASE kinu_native FROM PUBLIC")
    password = ""
    native_admin = Database(
        data_api=DataApi(aws, region, store["ClusterArn"], store["AdminSecretArn"], "kinu_native")
    )
    import sqlparse

    async with native_admin.connection() as sql:
        for statement in sqlparse.split(Path("infra/native.sql").read_text()):
            await sql.execute(statement)
        await sql.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        await sql.execute("GRANT USAGE ON SCHEMA public TO kinu_native")
        await sql.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO kinu_native"
        )
    print("Native credential database provisioned separately from application data")


async def main() -> None:
    env = dict(os.environ, NITRO_PRESET="aws_lambda")
    subprocess.run(["npm", "--prefix", "agent", "run", "build"], env=env, check=True)
    target = Path(".build/native")
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree("agent/.output/server", target / "server")
    package = json.loads(Path("agent/node_modules/@agent-native/core/package.json").read_text())
    optional = [
        f"--external:{name}"
        for name, meta in package["peerDependenciesMeta"].items()
        if meta.get("optional") and not (Path("agent/node_modules") / name).exists()
    ]
    subprocess.run(
        [
            "agent/node_modules/.bin/esbuild",
            "agent/cloud/assessment.ts",
            "--bundle",
            "--platform=node",
            "--target=node22",
            "--format=esm",
            "--outfile=.build/native/assessment.mjs",
            *optional,
            '--banner:js=import { createRequire } from "node:module"; '
            "const require = createRequire(import.meta.url);",
        ],
        check=True,
    )
    archive = Path(".build/native.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(target))
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
    native = await outputs(aws, region, "kinu-native")
    await bootstrap(aws, region, store, native)
    output = Path("/private/tmp/kinu-aws/native.json")
    output.write_text(json.dumps(native))
    output.chmod(0o600)
    print("Kinu Agent-Native service deployed")


if __name__ == "__main__":
    asyncio.run(main())
