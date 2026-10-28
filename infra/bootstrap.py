import argparse
import asyncio
import json
import os
from pathlib import Path

from connectors.aws import AwsClient
from core.storage.dataapi import DataApi
from core.storage.database import Database

from infra.stack import outputs as stack_outputs


async def application(aws: AwsClient, region: str) -> None:
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
    print("Kinu schema and application database role are ready")


async def native(
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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--native", action="store_true", help="Initialize only Agent-Native storage"
    )
    options = parser.parse_args()
    aws, region = AwsClient(), os.environ.get("AWS_REGION", "eu-west-2")
    if options.native:
        await native(
            aws,
            region,
            await stack_outputs(aws, region, "kinu-store"),
            await stack_outputs(aws, region, "kinu-native"),
        )
    else:
        await application(aws, region)


if __name__ == "__main__":
    asyncio.run(main())
