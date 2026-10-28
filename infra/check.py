import asyncio
import os
import subprocess
import sys

from connectors.aws import AwsClient

from infra.stack import outputs


async def main() -> None:
    store = await outputs(AwsClient(), os.environ.get("AWS_REGION", "eu-west-2"), "kinu-store")
    environment = os.environ | {
        "KINU_TEST_DATA_API_CLUSTER_ARN": store["ClusterArn"],
        "KINU_TEST_DATA_API_SECRET_ARN": store["AppSecretArn"],
    }
    paths = sys.argv[1:] or ["server/tests/storage"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q"], env=environment, check=False
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    asyncio.run(main())
