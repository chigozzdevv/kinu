import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    outputs = json.loads(Path("/private/tmp/kinu-aws/store.json").read_text())
    environment = os.environ | {
        "KINU_TEST_DATA_API_CLUSTER_ARN": outputs["ClusterArn"],
        "KINU_TEST_DATA_API_SECRET_ARN": outputs["AppSecretArn"],
    }
    paths = sys.argv[1:] or ["server/tests/storage"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q"], env=environment, check=False
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
