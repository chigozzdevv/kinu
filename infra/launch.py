import asyncio
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from bootstrap import main as bootstrap
from connectors.aws import AwsClient
from consumer import initialize
from contracts import ManagedCredential
from core.errors import ResourceNotFoundError
from core.storage.dataapi import DataApi
from core.storage.database import Database
from core.storage.records import Records
from demo.config import DemoConfig
from demo.rpc import invoke
from demo.seed import activate, seed
from native import outputs
from workspace import demo_workspace


async def main() -> None:
    region, aws = os.environ.get("AWS_REGION", "eu-west-2"), AwsClient()
    await bootstrap()
    store, app = await outputs(aws, region, "kinu-store"), await outputs(aws, region, "kinu-app")
    native, auth = (
        await outputs(aws, region, "kinu-native"),
        await outputs(aws, region, "kinu-auth"),
    )
    artifacts = await outputs(aws, region, "kinu-artifacts")
    database = Database(
        data_api=DataApi(aws, region, store["ClusterArn"], store["AppSecretArn"], store["Database"])
    )
    target = Path("/private/tmp/kinu-aws/demo.json")
    previous = json.loads(target.read_text()) if target.exists() else {}
    organisation = previous.get("Organisation") or os.environ.get("KINU_DEMO_ORGANISATION")
    if not organisation:
        async with database.connection() as sql:
            members = await (
                await sql.execute(
                    "SELECT DISTINCT organisation_id FROM kinu.members WHERE subject LIKE %s",
                    (f"https://cognito-idp.{region}.amazonaws.com/{auth['PoolId']}#%",),
                )
            ).fetchall()
        organisation = (
            members[0]["organisation_id"]
            if len(members) == 1
            else await demo_workspace(aws, region, database, auth["PoolId"])
        )
    external = previous.get("ExternalId") or secrets.token_urlsafe(32)
    blob = Path(".build/server.zip").read_bytes()
    key = f"server/{hashlib.sha256(blob).hexdigest()}.zip"
    await aws.call(
        "s3",
        "put_object",
        region,
        Bucket=artifacts["Bucket"],
        Key=key,
        Body=blob,
        ServerSideEncryption="AES256",
    )
    parameters = {
        "Organisation": organisation,
        "ExternalId": external,
        "PublicUrl": app["PublicUrl"],
        "ClusterArn": store["ClusterArn"],
        "DatabaseSecretArn": store["AppSecretArn"],
        "AssessArn": native["AssessArn"],
        "CodeBucket": artifacts["Bucket"],
        "CodeKey": key,
    }
    subprocess.run(
        [
            os.environ.get("KINU_AWS_CLI", "aws"),
            "cloudformation",
            "deploy",
            "--region",
            region,
            "--stack-name",
            "kinu-demo",
            "--template-file",
            "infra/demo.yaml",
            "--capabilities",
            "CAPABILITY_NAMED_IAM",
            "--no-fail-on-empty-changeset",
            "--parameter-overrides",
            *[f"{name}={value}" for name, value in parameters.items()],
        ],
        check=True,
    )
    values = await outputs(aws, region, "kinu-demo")
    target.write_text(json.dumps(values | {"ExternalId": external}))
    target.chmod(0o600)
    config = DemoConfig(
        organisation_id=organisation,
        provider_function=values["ProviderArn"],
        consumer_function=values["ConsumerArn"],
        secret=values["KeyArn"],
        region=region,
        assessment_function=native["AssessArn"],
    )
    async with Records(database).read() as rows:
        try:
            credential = await rows.get(ManagedCredential, organisation, config.credential_id)
        except ResourceNotFoundError:
            credential = None
    if credential is None:
        created = await invoke(
            aws,
            region,
            config.provider_function,
            {"operation": "create", "organisation_id": organisation, "request_id": "demo_initial"},
        )
        reference = created["secret_reference"]
        version = reference.rpartition("/")[2]
        secret = await aws.call("secretsmanager", "describe_secret", region, SecretId=config.secret)
        current = next(
            (
                identity
                for identity, stages in secret.get("VersionIdsToStages", {}).items()
                if "AWSCURRENT" in stages
            ),
            None,
        )
        if current != version:
            await aws.call(
                "secretsmanager",
                "update_secret_version_stage",
                region,
                SecretId=config.secret,
                VersionStage="AWSCURRENT",
                MoveToVersionId=version,
                **({"RemoveFromVersionId": current} if current else {}),
            )
        await initialize(aws, region, config.consumer_function, version)
        await seed(
            database,
            config,
            created["provider_id"],
            reference,
            values["AccessRoleArn"],
            external,
            "bootstrap_demo",
        )
    verified = await invoke(
        aws,
        region,
        values["WorkerArn"],
        {"operation": "verify-connection", "organisation_id": organisation},
    )
    if verified != {"verified": True}:
        raise RuntimeError("demo resource permissions did not verify")
    await activate(database, organisation)
    result = await invoke(aws, region, config.consumer_function + ":live", {})
    if result.get("ok") is not True:
        raise RuntimeError("demo workload could not use its initial credential")
    print(f"Kinu API demo ready for workspace {organisation}")


if __name__ == "__main__":
    asyncio.run(main())
