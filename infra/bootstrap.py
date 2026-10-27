import asyncio
import json
import os
from pathlib import Path

from boto3.session import Session
from connectors.aws import AwsClient
from core.storage.dataapi import DataApi
from core.storage.database import Database


async def main() -> None:
    region = os.environ.get("AWS_REGION", "eu-west-2")
    session = Session(region_name=region)
    aws = AwsClient(session)
    stack = await aws.call("cloudformation", "describe_stacks", region, StackName="kinu-store")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Stacks"][0]["Outputs"]}
    secret = await aws.call(
        "secretsmanager", "get_secret_value", region, SecretId=outputs["AppSecretArn"]
    )
    password = json.loads(secret["SecretString"])["password"]
    database = Database(
        data_api=DataApi(
            aws, region, outputs["ClusterArn"], outputs["AdminSecretArn"], outputs["Database"]
        )
    )
    await database.migrate()
    async with database.connection() as connection, connection.transaction():
        await connection.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kinu_app') "
            "THEN CREATE ROLE kinu_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE; "
            "END IF; END $$"
        )
        await connection.execute("SELECT set_config('kinu.new_password', %s, true)", (password,))
        await connection.execute(
            "DO $$ BEGIN EXECUTE format('ALTER ROLE kinu_app PASSWORD %L', "
            "current_setting('kinu.new_password')); END $$"
        )
        await connection.execute("GRANT USAGE ON SCHEMA kinu TO kinu_app")
        await connection.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA kinu TO kinu_app"
        )
        await connection.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA kinu "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kinu_app"
        )
    password = ""
    app = Database(
        data_api=DataApi(
            aws, region, outputs["ClusterArn"], outputs["AppSecretArn"], outputs["Database"]
        )
    )
    async with app.connection() as connection:
        row = await (await connection.execute("SELECT current_user AS username")).fetchone()
        assert row and row["username"] == "kinu_app"
    target = Path(os.environ.get("KINU_DEPLOY_OUTPUT", "/private/tmp/kinu-aws/store.json"))
    target.write_text(json.dumps(outputs))
    target.chmod(0o600)
    print("Kinu schema and application database role are ready")


if __name__ == "__main__":
    asyncio.run(main())
