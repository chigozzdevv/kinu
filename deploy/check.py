import json
import subprocess
import urllib.error
import urllib.request
import uuid


def compose(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Compose check failed for {arguments[0]}; inspect service logs")
    return result.stdout


def status(origin: str, path: str, expected: int) -> None:
    try:
        with urllib.request.urlopen(origin + path, timeout=10) as response:
            actual = response.status
    except urllib.error.HTTPError as error:
        actual = error.code
    if actual != expected:
        raise RuntimeError(f"Unexpected status for {path}: {actual}")


def check() -> None:
    configuration = json.loads(compose("config", "--format", "json"))
    origin = configuration["services"]["api"]["environment"]["KINU_PUBLIC_URL"]
    for path, code in (
        ("/", 200),
        ("/health/ready", 200),
        ("/_internal/assessment", 404),
        ("/_agent-native/auth/session", 401),
    ):
        status(origin, path, code)
    for name, service in configuration["services"].items():
        if service.get("ports") and name != "web":
            raise RuntimeError(f"Unexpected published port on {name}")
    compose(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        """
import asyncio, os
from pathlib import Path
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure
async def check():
    client = AsyncMongoClient(os.environ['KINU_MONGO_URL'])
    try:
        await client.kinu_private.secrets.find_one({})
        raise AssertionError('API can read private storage')
    except OperationFailure as error:
        assert error.code == 13
    assert not Path('/run/secrets/vault').exists()
    assert not Path('/run/secrets/native').exists()
    await client.close()
asyncio.run(check())
""",
    )
    compose(
        "exec",
        "-T",
        "agent",
        "node",
        "--input-type=module",
        "-e",
        """
import { existsSync } from 'node:fs';
for (const name of ['vault', 'auth', 'executor-token', 'setup']) {
  if (existsSync('/run/secrets/' + name)) throw new Error('Private secret mounted in agent');
}
""",
    )
    compose(
        "exec",
        "-T",
        "mongo",
        "mongosh",
        "--quiet",
        "--eval",
        """
const password = process.env.KINU_MONGO_NATIVE_PASSWORD;
const client = new Mongo('mongodb://native:' + password + '@mongo:27017/?authSource=admin');
for (const name of ['kinu', 'kinu_private']) {
  let denied = false;
  try { client.getDB(name).getCollection('inventory').findOne({}); }
  catch (error) { if (error.code !== 13) throw error; denied = true; }
  if (!denied) throw new Error('Agent can access private database');
}
""",
    )
    identity = "check_" + uuid.uuid4().hex
    script = """
import asyncio
from executor.config import ExecutorSettings
from executor.runtime import runtime
async def check():
    async with runtime(ExecutorSettings()) as service:
        IDENTITY
        ACTION
asyncio.run(check())
""".replace("IDENTITY", "identity = " + repr(identity))
    try:
        compose(
            "exec",
            "-T",
            "executor",
            "python",
            "-c",
            script.replace(
                "ACTION",
                "await service.vault.put('org_deploy_check', identity, {'token': identity})",
            ),
        )
        compose("restart", "executor")
        compose(
            "exec",
            "-T",
            "executor",
            "python",
            "-c",
            script.replace(
                "ACTION",
                "saved = await service.vault.get('org_deploy_check', identity); "
                "assert saved['token'] == identity",
            ),
        )
    finally:
        compose(
            "exec",
            "-T",
            "executor",
            "python",
            "-c",
            script.replace(
                "ACTION",
                "await service.vault.database.collection('secrets').delete_one("
                "{'organisation_id': 'org_deploy_check', 'id': identity})",
            ),
        )
    print(
        "Docker checks passed: routing, database permissions, secret mounts and vault persistence."
    )


if __name__ == "__main__":
    check()
